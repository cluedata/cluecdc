"""Real session/RBAC/redaction checks and optional channel-free CDC incident lifecycle."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from cdc_reliability_support import api_for, wait_value
from observability_fixture import attach


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--alerts", action="store_true")
    args = parser.parse_args()
    stack = attach(args.fixture)
    admin = api_for(stack)
    base = stack.url("cluecdc-api", 8000) + "/api/v1"
    pipeline = next(
        p
        for p in admin.request("/pipelines")
        if p["name"] == f"cdc_{stack.run_id}_po_po"
    )
    paths = [
        "/observability/summary",
        f"/pipelines/{pipeline['id']}/observability",
        f"/pipelines/{pipeline['id']}/observability/history",
        f"/pipelines/{pipeline['id']}/observability/capabilities",
    ]
    statuses = {}
    for path in paths:
        assert httpx.get(base + path).status_code == 401
    for role in ["VIEWER", "OPS", "ADMIN"]:
        email = f"obs-{role.lower()}-{uuid4().hex[:8]}@example.test"
        invite = admin.create("/users/invite", {"email": email, "role": role})
        token = invite["invite_url"].rsplit("/", 1)[-1]
        password = "Owned-observability-only-123!"
        with httpx.Client(base_url=base, timeout=10) as client:
            assert (
                client.post(
                    f"/invites/{token}/accept",
                    json={"password": password, "confirm_password": password},
                ).status_code
                == 200
            )
            assert (
                client.post(
                    "/auth/login", json={"email": email, "password": password}
                ).status_code
                == 200
            )
            for path in paths:
                response = client.get(path)
                assert response.status_code == 200
                assert all(
                    secret not in response.text
                    for secret in [
                        password,
                        *(
                            v
                            for k, v in stack.defaults.items()
                            if "PASSWORD" in k and len(v) >= 8
                        ),
                    ]
                )
                assert (
                    "ciphertext" not in response.text
                    and "database.password" not in response.text
                )
            response = client.post(
                "/alert-rules",
                json={
                    "name": "owned-role-probe-" + uuid4().hex,
                    "event_types": ["CDC_CAPTURE_LAG_HIGH"],
                },
            )
            assert response.status_code == (201 if role == "ADMIN" else 403)
            statuses[role] = {
                "reads": 200,
                "configure_alert_rule": response.status_code,
            }
    (args.fixture.parent / "rbac.json").write_text(
        json.dumps(
            {"status": "PASS", "anonymous": 401, "roles": statuses, "redaction": True},
            indent=2,
        )
    )
    print(
        "Real Viewer/Ops/Admin sessions, authenticated cached reads and redaction passed",
        flush=True,
    )
    if not args.alerts:
        return
    path = f"/pipelines/{pipeline['id']}/observability"
    measured = admin.request(path)
    assert (
        measured["capture_lag"]["status"] == "AVAILABLE"
        and measured["capture_lag"]["value"] > 1
    )
    rule = admin.create(
        "/alert-rules",
        {
            "name": "Owned measured CDC lifecycle " + uuid4().hex,
            "event_types": ["CDC_CAPTURE_LAG_HIGH"],
            "pipeline_filters": [pipeline["id"]],
            "config_json": {"observability": {"capture_lag_ms": 1}},
        },
    )

    def incidents():
        return [
            a
            for a in admin.request(
                f"/alerts?eventType=CDC_CAPTURE_LAG_HIGH&pipelineId={pipeline['id']}"
            )["items"]
            if a["details"].get("observability_rule_id") == rule["id"]
        ]

    firing = wait_value(
        incidents,
        lambda rows: len(rows) == 1 and rows[0]["status"] == "firing",
        "three real measured firing samples",
        timeout=140,
    )[0]
    config = json.loads(stack.path.read_text(encoding="utf-8"))
    original = config["services"]["cluecdc-worker"]["environment"][
        "OBSERVABILITY_EXPORTERS"
    ]
    missing = {
        cluster: {worker: "http://kafka-connect:19404/metrics" for worker in workers}
        for cluster, workers in json.loads(original).items()
    }
    config["services"]["cluecdc-worker"]["environment"]["OBSERVABILITY_EXPORTERS"] = (
        json.dumps(missing)
    )
    at = datetime.now(UTC).isoformat()
    stack.path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    try:
        stack.compose("up", "-d", "--wait", "cluecdc-worker", timeout=120)
        loss = wait_value(
            lambda: admin.request(path),
            lambda o: o["collected_at"] > at and o["capture_lag"]["value"] is None,
            "actual exporter loss",
            timeout=100,
        )
        during_loss = incidents()
        assert (
            len(during_loss) == 1
            and during_loss[0]["id"] == firing["id"]
            and during_loss[0]["status"] == "firing"
        )
    finally:
        config["services"]["cluecdc-worker"]["environment"][
            "OBSERVABILITY_EXPORTERS"
        ] = original
        stack.path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        stack.compose("up", "-d", "--wait", "cluecdc-worker", timeout=120)
    admin.request(
        f"/alert-rules/{rule['id']}",
        "PUT",
        {
            "name": rule["name"],
            "event_types": rule["event_types"],
            "pipeline_filters": [pipeline["id"]],
            "config_json": {"observability": {"capture_lag_ms": 999999999}},
        },
    )
    recovered = wait_value(
        incidents,
        lambda rows: len(rows) == 1 and rows[0]["status"] == "resolved",
        "two real valid recovery samples",
        timeout=120,
    )[0]
    detail = admin.request(f"/alerts/{recovered['id']}")
    assert detail["deliveries"] == []
    (args.fixture.parent / "alerts.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "firing": firing,
                "telemetry_loss": loss,
                "incident_held": during_loss[0],
                "recovery": recovered,
                "notifications_without_channels": len(detail["deliveries"]),
                "notification_channel_e2e": "NOT_RUN; requires eligible HTTPS endpoint",
            },
            indent=2,
        )
    )
    print(
        "Real alert confirmation, deduplication, telemetry-loss hold and recovery passed",
        flush=True,
    )


if __name__ == "__main__":
    main()
