from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.connect import KafkaConnectClient
from app.adapters.kafka import KafkaExplorer
from app.core.errors import DomainError
from app.models.entities import ConnectCluster, KafkaCluster, now
from app.services.secrets import kafka_credentials


async def probe_kafka(cluster: KafkaCluster, credentials: dict[str, str]) -> dict[str, Any]:
    details = await KafkaExplorer(credentials).health(cluster)
    return {
        "status": "HEALTHY",
        "message": "Kafka connection succeeded",
        "checked_at": now(),
        "details": details,
    }


async def probe_connect(cluster: ConnectCluster) -> dict[str, Any]:
    plugins = await KafkaConnectClient(cluster.base_url).plugins()
    return {
        "status": "HEALTHY",
        "message": "Kafka Connect connection succeeded",
        "checked_at": now(),
        "details": {"plugin_count": len(plugins)},
    }


async def check_kafka(session: AsyncSession, cluster: KafkaCluster) -> dict[str, Any]:
    checked_at = now()
    try:
        result = await probe_kafka(cluster, await kafka_credentials(session, cluster))
        cluster.status = "HEALTHY"
        cluster.status_reason = result["message"]
    except DomainError as exc:
        cluster.status = "UNHEALTHY"
        cluster.status_reason = exc.message
        result = {
            "status": cluster.status,
            "message": exc.message,
            "checked_at": checked_at,
            "details": {"code": exc.code},
        }
    except Exception:
        cluster.status = "UNHEALTHY"
        cluster.status_reason = "Kafka health check failed"
        result = {
            "status": cluster.status,
            "message": cluster.status_reason,
            "checked_at": checked_at,
            "details": {"code": "KAFKA_HEALTH_CHECK_FAILED"},
        }
    cluster.last_health_check_at = checked_at
    return result


async def check_connect(session: AsyncSession, cluster: ConnectCluster) -> dict[str, Any]:
    del session  # Kept in the signature so both persisted checks share one calling convention.
    checked_at = now()
    try:
        result = await probe_connect(cluster)
        cluster.status = "HEALTHY"
        cluster.status_reason = result["message"]
    except DomainError as exc:
        cluster.status = "UNHEALTHY"
        cluster.status_reason = exc.message
        result = {
            "status": cluster.status,
            "message": exc.message,
            "checked_at": checked_at,
            "details": {"code": exc.code},
        }
    except Exception:
        cluster.status = "UNHEALTHY"
        cluster.status_reason = "Kafka Connect health check failed"
        result = {
            "status": cluster.status,
            "message": cluster.status_reason,
            "checked_at": checked_at,
            "details": {"code": "CONNECT_HEALTH_CHECK_FAILED"},
        }
    cluster.last_health_check_at = checked_at
    return result
