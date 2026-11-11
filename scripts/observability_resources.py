"""Run two owned collector processes and inspect resource/retention API bounds."""

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from cdc_reliability_support import api_for
from observability_fixture import attach


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args()
    stack = attach(args.fixture)
    api = api_for(stack)
    pipeline = next(
        p for p in api.request("/pipelines") if p["name"] == f"cdc_{stack.run_id}_po_po"
    )
    history = api.request(f"/pipelines/{pipeline['id']}/observability/history")
    assert len(history["points"]) <= 500
    base = stack.url("cluecdc-api", 8000)
    with httpx.Client(base_url=base, timeout=10) as client:
        client.post(
            "/api/v1/auth/login",
            json={
                "email": stack.defaults["DEFAULT_ADMIN_EMAIL"],
                "password": stack.defaults["DEFAULT_ADMIN_PASSWORD"],
            },
        ).raise_for_status()
        path = f"/api/v1/pipelines/{pipeline['id']}/observability/history"
        assert client.get(path, params={"limit": 501}).status_code == 422
        assert (
            client.get(
                path,
                params={"start": (datetime.now(UTC) - timedelta(days=8)).isoformat()},
            ).status_code
            == 422
        )
    stack.fault("cluecdc-worker", "restart")
    stack.wait_service("cluecdc-worker")
    stack.compose(
        "up",
        "-d",
        "--wait",
        "--scale",
        "cluecdc-worker=2",
        "cluecdc-worker",
        timeout=180,
    )
    identifiers = stack.compose("ps", "-q", "cluecdc-worker").splitlines()
    assert len(identifiers) == 2
    for info in json.loads(stack.command(["docker", "inspect", *identifiers])):
        assert info["Config"]["Labels"]["io.cluecdc.reliability.owner"] == stack.run_id
    probe = """import asyncio,json
from sqlalchemy import func,select
from app.core.database import Session
from app.models.entities import ObservabilityCollectionState as S,PipelineMetric as M,Job,AuditLog
async def main():
 maximum=0
 for i in range(300):
  async with Session() as db:
   count=await db.scalar(select(func.count()).select_from(S).where(S.lease_expires_at>func.now()))
   maximum=max(maximum,count)
  await asyncio.sleep(.2)
 async with Session() as db:
  rows=(await db.execute(select(M.pipeline_id,M.sample_bucket,func.count()).where(M.sample_bucket.is_not(None)).group_by(M.pipeline_id,M.sample_bucket).having(func.count()>1))).all()
  jobs=await db.scalar(select(func.count()).select_from(Job).where(Job.kind=='observability'))
  audits=await db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action.like('observability.collect%')))
  print(json.dumps({'maximum_active_leases':maximum,'duplicate_buckets':len(rows),'generic_observability_jobs':jobs,'poll_audits':audits}))
asyncio.run(main())
"""
    try:
        measured = json.loads(
            stack.exec("cluecdc-api", ["python", "-c", probe], timeout=90)
        )
        assert (
            measured["maximum_active_leases"] <= 4
            and measured["duplicate_buckets"] == 0
        )
        assert measured["generic_observability_jobs"] == measured["poll_audits"] == 0
        stats = stack.command(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}", *identifiers]
        )
        metrics = httpx.get(base + "/metrics", timeout=10)
        metrics.raise_for_status()
        assert "cluecdc_observability_stale_collections" in metrics.text
        (args.fixture.parent / "collector.prom").write_text(metrics.text)
        (args.fixture.parent / "resources.json").write_text(
            json.dumps(
                {
                    "status": "PASS",
                    "workers": 2,
                    "global_collection_limit": 4,
                    "measured": measured,
                    "docker_stats": [json.loads(line) for line in stats.splitlines()],
                    "history_point_limit": 500,
                    "invalid_ranges_rejected": True,
                },
                indent=2,
            )
        )
        print(
            "Two real collector processes: bounded leases, no duplicate buckets/jobs/audits; API bounds passed"
        )
    finally:
        stack.compose(
            "up",
            "-d",
            "--wait",
            "--scale",
            "cluecdc-worker=1",
            "cluecdc-worker",
            timeout=180,
        )


if __name__ == "__main__":
    main()
