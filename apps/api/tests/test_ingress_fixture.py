from copy import deepcopy

import pytest
import yaml
from scripts.prepare_ingress_fixture import ADMISSION_JOBS, prepare


def admission_job(name, namespace="ingress-nginx"):
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "ttlSecondsAfterFinished": 0,
            "template": {"spec": {"restartPolicy": "OnFailure"}},
        },
    }


def test_retain_both_completed_jobs_without_changing_other_resources():
    """TEST-01: either fast admission Job must remain observable by sequential waits."""
    documents = [admission_job(name) for name in sorted(ADMISSION_JOBS)]
    documents.extend(
        [
            admission_job("unrelated"),
            admission_job("ingress-nginx-admission-patch", namespace="another-namespace"),
            {
                "kind": "ValidatingWebhookConfiguration",
                "metadata": {"name": "ingress-nginx-admission"},
                "webhooks": [{"failurePolicy": "Fail", "clientConfig": {"caBundle": "fixture-ca"}}],
            },
        ]
    )
    expected = deepcopy(documents)
    for document in expected[:2]:
        del document["spec"]["ttlSecondsAfterFinished"]
    result = prepare(yaml.safe_dump_all(documents))
    assert list(yaml.safe_load_all(result)) == expected
    assert prepare(result) == result


@pytest.mark.parametrize("scenario", ["missing", "duplicate", "wrong-namespace"])
def test_reject_unexpected_admission_job_inventory(scenario):
    """TEST-01: a changed upstream fixture must fail closed, not silently weaken waits."""
    documents = [admission_job(name) for name in sorted(ADMISSION_JOBS)]
    if scenario == "missing":
        documents.pop()
    elif scenario == "duplicate":
        documents.append(deepcopy(documents[0]))
    else:
        documents[0]["metadata"]["namespace"] = "unexpected"
    with pytest.raises(ValueError, match="Expected both ingress admission Jobs"):
        prepare(yaml.safe_dump_all(documents))
