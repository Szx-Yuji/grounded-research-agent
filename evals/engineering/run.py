"""Run existing offline engineering checks and emit a redacted scenario report.

Only the explicitly isolated PostgreSQL test database is migrated or written.
JUnit assertion messages, captured output, credentials and provider payloads are
never copied into the persistent JSON report.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "evals/results/engineering-scenarios.json"
SUITES = ("runtime", "durable_integration", "runtime_integration")

# Exact function names make missing/renamed evidence visible as incomplete.
SCENARIOS = {
    "queued_api_reopen": {
        "description": "An enqueued intent survives reopening the API without dispatching a provider.",
        "required": [
            "test_api_enqueues_atomically_without_execution_and_survives_api_restart"
        ],
    },
    "two_worker_claims": {
        "description": "A competing claim skips a locked job and claims another queued job.",
        "required": ["test_skip_locked_claim_does_not_wait_for_another_worker"],
    },
    "idempotent_intents": {
        "description": "Identical retries reuse one intent; conflicting payloads are rejected, including concurrent retries.",
        "required": [
            "test_idempotent_intent_replay_and_payload_mismatch",
            "test_concurrent_idempotency_creates_one_intent_and_message",
        ],
    },
    "shared_delegate_budget": {
        "description": "Nested delegates and concurrent search threads share atomic call/output-token reservations.",
        "required": [
            "test_nested_calls_share_atomic_budget_and_output_reservations",
            "test_parallel_searches_cannot_overrun_shared_budget",
        ],
    },
    "abrupt_worker_exit_unknown_no_replay": {
        "description": "A real worker child exits via os._exit(17) after claim; an expired attempt is interrupted and is not reclaimed. A persisted in-flight call is exposed as unknown.",
        "required": [
            "test_process_crash_after_claim_is_interrupted_without_reexecution",
            "test_stale_lease_blocks_provider_before_dispatch_and_started_becomes_unknown",
        ],
    },
    "independent_worker_process": {
        "description": "A separate Python process claims a real PG intent, executes a deterministic engine and persists its result.",
        "required": ["test_real_worker_subprocess_executes_queued_intent"],
    },
    "resource_permissions": {
        "description": "Real JWT/API ownership rejects another user's execution and knowledge base before embedding dispatch.",
        "required": [
            "test_real_gateway_ledger_api_ownership_and_request_correlation",
            "test_readonly_integration_rejects_cross_tenant_before_embedding",
        ],
    },
}

NETWORK_GUARD = """
import socket
_getaddrinfo = socket.getaddrinfo
_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
def _check(address):
    if isinstance(address, tuple):
        if address[0] not in ("127.0.0.1", "::1", "localhost") or address[1] != 55439:
            raise OSError("External network disabled for offline engineering scenarios")
    elif not isinstance(address, str):
        raise OSError("Unsupported address in offline engineering scenarios")
def _local_getaddrinfo(host, port, *args, **kwargs):
    _check((host, int(port)))
    return _getaddrinfo(host, port, *args, **kwargs)
def _local_connect(self, address):
    _check(address)
    return _connect(self, address)
def _local_connect_ex(self, address):
    _check(address)
    return _connect_ex(self, address)
socket.getaddrinfo = _local_getaddrinfo
socket.socket.connect = _local_connect
socket.socket.connect_ex = _local_connect_ex
"""


def parse_junit(path: Path, suite: str) -> list[dict]:
    """Keep outcome metadata only; never persist failure messages or output."""
    result = []
    for case in ElementTree.parse(path).getroot().iter("testcase"):
        tags = {child.tag for child in case}
        status = (
            "error"
            if "error" in tags
            else "failed"
            if "failure" in tags
            else "skipped"
            if "skipped" in tags
            else "passed"
        )
        result.append(
            {
                "suite": suite,
                "name": case.get("name", "")[:300],
                "class": case.get("classname", "")[:300],
                "status": status,
                "seconds": float(case.get("time", "0")),
            }
        )
    return result


def scenario_results(cases: list[dict]) -> list[dict]:
    result = []
    by_name = {case["name"]: case for case in cases}
    for name, definition in SCENARIOS.items():
        required = definition["required"]
        missing = [name for name in required if name not in by_name]
        evidence = [by_name[name] for name in required if name in by_name]
        status = (
            "incomplete"
            if missing
            else "passed"
            if all(case["status"] == "passed" for case in evidence)
            else "failed"
        )
        result.append(
            {
                "id": name,
                "description": definition["description"],
                "status": status,
                "required_tests": required,
                "missing_tests": missing,
                "evidence": evidence,
            }
        )
    return result


def test_environment(guard_dir: Path) -> dict[str, str]:
    """Use local DB credentials without forwarding provider credentials."""
    raw = os.environ.get("DATABASE_URL") or dotenv_values(ROOT / ".env").get(
        "DATABASE_URL"
    )
    if not raw:
        raise ValueError("database_not_configured")
    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {"postgresql", "postgresql+psycopg"}
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.port != 55439
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("database_must_use_local_port_55439")
    target = urlunsplit(parsed._replace(path="/grounded_research_test"))
    env = os.environ.copy()
    for key in list(env):
        if any(
            word in key for word in ("API_KEY", "ACCESS_TOKEN", "API_TOKEN")
        ) or key.upper().endswith("_PROXY"):
            env.pop(key)
    env.update(
        {
            "DATABASE_URL": target,
            "SECRET_KEY": "offline-engineering-test-only-secret-00000000000000",
            "PROJECT_NAME": "Offline engineering scenarios",
            "FIRST_SUPERUSER": "offline-engineering@example.com",
            "FIRST_SUPERUSER_PASSWORD": "offline-fixture-only-password",
            "DEEPSEEK_API_KEY": "",
            "ZHIPUAI_API_KEY": "",
            "TAVILY_API_KEY": "",
            "SENTRY_DSN": "",
            "REDIS_URL": "",
            "DOCUMENT_STORAGE_DIR": str(guard_dir / "documents"),
            "CHROMA_PERSIST_DIRECTORY": str(guard_dir / "chroma"),
            "PYTHONPATH": str(guard_dir) + os.pathsep + str(ROOT / "backend"),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "ANONYMIZED_TELEMETRY": "False",
            "OTEL_SDK_DISABLED": "true",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
    )
    return env


def command_run(command: list[str], env: dict[str, str], timeout: int) -> int:
    try:
        result = subprocess.run(
            command,
            cwd=ROOT / "backend",
            env=env,
            timeout=timeout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode
    except subprocess.TimeoutExpired:
        return 124


def main() -> int:
    started = time.monotonic()
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "offline_engineering",
        "database": "loopback:55439/grounded_research_test",
        "provider_mode": "deterministic substitutes; external Python sockets blocked",
        "crash_injection": "real child process os._exit(17), not an actual SIGKILL signal",
        "limitations": [
            "No paid provider, answer-quality, deployment-capacity or exactly-once billing validation.",
            "Queued durability and unknown-outcome fencing are tested; full graph checkpoint resume is not implemented.",
            "All existing cases are retained; scenario groups are an index, not a production success-rate estimate.",
        ],
        "setup_status": "not_started",
        "suites": [],
        "cases": [],
    }
    with tempfile.TemporaryDirectory(prefix="grounded-engineering-") as temporary:
        work = Path(temporary)
        (work / "sitecustomize.py").write_text(NETWORK_GUARD)
        try:
            env = test_environment(work)
        except (ValueError, OSError):
            report["setup_status"] = "invalid_local_test_configuration"
        else:
            code = command_run(
                [sys.executable, "-m", "alembic", "upgrade", "head"], env, 60
            )
            report["setup_status"] = "passed" if code == 0 else "migration_failed"
            if code == 0:
                for suite in SUITES:
                    junit = work / f"{suite}.xml"
                    command = [
                        sys.executable,
                        "-m",
                        "pytest",
                        f"--confcutdir=tests/{suite}",
                        f"tests/{suite}",
                        "-q",
                        f"--junitxml={junit}",
                        "-o",
                        "junit_logging=no",
                        "-o",
                        "junit_log_passing_tests=false",
                    ]
                    code = command_run(command, env, 120)
                    try:
                        cases = parse_junit(junit, suite) if junit.exists() else []
                    except (OSError, ValueError, ElementTree.ParseError):
                        cases = []
                    report["cases"].extend(cases)
                    report["suites"].append(
                        {
                            "name": suite,
                            "exit_code": code,
                            "case_count": len(cases),
                            "status": "passed" if code == 0 and cases else "failed",
                        }
                    )
    report["scenarios"] = scenario_results(report["cases"])
    report["counts"] = {
        status: sum(case["status"] == status for case in report["cases"])
        for status in ("passed", "failed", "error", "skipped")
    }
    report["status"] = (
        "passed"
        if (
            report["setup_status"] == "passed"
            and len(report["suites"]) == len(SUITES)
            and all(suite["status"] == "passed" for suite in report["suites"])
            and all(scenario["status"] == "passed" for scenario in report["scenarios"])
        )
        else "failed"
    )
    report["seconds"] = round(time.monotonic() - started, 3)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "setup_status": report["setup_status"],
                "counts": report["counts"],
                "report": str(OUTPUT.relative_to(ROOT)),
                "scenarios": {row["id"]: row["status"] for row in report["scenarios"]},
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
