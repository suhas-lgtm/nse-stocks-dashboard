"""
Validate the GitHub Actions workflow files before pushing them.

A broken workflow fails in a way that is easy to misread: GitHub starts no
jobs at all, shows the file path instead of the workflow name, and the run
just says "Failure" with nothing to click into. That happened with

    if: ${{ secrets.ADVISORKHOJ_KEY != '' }}

because the `secrets` context is not available in `if` — only `github`, `env`,
`needs`, `vars`, `inputs`, `job`, `runner`, `steps`, `strategy` and `matrix`
are. The fix is to hoist the secret into a job-level `env` and test that.

    python tests/check_workflows.py
"""

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("pyyaml not installed; skipping workflow checks")
    sys.exit(0)

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = ROOT / ".github" / "workflows"

# Contexts GitHub allows inside an `if:` expression.
ALLOWED_IF_CONTEXTS = {
    "github", "needs", "vars", "inputs", "job", "runner",
    "env", "steps", "strategy", "matrix", "always", "success",
    "failure", "cancelled", "hashFiles", "contains", "startsWith",
    "endsWith", "format", "join", "toJSON", "fromJSON",
}
CONTEXT_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\.")


def check_if_expression(where: str, expr) -> list[str]:
    problems = []
    for name in CONTEXT_RE.findall(str(expr)):
        if name not in ALLOWED_IF_CONTEXTS:
            problems.append(
                f"{where}: `if` uses the `{name}` context, which GitHub does "
                f"not provide there. Hoist it into a job-level env and test "
                f"`env.<NAME>` instead.")
    return problems


def main() -> int:
    files = sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(WORKFLOW_DIR.glob("*.yaml"))
    if not files:
        print(f"No workflow files under {WORKFLOW_DIR}")
        return 1

    problems: list[str] = []
    for path in files:
        rel = path.relative_to(ROOT)
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as e:
            problems.append(f"{rel}: does not parse — {type(e).__name__}: {e}")
            continue

        if not isinstance(doc, dict):
            problems.append(f"{rel}: not a mapping at the top level")
            continue
        if not doc.get("name"):
            problems.append(f"{rel}: has no `name`")
        jobs = doc.get("jobs") or {}
        if not jobs:
            problems.append(f"{rel}: defines no jobs")

        step_count = 0
        for job_name, job in jobs.items():
            if "if" in job:
                problems += check_if_expression(f"{rel} job {job_name}", job["if"])
            for step in job.get("steps") or []:
                step_count += 1
                label = step.get("name") or step.get("uses") or "unnamed step"
                if "if" in step:
                    problems += check_if_expression(f"{rel} step '{label}'", step["if"])
        print(f"  {str(rel):<48} {len(jobs)} job(s), {step_count} step(s)")

    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nAll workflows OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
