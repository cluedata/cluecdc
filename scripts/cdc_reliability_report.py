"""Merge executed CDC evidence and JUnit regression results without promoting skips."""

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from cdc_reliability import compatibility
from cdc_reliability_support import Evidence


def junit_results(path):
    results = []
    for case in ET.parse(path).getroot().iter("testcase"):
        failure = case.find("failure")
        if failure is None:
            failure = case.find("error")
        skipped = case.find("skipped")
        results.append(
            {
                "test_id": case.get("classname", "") + ":" + case.get("name", ""),
                "status": "NOT RUN"
                if skipped is not None
                else "FAIL"
                if failure is not None
                else "PASS",
                "failure_reason": skipped.get("message")
                if skipped is not None
                else failure.get("message")
                if failure is not None
                else None,
                "duration_seconds": float(case.get("time", "0")),
            }
        )
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        type=Path,
        action="append",
        required=True,
        help="Executed summary.json; later inputs supersede matching scopes, preserving previous results separately",
    )
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = Evidence(
        args.output, "merged executed evidence; per-result environment retained"
    )
    selected, superseded = {}, []
    for path in args.run:
        for result in json.loads(path.read_text(encoding="utf-8"))["results"]:
            result = {**result, "evidence_input": str(path)}
            scope = (
                result["source_type"],
                result["destination_type"],
                result["delivery_mode"],
                result["scenario"],
            )
            previous = selected.get(scope)
            if previous is not None:
                if result["status"] == "NOT RUN" and previous["status"] != "NOT RUN":
                    continue
                superseded.append(
                    {
                        **previous,
                        "superseded_by": {
                            "test_id": result["test_id"],
                            "evidence_input": str(path),
                            "status": result["status"],
                        },
                    }
                )
            selected[scope] = result
    evidence.results = list(selected.values())
    evidence.output.mkdir(parents=True, exist_ok=True)
    (evidence.output / "superseded-results.json").write_text(
        json.dumps(superseded, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    # A missing scenario from one suite is not a failure or a pass in another.
    executed = {
        (r["source_type"], r["destination_type"], r["scenario"])
        for r in evidence.results
        if r["status"] != "NOT RUN"
    }
    evidence.results = [
        r
        for r in evidence.results
        if r["status"] != "NOT RUN"
        or (r["source_type"], r["destination_type"], r["scenario"]) not in executed
    ]
    for result in junit_results(args.junit):
        evidence.add(
            result["test_id"],
            None,
            None,
            None,
            "checkpoint-unit"
            if "test_warehouse_runtime" in result["test_id"]
            else "regression",
            "Executed API regression assertion; mocks are not real E2E proof",
            status=result["status"],
            reason=result["failure_reason"],
            duration=result["duration_seconds"],
            tier="regression",
            flush=False,
        )
    compatibility(evidence)
    evidence.write()
    inputs = [*args.run, args.junit]
    (args.output / "inputs.json").write_text(
        json.dumps(
            [
                {"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                for p in inputs
            ],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"results": len(evidence.results), "output": str(args.output)}))


if __name__ == "__main__":
    main()
