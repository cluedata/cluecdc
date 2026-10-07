from datetime import timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.providers import provider_for
from app.alerts.security import masked_config, validate_channel_config
from app.core.auth import Principal, require
from app.core.database import session_dependency
from app.core.errors import DomainError
from app.models.entities import (
    Alert,
    AlertRule,
    AlertRuleChannel,
    NotificationChannel,
    NotificationDelivery,
    SecretReference,
    now,
)
from app.repositories.metadata import audit, get, serialize
from app.services.secrets import secret_provider

router = APIRouter(prefix="/api/v1")
DB = Annotated[AsyncSession, Depends(session_dependency)]


class ChannelInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: Literal["slack", "telegram", "webhook"]
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class ChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class RuleInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    enabled: bool = True
    severity: Literal["info", "warning", "critical"] | None = None
    event_types: list[str] = Field(default_factory=list, max_length=100)
    source_filters: list[str] = Field(default_factory=list, max_length=100)
    pipeline_filters: list[str] = Field(default_factory=list, max_length=100)
    connector_filters: list[str] = Field(default_factory=list, max_length=100)
    channel_ids: list[UUID] = Field(default_factory=list, max_length=50)
    cooldown_seconds: int = Field(default=900, ge=0, le=604800)
    notification_policy: Literal[
        "notify_every_occurrence", "notify_after_cooldown", "notify_first_occurrence_only"
    ] = "notify_after_cooldown"
    send_recovery: bool = True


class SilenceInput(BaseModel):
    duration_seconds: int | None = Field(default=1800, ge=60, le=2592000)


def channel_view(channel: NotificationChannel) -> dict:
    return {
        **serialize(channel),
        "config": masked_config(channel.type),
    }


async def rule_view(db: AsyncSession, rule: AlertRule) -> dict:
    channel_ids = (
        await db.scalars(
            select(AlertRuleChannel.channel_id).where(AlertRuleChannel.alert_rule_id == rule.id)
        )
    ).all()
    return {**serialize(rule), "channel_ids": channel_ids}


@router.get("/alerts/summary")
async def alert_summary(db: DB, user: Annotated[Principal, Depends(require("pipelines.read"))]):
    active = Alert.status.in_(["firing", "acknowledged", "silenced"])
    count = await db.scalar(select(func.count()).select_from(Alert).where(active))
    recent = (
        await db.scalars(select(Alert).where(active).order_by(Alert.last_seen_at.desc()).limit(5))
    ).all()
    return {"active_count": count or 0, "recent": [serialize(a) for a in recent]}


@router.get("/alerts")
async def alerts(
    db: DB,
    user: Annotated[Principal, Depends(require("pipelines.read"))],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    status: str | None = None,
    severity: str | None = None,
    event_type: str | None = Query(default=None, alias="eventType"),
    pipeline_id: UUID | None = Query(default=None, alias="pipelineId"),
    source_id: str | None = Query(default=None, alias="sourceId"),
):
    filters: list[Any] = []
    if status == "active":
        filters.append(Alert.status.in_(["firing", "acknowledged", "silenced"]))
    elif status:
        filters.append(Alert.status == status)
    if severity:
        filters.append(Alert.severity == severity)
    if event_type:
        filters.append(Alert.event_type == event_type)
    if pipeline_id:
        filters.append(Alert.pipeline_id == pipeline_id)
    if source_id:
        filters.append(Alert.source_id == source_id)
    total = await db.scalar(select(func.count()).select_from(Alert).where(*filters))
    items = (
        await db.scalars(
            select(Alert)
            .where(*filters)
            .order_by(Alert.last_seen_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {
        "items": [serialize(a) for a in items],
        "page": page,
        "pageSize": page_size,
        "total": total or 0,
    }


@router.get("/alerts/{identifier}")
async def alert_detail(
    identifier: UUID,
    db: DB,
    user: Annotated[Principal, Depends(require("pipelines.read"))],
):
    alert = await get(db, Alert, identifier)
    rows = (
        await db.execute(
            select(NotificationDelivery, NotificationChannel)
            .join(NotificationChannel, NotificationChannel.id == NotificationDelivery.channel_id)
            .where(NotificationDelivery.alert_id == alert.id)
            .order_by(NotificationDelivery.created_at)
        )
    ).all()
    return {
        **serialize(alert),
        "deliveries": [
            {**serialize(delivery), "channel_name": channel.name, "channel_type": channel.type}
            for delivery, channel in rows
        ],
    }


@router.post("/alerts/{identifier}/acknowledge")
async def acknowledge_alert(
    identifier: UUID,
    db: DB,
    user: Annotated[Principal, Depends(require("pipelines.operate"))],
):
    alert = await get(db, Alert, identifier)
    if alert.status == "resolved":
        raise DomainError("ALERT_RESOLVED", "Resolved alerts cannot be acknowledged", 409)
    before = serialize(alert)
    alert.status = "acknowledged"
    alert.acknowledged_at = now()
    alert.acknowledged_by = user.actor
    audit(db, user.actor, "alert.acknowledged", alert, before)
    await db.commit()
    return serialize(alert)


@router.post("/alerts/{identifier}/silence")
async def silence_alert(
    identifier: UUID,
    data: SilenceInput,
    db: DB,
    user: Annotated[Principal, Depends(require("pipelines.operate"))],
):
    alert = await get(db, Alert, identifier)
    if alert.status == "resolved":
        raise DomainError("ALERT_RESOLVED", "Resolved alerts cannot be silenced", 409)
    before = serialize(alert)
    alert.status = "silenced"
    alert.silenced_until = (
        now() + timedelta(seconds=data.duration_seconds) if data.duration_seconds else None
    )
    alert.silenced_by = user.actor
    audit(db, user.actor, "alert.silenced", alert, before)
    await db.commit()
    return serialize(alert)


@router.post("/alerts/{identifier}/unsilence")
async def unsilence_alert(
    identifier: UUID,
    db: DB,
    user: Annotated[Principal, Depends(require("pipelines.operate"))],
):
    alert = await get(db, Alert, identifier)
    if alert.status != "silenced":
        raise DomainError("ALERT_NOT_SILENCED", "Only silenced alerts can be unsilenced", 409)
    before = serialize(alert)
    alert.status = "firing"
    alert.silenced_until = None
    alert.silenced_by = None
    audit(db, user.actor, "alert.unsilenced", alert, before)
    await db.commit()
    return serialize(alert)


@router.get("/notification-channels")
async def list_channels(db: DB, user: Annotated[Principal, Depends(require("pipelines.read"))]):
    rows = (await db.scalars(select(NotificationChannel).order_by(NotificationChannel.name))).all()
    return [channel_view(row) for row in rows]


@router.post("/notification-channels", status_code=201)
async def create_channel(
    data: ChannelInput,
    db: DB,
    user: Annotated[Principal, Depends(require("settings.manage"))],
):
    config = await validate_channel_config(data.type, data.config)
    ref = await secret_provider(db).put_secret(config)
    channel = NotificationChannel(
        name=data.name, type=data.type, enabled=data.enabled, config_encrypted=ref
    )
    db.add(channel)
    await db.flush()
    audit(db, user.actor, "notification_channel.created", channel)
    await db.commit()
    return channel_view(channel)


@router.get("/notification-channels/{identifier}")
async def get_channel(
    identifier: UUID,
    db: DB,
    user: Annotated[Principal, Depends(require("settings.manage"))],
):
    return channel_view(await get(db, NotificationChannel, identifier))


@router.put("/notification-channels/{identifier}")
async def update_channel(
    identifier: UUID,
    data: ChannelUpdate,
    db: DB,
    user: Annotated[Principal, Depends(require("settings.manage"))],
):
    channel = await get(db, NotificationChannel, identifier)
    before = serialize(channel)
    if data.name is not None:
        channel.name = data.name
    if data.enabled is not None:
        channel.enabled = data.enabled
    supplied = {key: value for key, value in data.config.items() if value not in (None, "")}
    if supplied:
        existing = await secret_provider(db).get_secret(channel.config_encrypted)
        if "headers" in supplied and isinstance(supplied["headers"], dict):
            supplied["headers"] = {**existing.get("headers", {}), **supplied["headers"]}
        merged = {**existing, **supplied}
        validated = await validate_channel_config(channel.type, merged)
        old_ref = channel.config_encrypted
        channel.config_encrypted = await secret_provider(db).put_secret(validated)
        await db.flush()
        old_secret = await db.get(SecretReference, old_ref)
        if old_secret:
            await db.delete(old_secret)
    audit(db, user.actor, "notification_channel.updated", channel, before)
    await db.commit()
    return channel_view(channel)


@router.delete("/notification-channels/{identifier}", status_code=204)
async def delete_channel(
    identifier: UUID,
    db: DB,
    user: Annotated[Principal, Depends(require("settings.manage"))],
):
    channel = await get(db, NotificationChannel, identifier)
    secret = await db.get(SecretReference, channel.config_encrypted)
    audit(db, user.actor, "notification_channel.deleted", channel)
    await db.delete(channel)
    await db.flush()
    if secret:
        await db.delete(secret)
    await db.commit()


@router.post("/notification-channels/{identifier}/test")
async def test_channel(
    identifier: UUID,
    db: DB,
    user: Annotated[Principal, Depends(require("settings.manage"))],
):
    channel = await get(db, NotificationChannel, identifier)
    config = await secret_provider(db).get_secret(channel.config_encrypted)
    result = await provider_for(channel.type).test(config)
    if not result.success:
        raise DomainError("CHANNEL_TEST_FAILED", result.error or "Channel test failed", 502)
    return {"success": True, "message": "Test notification sent successfully"}


@router.get("/alert-rules")
async def list_rules(db: DB, user: Annotated[Principal, Depends(require("pipelines.read"))]):
    rules = (await db.scalars(select(AlertRule).order_by(AlertRule.name))).all()
    return [await rule_view(db, rule) for rule in rules]


@router.post("/alert-rules", status_code=201)
async def create_rule(
    data: RuleInput,
    db: DB,
    user: Annotated[Principal, Depends(require("settings.manage"))],
):
    rule = AlertRule(**data.model_dump(exclude={"channel_ids"}))
    db.add(rule)
    await db.flush()
    await _replace_rule_channels(db, rule.id, data.channel_ids)
    audit(db, user.actor, "alert_rule.created", rule)
    await db.commit()
    return await rule_view(db, rule)


@router.get("/alert-rules/{identifier}")
async def get_rule(
    identifier: UUID, db: DB, user: Annotated[Principal, Depends(require("pipelines.read"))]
):
    return await rule_view(db, await get(db, AlertRule, identifier))


@router.put("/alert-rules/{identifier}")
async def update_rule(
    identifier: UUID,
    data: RuleInput,
    db: DB,
    user: Annotated[Principal, Depends(require("settings.manage"))],
):
    rule = await get(db, AlertRule, identifier)
    before = serialize(rule)
    for key, value in data.model_dump(exclude={"channel_ids"}).items():
        setattr(rule, key, value)
    await _replace_rule_channels(db, rule.id, data.channel_ids)
    audit(db, user.actor, "alert_rule.updated", rule, before)
    await db.commit()
    return await rule_view(db, rule)


@router.delete("/alert-rules/{identifier}", status_code=204)
async def delete_rule(
    identifier: UUID,
    db: DB,
    user: Annotated[Principal, Depends(require("settings.manage"))],
):
    rule = await get(db, AlertRule, identifier)
    audit(db, user.actor, "alert_rule.deleted", rule)
    await db.delete(rule)
    await db.commit()


async def _replace_rule_channels(db: AsyncSession, rule_id: UUID, channel_ids: list[UUID]):
    await db.execute(delete(AlertRuleChannel).where(AlertRuleChannel.alert_rule_id == rule_id))
    if channel_ids:
        existing = set(
            (
                await db.scalars(
                    select(NotificationChannel.id).where(NotificationChannel.id.in_(channel_ids))
                )
            ).all()
        )
        if existing != set(channel_ids):
            raise DomainError(
                "INVALID_CHANNEL", "One or more notification channels do not exist", 422
            )
        db.add_all(
            [AlertRuleChannel(alert_rule_id=rule_id, channel_id=value) for value in channel_ids]
        )
