"""
Summarises coverage.py's JSON report for review and CI.

Reports separately: application line coverage, application branch coverage,
test-safety-infrastructure coverage, and the combined line+branch figure that
coverage.py enforces (`fail_under` in pyproject.toml), plus missing lines per
file. Standard library only.

Usage: python scripts/coverage_summary.py coverage.json
Writes Markdown to $GITHUB_STEP_SUMMARY when that variable is set.
"""

import json
import os
import sys
from pathlib import PurePosixPath

# Test infrastructure that protects the databases; measured, not exempt.
SAFETY_FILES = {
    "conftest.py",
    "tests/conftest.py",
    "tests/db_safety.py",
    "tests/disposable_database.py",
    "tests/isolation_guard.py",
}


def _group(path: str) -> str:
    if path in SAFETY_FILES:
        return "safety"
    if PurePosixPath(path).parts[0] == "tests":
        return "other-test-support"
    return "application"


def _percent(covered: int, total: int) -> str:
    return "n/a" if total == 0 else f"{100 * covered / total:.1f}%"


def _ranges(lines: list[int]) -> str:
    spans: list[list[int]] = []
    for line in sorted(lines):
        if spans and line == spans[-1][1] + 1:
            spans[-1][1] = line
        else:
            spans.append([line, line])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in spans)


def summarise(report: dict) -> str:
    totals: dict[str, list[int]] = {}
    rows = []
    for raw_path, data in sorted(report["files"].items()):
        path = raw_path.replace("\\", "/")
        s = data["summary"]
        group = _group(path)
        t = totals.setdefault(group, [0, 0, 0, 0])
        t[0] += s["num_statements"]
        t[1] += s["covered_lines"]
        t[2] += s.get("num_branches", 0)
        t[3] += s.get("covered_branches", 0)
        rows.append((s["percent_covered"], path, data.get("missing_lines", [])))

    out = ["## Coverage", "", "| Scope | Lines | Branches |", "|---|---|---|"]
    for group in ("application", "safety", "other-test-support"):
        if group in totals:
            st, sc, bt, bc = totals[group]
            out.append(
                f"| {group} | {_percent(sc, st)} ({sc}/{st}) | {_percent(bc, bt)} ({bc}/{bt}) |"
            )
    combined = report["totals"]["percent_covered"]
    out += ["", f"**Combined line+branch (enforced): {combined:.1f}%**", ""]
    out += ["| File | Combined | Missing lines |", "|---|---|---|"]
    for percent, path, missing in sorted(rows):
        if missing:
            out.append(f"| `{path}` | {percent:.1f}% | {_ranges(missing)} |")
    return "\n".join(out) + "\n"


def main() -> None:
    with open(sys.argv[1], encoding="utf-8") as handle:
        text = summarise(json.load(handle))
    sys.stdout.write(text)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(text)


if __name__ == "__main__":
    main()
