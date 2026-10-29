import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def policy():
    source = Path(__file__).resolve().parents[3] / "scripts/check-js-audit.py"
    spec = importlib.util.spec_from_file_location("js_audit_policy", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exact_reviewed_advisory_is_only_allowed_exception(policy):
    report = {
        "deepmerge": [
            {
                "url": "https://github.com/advisories/GHSA-gcqx-w48m-w2h4",
                "severity": "low",
            }
        ]
    }
    assert policy.validate(report, 1) == 1
    assert policy.validate({}, 0) == 0
    report["deepmerge"][0]["severity"] = "high"
    with pytest.raises(ValueError):
        policy.validate(report, 1)


@pytest.mark.parametrize(
    "report,status",
    [({}, 1), ({"new-library": []}, 1), ({"error": "network unavailable"}, 1), ({}, 2)],
)
def test_error_or_unknown_advisory_reports_fail_closed(policy, report, status):
    with pytest.raises(ValueError):
        policy.validate(report, status)
