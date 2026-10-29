"""Validate native Bun audit output against one exact, expiring exception."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path


def validate(report: dict, native_exit: int) -> int:
    if not isinstance(report, dict) or native_exit not in {0, 1}:
        raise ValueError("Bun audit did not return a valid advisory report")
    if not report and native_exit == 0:
        return 0
    if not report:
        raise ValueError("Bun audit failed without advisory data")
    count = 0
    for package, findings in report.items():
        if package != "deepmerge" or not isinstance(findings, list) or not findings:
            raise ValueError("Unreviewed JavaScript advisory; inspect the raw audit report")
        for finding in findings:
            if not isinstance(finding, dict) or finding.get("severity") != "low" or finding.get("url") != "https://github.com/advisories/GHSA-gcqx-w48m-w2h4":
                raise ValueError("Unreviewed JavaScript advisory; inspect the raw audit report")
            if datetime.now(UTC).date().isoformat() > "2026-11-29":
                raise ValueError("The deepmerge scope exception expired; review SECURITY.md")
            count += 1
    return count


if __name__ == "__main__":
    try:
        count = validate(json.loads(Path(sys.argv[1]).read_text()), int(sys.argv[2]))
    except (ValueError, OSError, IndexError) as exc:
        raise SystemExit(str(exc)) from None
    sys.stdout.write(f"JavaScript audit policy passed; {count} explicitly reviewed low-severity record(s). Raw report retained.\n")
