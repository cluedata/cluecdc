"""Bounded read-only providers. No CDC payloads, group joins, writes or offset resets."""

import asyncio
import hashlib
import json
import math
import time
from collections import OrderedDict
from datetime import datetime

import httpx
from confluent_kafka import ConsumerGroupTopicPartitions, TopicCollection
from confluent_kafka import TopicPartition as TP
from confluent_kafka.admin import AdminClient, OffsetSpec
from prometheus_client.parser import text_string_to_metric_families

from app.adapters.kafka import admin_options, enum_name
from app.core.config import get_settings
from app.models.entities import KafkaCluster, now
from app.schemas.observability import DeliveryObservation, Metric, PartitionObservation

MAX_PARTITIONS = 2048
EXPORTER_BACKOFF: OrderedDict[str, tuple[float, int]] = OrderedDict()
PROVIDER_BACKOFF: OrderedDict[tuple[str, str], tuple[float, int]] = OrderedDict()


def provider_ready(kind: str, identifier: str) -> bool:
    return PROVIDER_BACKOFF.get((kind, identifier), (0.0, 0))[0] <= time.monotonic()


def provider_result(kind: str, identifier: str, *, success: bool) -> None:
    key = (kind, identifier)
    if success:
        PROVIDER_BACKOFF.pop(key, None)
        return
    failures = PROVIDER_BACKOFF.get(key, (0.0, 0))[1]
    delay = min(300, get_settings().observability_interval_seconds * 2**failures)
    PROVIDER_BACKOFF[key] = (time.monotonic() + delay, min(failures + 1, 4))
    PROVIDER_BACKOFF.move_to_end(key)
    while len(PROVIDER_BACKOFF) > 512:
        PROVIDER_BACKOFF.popitem(last=False)


SINK_NAMES = {
    "active_count": ("records", "sum"),
    "lag_max": ("records", "max"),
    "read_rate": ("records/second", "sum"),
    "send_rate": ("records/second", "sum"),
    "offset_commit_completion_rate": ("commits/second", "sum"),
}


def metric(
    unit: str,
    source: str,
    reason: str,
    value=None,
    *,
    at=None,
    status="UNAVAILABLE",
    confidence="UNKNOWN",
) -> Metric:
    return Metric(
        value=value,
        unit=unit,
        source=source,
        reason_code=reason,
        observed_at=at,
        collected_at=now(),
        status=status,
        confidence=confidence,
    )


def measured(value, unit, source, at) -> Metric:
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return metric(unit, source, "INVALID_MEASUREMENT", status="INCONCLUSIVE")
    return metric(unit, source, "MEASURED", value, at=at, status="AVAILABLE", confidence="DIRECT")


class Exporters:
    def __init__(self):
        self.samples: dict[str, list] = {}
        self.scraped_at: dict[str, datetime] = {}
        self.errors: dict[str, str] = {}

    async def scrape(self, workers: dict[str, str]):
        async def fetch(worker, url):
            token = get_settings().observability_exporter_token
            retry_at, failures = EXPORTER_BACKOFF.get(url, (0.0, 0))
            if retry_at > time.monotonic():
                self.errors[worker] = "EXPORTER_BACKOFF"
                return
            try:
                async with httpx.AsyncClient(
                    timeout=5,
                    follow_redirects=False,
                    trust_env=False,
                    headers={
                        "Cache-Control": "no-cache",
                        **(
                            {"Authorization": "Bearer " + token.get_secret_value()} if token else {}
                        ),
                    },
                ) as client:
                    async with client.stream("GET", url) as response:
                        response.raise_for_status()
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > 2_000_000:
                                return
                samples = [
                    s for f in text_string_to_metric_families(body.decode()) for s in f.samples
                ]
                if not any(s.name == "jmx_scrape_error" and s.value == 0 for s in samples):
                    return
                self.samples[worker] = samples
                self.scraped_at[worker] = now()
            except (httpx.HTTPError, ValueError, UnicodeError):
                return
            finally:
                if worker in self.samples:
                    EXPORTER_BACKOFF.pop(url, None)
                else:
                    self.errors[worker] = "EXPORTER_UNAVAILABLE"
                    interval = get_settings().observability_interval_seconds
                    EXPORTER_BACKOFF[url] = (
                        time.monotonic() + min(300, interval * 2**failures),
                        min(failures + 1, 4),
                    )
                    EXPORTER_BACKOFF.move_to_end(url)
                    while len(EXPORTER_BACKOFF) > 128:
                        EXPORTER_BACKOFF.popitem(last=False)

        async with asyncio.timeout(get_settings().observability_provider_timeout_seconds):
            await asyncio.gather(*(fetch(w, u) for w, u in workers.items()))

    def select(self, worker, name, labels):
        return [
            s
            for s in self.samples.get(worker, [])
            if s.name == name and all(s.labels.get(k) == str(v) for k, v in labels.items())
        ]

    def capture(self, tasks, server, provider, name, unit, context="streaming"):
        # PostgreSQL/MySQL Debezium capture is single task. Refuse ambiguous identity.
        if len(tasks) != 1 or tasks[0].get("state") != "RUNNING":
            return metric(unit, "debezium_jmx", "TASK_COVERAGE_UNVERIFIED")
        worker = tasks[0].get("worker_id")
        if worker not in self.samples:
            return metric(unit, "debezium_jmx", self.errors.get(worker, "WORKER_NOT_CONFIGURED"))
        samples = self.select(
            worker, name, {"server": server, "provider": provider, "context": context}
        )
        if len(samples) != 1:
            return metric(unit, "debezium_jmx", "MISSING_OR_AMBIGUOUS_SERIES")
        sample = samples[0]
        try:
            at = (
                datetime.fromtimestamp(float(sample.timestamp), self.scraped_at[worker].tzinfo)
                if sample.timestamp is not None
                else self.scraped_at[worker]
            )
        except (ValueError, OverflowError, OSError):
            return metric(unit, "debezium_jmx", "INVALID_EXPORTER_TIMESTAMP", status="INCONCLUSIVE")
        if abs((now() - at).total_seconds()) > get_settings().observability_stale_seconds:
            return metric(unit, "debezium_jmx", "EXPORTER_SAMPLE_EXPIRED", status="STALE")
        return measured(sample.value, unit, "debezium_jmx", at)

    def sink(self, tasks, connector):
        result = {}
        for key, (unit, aggregation) in SINK_NAMES.items():
            samples = []
            times = []
            failure_status, failure_reason = "UNAVAILABLE", "TASK_COVERAGE_UNVERIFIED"
            for task in tasks:
                worker = task.get("worker_id")
                found = self.select(
                    worker, "cluecdc_sink_" + key, {"connector": connector, "task": task.get("id")}
                )
                if task.get("state") != "RUNNING" or len(found) != 1:
                    break
                timestamp = getattr(found[0], "timestamp", None)
                try:
                    at = (
                        datetime.fromtimestamp(float(timestamp), self.scraped_at[worker].tzinfo)
                        if timestamp is not None
                        else self.scraped_at[worker]
                    )
                except (ValueError, TypeError, OverflowError, OSError):
                    failure_status, failure_reason = "INCONCLUSIVE", "INVALID_EXPORTER_TIMESTAMP"
                    break
                if abs((now() - at).total_seconds()) > get_settings().observability_stale_seconds:
                    failure_status, failure_reason = "STALE", "EXPORTER_SAMPLE_EXPIRED"
                    break
                samples.append(found[0].value)
                times.append(at)
            if not tasks or len(samples) != len(tasks):
                result[key] = metric(unit, "connect_jmx", failure_reason, status=failure_status)
            elif any(not math.isfinite(v) or v < 0 for v in samples):
                result[key] = metric(
                    unit, "connect_jmx", "INVALID_MEASUREMENT", status="INCONCLUSIVE"
                )
            else:
                value = max(samples) if aggregation == "max" else sum(samples)
                result[key] = measured(value, unit, "connect_jmx", min(times))
        return result


def partition_evidence(topic, partition, low, end, committed, at):
    if low is None or end is None or committed is None or committed < 0:
        backlog = metric("records", "kafka_admin", "OFFSET_MISSING")
    elif not 0 <= low <= committed <= end:
        backlog = metric("records", "kafka_admin", "OFFSET_OUT_OF_RANGE", status="INCONCLUSIVE")
    else:
        backlog = measured(max(0, end - committed), "records", "kafka_admin", at)
    return PartitionObservation(
        topic=topic,
        partition=partition,
        beginning_offset=low,
        end_offset=end,
        committed_offset=committed,
        backlog=backlog,
    )


def delivery_total(delivery: DeliveryObservation):
    delivery.valid_partitions = sum(p.backlog.status == "AVAILABLE" for p in delivery.partitions)
    if (
        delivery.required_partitions > 0
        and delivery.valid_partitions == delivery.required_partitions
        and delivery.group_state == "STABLE"
    ):
        delivery.backlog = measured(
            sum(p.backlog.value or 0 for p in delivery.partitions), "records", "kafka_admin", now()
        )
    else:
        delivery.backlog = metric(
            "records", "kafka_admin", "INCOMPLETE_PARTITION_COVERAGE", status="PARTIAL"
        )


def sample_backlog(
    cluster: KafkaCluster,
    credentials: dict,
    config: dict,
    connector_name: str,
    delivery: DeliveryObservation,
    tasks: list[dict] | None = None,
):
    """Only describe one candidate group; verify active member client IDs and assignment.

    A default name is only a candidate. All active clients must match the actual
    Connect task identity and all required partitions must be assigned and sampled.
    """
    deadline = time.monotonic() + 9
    admin = AdminClient({**admin_options(cluster, credentials), "socket.timeout.ms": 2000})
    group = config.get("consumer.override.group.id") or f"connect-{connector_name}"
    delivery.group_id = group
    description = admin.describe_consumer_groups([group], request_timeout=2)[group].result(2.5)
    delivery.group_state = enum_name(description.state).upper()
    prefix = f"connector-consumer-{connector_name}-"
    members = description.members
    if (
        delivery.group_state != "STABLE"
        or not members
        or any(
            not m.client_id.startswith(prefix) or not m.client_id[len(prefix) :].isdigit()
            for m in members
        )
    ):
        delivery.backlog = metric(
            "records", "kafka_admin", "GROUP_IDENTITY_OR_REBALANCE", status="INCONCLUSIVE"
        )
        return
    delivery.group_provenance = "connect_config_and_verified_runtime_clients"
    if tasks is not None:
        expected = {prefix + str(t["id"]) for t in tasks if t.get("state") in {"RUNNING", "PAUSED"}}
        if not expected or len(members) != len(tasks) or {m.client_id for m in members} != expected:
            delivery.backlog = metric(
                "records", "kafka_admin", "TASK_GROUP_COVERAGE_UNVERIFIED", status="INCONCLUSIVE"
            )
            return
    topics = sorted(set(t.strip() for t in str(config.get("topics", "")).split(",") if t.strip()))
    if not topics or config.get("topics.regex") or len(topics) > 100:
        delivery.backlog = metric("records", "kafka_admin", "TOPIC_COVERAGE_UNVERIFIED")
        return
    required: set[tuple[str, int]] = set()
    # DescribeTopics is targeted and does not auto-create missing topics, unlike
    # the legacy Metadata/list_topics(topic) API on auto-creation-enabled brokers.
    descriptions = admin.describe_topics(TopicCollection(topics), request_timeout=2)
    if set(descriptions) != set(topics):
        delivery.backlog = metric("records", "kafka_admin", "TOPIC_COVERAGE_UNVERIFIED")
        return
    for topic, future in descriptions.items():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            delivery.backlog = metric("records", "kafka_admin", "PROVIDER_BUDGET_EXHAUSTED")
            return
        try:
            entry = future.result(min(2, remaining))
        except Exception:
            delivery.backlog = metric("records", "kafka_admin", "TOPIC_METADATA_UNAVAILABLE")
            return
        required.update((topic, p.id) for p in entry.partitions)
    delivery.required_partitions = len(required)
    if len(required) > MAX_PARTITIONS:
        delivery.backlog = metric("records", "kafka_admin", "PARTITION_LIMIT_EXCEEDED")
        return
    assigned = {(p.topic, p.partition) for m in members for p in m.assignment.topic_partitions}
    offsets = admin.list_consumer_group_offsets(
        [ConsumerGroupTopicPartitions(group, [TP(t, p) for t, p in sorted(required)])],
        request_timeout=2,
    )[group].result(2.5)
    commits = {
        (p.topic, p.partition): p.offset for p in offsets.topic_partitions if p.error is None
    }
    requests = {TP(t, p): OffsetSpec.latest() for t, p in required}
    ends = admin.list_offsets(requests, request_timeout=2)
    lows = admin.list_offsets({p: OffsetSpec.earliest() for p in requests}, request_timeout=2)
    at = now()
    for tp, future in ends.items():
        try:
            end = future.result(max(0, deadline - time.monotonic())).offset
            low = lows[tp].result(max(0, deadline - time.monotonic())).offset
        except Exception:
            end = low = None
        key = (tp.topic, tp.partition)
        committed = commits.get(key) if key in assigned else None
        delivery.partitions.append(partition_evidence(*key, low, end, committed, at))
    after = admin.describe_consumer_groups([group], request_timeout=2)[group].result(2.5)

    def member_identity(m):
        return (
            m.member_id,
            m.client_id,
            sorted((p.topic, p.partition) for p in m.assignment.topic_partitions),
        )

    before_identity = sorted(member_identity(m) for m in members)
    after_identity = sorted(member_identity(m) for m in after.members)
    if enum_name(after.state).upper() != "STABLE" or before_identity != after_identity:
        delivery.group_state = "REBALANCING"
    else:
        delivery.group_generation = hashlib.sha256(json.dumps(before_identity).encode()).hexdigest()
    delivery_total(delivery)
