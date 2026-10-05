import asyncio
import contextlib
import json
import ssl
import time
from typing import Any

from aiokafka import AIOKafkaConsumer, TopicPartition
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import (
    BrokerNotAvailableError,
    ClusterAuthorizationFailedError,
    KafkaConnectionError,
    KafkaTimeoutError,
    MetadataEmptyBrokerList,
    NoBrokersAvailable,
    RequestTimedOutError,
    TopicAuthorizationFailedError,
    UnknownTopicOrPartitionError,
)
from confluent_kafka import ConsumerGroupTopicPartitions
from confluent_kafka import TopicPartition as ConfluentTopicPartition
from confluent_kafka.admin import AdminClient, OffsetSpec

from app.core.errors import DomainError
from app.models.entities import KafkaCluster

PROTECTED_CONNECT_TOPICS = {
    "_cluecdc_connect_configs",
    "_cluecdc_connect_offsets",
    "_cluecdc_connect_status",
    "connect-configs",
    "connect-offsets",
    "connect-status",
    "_connect-configs",
    "_connect-offsets",
    "_connect-status",
}


def protected_topic_reason(name: str, metadata: dict | None = None) -> str | None:
    """Return why a topic must not be deleted through ClueCDC."""
    if name.startswith("__") or bool((metadata or {}).get("is_internal")):
        return "Kafka internal topics cannot be deleted from ClueCDC"
    if name in PROTECTED_CONNECT_TOPICS:
        return "Kafka Connect internal topics cannot be deleted from ClueCDC"
    return None


def options(cluster: KafkaCluster) -> dict:
    opts: dict[str, Any] = {
        "bootstrap_servers": cluster.bootstrap_servers.split(","),
        "security_protocol": cluster.security_protocol,
        "request_timeout_ms": 5000,
    }
    if cluster.security_protocol == "SSL":
        opts["ssl_context"] = ssl.create_default_context()
    return opts


def admin_options(cluster: KafkaCluster) -> dict:
    """Translate a registered cluster into librdkafka configuration."""
    return {
        "bootstrap.servers": cluster.bootstrap_servers,
        "security.protocol": cluster.security_protocol.lower(),
        "socket.timeout.ms": 5000,
    }


def enum_name(value: object, fallback: str = "UNKNOWN") -> str:
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    rendered = str(value)
    return rendered.rsplit(".", 1)[-1] if rendered else fallback


class KafkaExplorer:
    async def consumer_groups(self, cluster: KafkaCluster) -> list[dict]:
        """Return committed offsets and broker lag for topic-consuming groups."""
        try:
            async with asyncio.timeout(20):
                return await asyncio.to_thread(self._consumer_groups_sync, cluster)
        except Exception as exc:
            raise DomainError(
                "KAFKA_CONSUMER_GROUPS_UNAVAILABLE",
                "Kafka consumer offsets and lag could not be retrieved",
                503,
            ) from exc

    def _consumer_groups_sync(self, cluster: KafkaCluster) -> list[dict]:
        """Run librdkafka's blocking admin calls outside the application event loop."""
        admin = AdminClient(admin_options(cluster))
        listed = admin.list_consumer_groups(request_timeout=10).result(timeout=12)
        if listed.errors:
            raise listed.errors[0]
        groups = sorted(listed.valid, key=lambda group: group.group_id)[:500]
        if not groups:
            return []

        group_ids = [group.group_id for group in groups]
        description_futures = admin.describe_consumer_groups(group_ids, request_timeout=10)
        descriptions = {
            group_id: future.result(timeout=12) for group_id, future in description_futures.items()
        }

        committed_by_group: dict[str, dict[tuple[str, int], int]] = {}
        partition_keys: set[tuple[str, int]] = set()
        for group_id in group_ids:
            request = [ConsumerGroupTopicPartitions(group_id)]
            response = admin.list_consumer_group_offsets(request, request_timeout=10)[
                group_id
            ].result(timeout=12)
            committed: dict[tuple[str, int], int] = {}
            for partition in response.topic_partitions:
                if partition.error is None and partition.offset >= 0:
                    key = (partition.topic, partition.partition)
                    committed[key] = partition.offset
                    partition_keys.add(key)
            committed_by_group[group_id] = committed

        end_offsets: dict[tuple[str, int], int] = {}
        if partition_keys:
            requests = {
                ConfluentTopicPartition(topic, partition): OffsetSpec.latest()
                for topic, partition in partition_keys
            }
            end_offsets = {
                (partition.topic, partition.partition): future.result(timeout=12).offset
                for partition, future in admin.list_offsets(requests, request_timeout=10).items()
            }

        result: list[dict[str, Any]] = []
        for group in groups:
            group_id = group.group_id
            description = descriptions[group_id]
            offsets: list[dict[str, Any]] = []
            for (topic, partition), committed_offset in sorted(
                committed_by_group[group_id].items()
            ):
                end_offset = end_offsets.get((topic, partition))
                offsets.append(
                    {
                        "topic": topic,
                        "partition": partition,
                        "committed_offset": committed_offset,
                        "end_offset": end_offset,
                        "lag": (
                            max(0, end_offset - committed_offset)
                            if end_offset is not None
                            else None
                        ),
                    }
                )
            result.append(
                {
                    "group_id": group_id,
                    "state": enum_name(description.state),
                    "protocol_type": "consumer",
                    "protocol": description.partition_assignor or "",
                    "members": len(description.members),
                    "topics": sorted({offset["topic"] for offset in offsets}),
                    "offsets": offsets,
                    "total_lag": sum(
                        offset["lag"] for offset in offsets if offset["lag"] is not None
                    ),
                }
            )
        return result

    async def ensure_topics(self, cluster: KafkaCluster, names: list[str]) -> dict:
        names = sorted(set(names))
        admin = AIOKafkaAdminClient(**options(cluster))
        try:
            async with asyncio.timeout(15):
                await admin.start()
                existing = set(await admin.list_topics())
                missing = [name for name in names if name not in existing]
                created = []
                if missing:
                    # -1 uses broker defaults. aiokafka 0.13 requires an explicit
                    # empty assignment when both defaults are requested.
                    response = await admin.create_topics(
                        [NewTopic(name, -1, -1, replica_assignments={}) for name in missing],
                        timeout_ms=10000,
                    )
                    for name, code, *_ in response.topic_errors:
                        if code not in {0, 36}:  # Another producer may create it first.
                            raise DomainError(
                                "KAFKA_TOPIC_CREATION_FAILED",
                                "Capture topics could not be prepared; check Kafka permissions",
                                503,
                                {"topic": name, "kafka_error_code": code},
                            )
                        if code == 0:
                            created.append(name)
                return {"topics": names, "created": sorted(created)}
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError(
                "KAFKA_TOPIC_CREATION_FAILED",
                "Capture topics could not be prepared; check Kafka availability and permissions",
                503,
            ) from exc
        finally:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await admin.close()

    async def topics(self, cluster: KafkaCluster, name: str | None = None) -> list[dict]:
        admin = AIOKafkaAdminClient(**options(cluster))
        try:
            async with asyncio.timeout(10):
                await admin.start()
                known = sorted(await admin.list_topics())
                if name and name not in known:
                    raise DomainError("TOPIC_NOT_FOUND", "Kafka topic was not found", 404)
                names = [name] if name else known
                # Bound metadata lookup; exclude protected topics from normal lists,
                # but allow direct inspection so the UI can explain the protection.
                names = (
                    [name] if name else [n for n in names if not protected_topic_reason(n)][:500]
                )
                if not names:
                    return []
                descriptions = await admin.describe_topics(names)
                topics = []
                for topic in descriptions:
                    if topic.get("error_code"):
                        if name:
                            raise DomainError("TOPIC_NOT_FOUND", "Kafka topic was not found", 404)
                        continue
                    partitions = topic.get("partitions", [])
                    protection_reason = protected_topic_reason(topic["topic"], topic)
                    if not name and protection_reason:
                        continue
                    topics.append(
                        {
                            "name": topic["topic"],
                            "partitions": len(partitions),
                            "replication_factor": len(partitions[0]["replicas"])
                            if partitions
                            else None,
                            "partition_details": partitions,
                            "message_rate": None,
                            "retention": None,
                            "size": None,
                            "internal": bool(topic.get("is_internal")),
                            "protected": protection_reason is not None,
                            "protection_reason": protection_reason,
                        }
                    )
                return topics
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError(
                "KAFKA_UNAVAILABLE", "Kafka metadata could not be retrieved", 503
            ) from exc
        finally:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await admin.close()

    async def delete_topic(self, cluster: KafkaCluster, name: str) -> dict:
        """Delete one exact topic and normalize broker failures for the API."""
        if not name or len(name) > 249:
            raise DomainError(
                "INVALID_TOPIC_NAME", "Kafka topic names must be between 1 and 249 characters", 422
            )

        admin = AIOKafkaAdminClient(**options(cluster))
        try:
            async with asyncio.timeout(15):
                await admin.start()
                known = set(await admin.list_topics())
                if name not in known:
                    raise DomainError("TOPIC_NOT_FOUND", "Kafka topic was not found", 404)

                descriptions = await admin.describe_topics([name])
                metadata = next(
                    (topic for topic in descriptions if topic.get("topic") == name), None
                )
                if metadata is None or metadata.get("error_code") == 3:
                    raise DomainError("TOPIC_NOT_FOUND", "Kafka topic was not found", 404)
                if metadata.get("error_code") in {29, 31}:
                    raise DomainError(
                        "KAFKA_AUTHORIZATION_FAILED",
                        "Kafka denied permission to inspect or delete this topic",
                        403,
                    )
                if metadata.get("error_code") == 7:
                    raise DomainError(
                        "KAFKA_TIMEOUT", "Kafka timed out while inspecting the topic", 504
                    )
                if metadata.get("error_code"):
                    raise DomainError(
                        "KAFKA_UNAVAILABLE",
                        "Kafka metadata is unavailable; try again shortly",
                        503,
                        {"kafka_error_code": metadata["error_code"]},
                    )
                if reason := protected_topic_reason(name, metadata):
                    raise DomainError(
                        "PROTECTED_INTERNAL_TOPIC",
                        reason,
                        409,
                        {"topic": name},
                    )

                response = await admin.delete_topics([name], timeout_ms=10000)
                errors = getattr(response, "topic_error_codes", [])
                error_code = next(
                    (code for topic, code, *_ in errors if topic == name),
                    None,
                )
                if error_code is None:
                    raise DomainError(
                        "INTERNAL_ERROR",
                        "Kafka returned an incomplete topic deletion response",
                        500,
                    )
                if error_code == 0:
                    return {"deleted": True, "topic": name}
                if error_code == 3:
                    raise DomainError("TOPIC_NOT_FOUND", "Kafka topic was not found", 404)
                if error_code == 73:
                    raise DomainError(
                        "TOPIC_DELETION_DISABLED",
                        "Topic deletion is disabled on the Kafka cluster",
                        409,
                    )
                if error_code in {29, 31}:
                    raise DomainError(
                        "KAFKA_AUTHORIZATION_FAILED",
                        "Kafka denied permission to delete this topic",
                        403,
                    )
                if error_code == 7:
                    raise DomainError(
                        "KAFKA_TIMEOUT", "Kafka timed out while deleting the topic", 504
                    )
                raise DomainError(
                    "INTERNAL_ERROR",
                    "Kafka returned an unexpected topic deletion error",
                    500,
                    {"kafka_error_code": error_code},
                )
        except DomainError:
            raise
        except (TopicAuthorizationFailedError, ClusterAuthorizationFailedError) as exc:
            raise DomainError(
                "KAFKA_AUTHORIZATION_FAILED",
                "Kafka denied permission to delete this topic",
                403,
            ) from exc
        except (TimeoutError, KafkaTimeoutError, RequestTimedOutError) as exc:
            raise DomainError(
                "KAFKA_TIMEOUT", "Kafka timed out while deleting the topic", 504
            ) from exc
        except UnknownTopicOrPartitionError as exc:
            raise DomainError("TOPIC_NOT_FOUND", "Kafka topic was not found", 404) from exc
        except (
            KafkaConnectionError,
            BrokerNotAvailableError,
            MetadataEmptyBrokerList,
            NoBrokersAvailable,
            ConnectionError,
            OSError,
        ) as exc:
            raise DomainError(
                "KAFKA_UNAVAILABLE", "Kafka is unavailable; try again shortly", 503
            ) from exc
        except Exception as exc:
            raise DomainError(
                "INTERNAL_ERROR", "An unexpected error occurred while deleting the topic", 500
            ) from exc
        finally:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await admin.close()

    async def notifications(
        self, cluster: KafkaCluster, topic: str, limit: int = 200
    ) -> list[dict]:
        """Read a bounded recent window of Debezium notification records."""
        consumer = AIOKafkaConsumer(
            **options(cluster),
            group_id=None,
            enable_auto_commit=False,
            auto_offset_reset="latest",
            fetch_max_bytes=1_000_000,
        )
        try:
            async with asyncio.timeout(10):
                await consumer.start()
                metadata = await self.topics(cluster, topic)
                partitions = {
                    partition["partition"] for partition in metadata[0]["partition_details"]
                }
                tps = [TopicPartition(topic, partition) for partition in sorted(partitions)[:16]]
                consumer.assign(tps)
                ends = await consumer.end_offsets(tps)
                beginnings = await consumer.beginning_offsets(tps)
                per_partition = max(1, limit // max(1, len(tps)))
                for tp in tps:
                    consumer.seek(tp, max(beginnings[tp], ends[tp] - per_partition))
                records: list[dict] = []
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and len(records) < limit:
                    batches = await consumer.getmany(
                        timeout_ms=300, max_records=limit - len(records)
                    )
                    if not batches:
                        # A manually assigned consumer can return an empty first
                        # poll while its broker connection is still warming up.
                        # Keep the bounded deadline instead of treating that as
                        # end-of-topic, otherwise short-lived notifications are
                        # intermittently invisible to the operation worker.
                        continue
                    for tp, batch in batches.items():
                        for record in batch:
                            if record.offset < ends[tp]:
                                value = decode(record.value)
                                if isinstance(value, dict):
                                    records.append(value.get("payload", value))
                    positions = [await consumer.position(tp) for tp in tps]
                    if all(position >= ends[tp] for tp, position in zip(tps, positions)):
                        break
                return records[-limit:]
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError(
                "KAFKA_UNAVAILABLE", "Snapshot notifications could not be retrieved", 503
            ) from exc
        finally:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await consumer.stop()


def decode(value: bytes | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (ValueError, UnicodeDecodeError):
        return {"text": value.decode("utf-8", errors="replace")[:65536]}
