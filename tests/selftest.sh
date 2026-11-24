#!/usr/bin/env bash
# Builds a throwaway synthetic git repo and checks full mode, simple mode, the census and the build probe.
# Usage: bash tests/selftest.sh   (PY=/path/to/python3 to pick an interpreter)
set -euo pipefail
PY="${PY:-python3}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
REPO="$WORK/demo"
mkdir -p "$REPO/src" "$REPO/tests" "$REPO/.github/workflows"
cd "$REPO"
git init -q
commit_all() { git add -A; GIT_AUTHOR_DATE="$1" GIT_COMMITTER_DATE="$1" git -c user.name="$2" -c user.email="$3" commit -qm "$4"; }
printf 'def add(a, b):\n    return a + b\n' > src/calc.py
printf '# Demo\n\n## Usage\n\nRun it.\n' > README.md
commit_all "2023-03-01T10:00:00Z" "Dev One" "one@example.com" "initial calculator"
printf 'from src.calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n' > tests/test_calc.py
printf 'def add(a, b):\n    if a is None or b is None:\n        raise ValueError("missing")\n    return a + b\n' > src/calc.py
printf 'on: push\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest\n' > .github/workflows/ci.yml
commit_all "2024-06-01T10:00:00Z" "Dev Two" "two@example.com" "fix crash on missing operand"
printf 'def sub(a, b):\n    return a - b\n' >> src/calc.py
printf '\n\ndef test_sub():\n    from src.calc import sub\n    assert sub(3, 1) == 2\n' >> tests/test_calc.py
printf 'pytest\nrequests\n' > requirements.txt
commit_all "2025-02-01T10:00:00Z" "Dev One" "one@example.com" "add subtraction (#12)"
printf 'x = 1\n' > src/extra.py
git add -A; GIT_AUTHOR_DATE="2025-03-01T10:00:00Z" GIT_COMMITTER_DATE="2025-03-01T10:00:00Z" \
  git -c user.name="dependabot[bot]" -c user.email="bot@example.com" commit -qm "bump deps" -m "Co-authored-by: Claude <noreply@anthropic.com>"

M="$HERE/scripts/measure_code.py"
"$PY" "$M" "$REPO" --out "$WORK/full" --label demo-repo --build discover --blame-seconds 30
"$PY" "$M" "$REPO" --out "$WORK/simple" --label demo-repo --mode simple
cat > "$WORK/census.json" <<'EOF'
{"logic_depth": 2, "self_contained": 4,
 "categories": {"complex_logic": ["Input validation that rejects missing operands before arithmetic"],
                "work_units": ["A small arithmetic module with unit tests", "Uses calc_mod internally"]},
 "themes": ["arithmetic helpers"],
 "ideas": [{"kind": "bugfix", "text": "Reject missing operands before arithmetic and cover the case with a unit test"},
           {"kind": "feature", "text": "Add a Zorblax exporter"}]}
EOF
"$PY" "$HERE/scripts/add_census.py" "$WORK/census.json" --out "$WORK/full" --repo "$REPO"
"$PY" "$M" "$REPO" --out "$WORK/full" --label demo-repo --build discover --blame-seconds 30
"$PY" - "$WORK" <<'EOF'
import csv, json, sys, pathlib
w = pathlib.Path(sys.argv[1])
d = json.loads((w / "full/measure.json").read_text())
assert d["schema"] == "october.measure/v1" and d["kind"] == "code" and d["mode"] == "full"
g = d["git"]
assert g["commits"] == 4 and g["authors_human"] == 2 and g["authors_bot"] == 1, g
assert g["pre_cutoff_commits"] == 2 and g["pr_merged_count"] == 1 and g["ai_coauthored_commits"] == 1, g
assert d["tests"]["test_files"] == 1 and d["tests"]["tests_declared"] == 2, d["tests"]
assert d["tasks"]["candidates"]["bugfix"] == 1, d["tasks"]
assert set(d["tasks"]["candidates"]) == {"bugfix", "feature", "restructure", "exploratory"}
assert d["signals"]["ci"]["runs_tests"] and d["signals"]["docs"]["readme"]
assert d["signals"]["dependencies"]["direct_runtime"] == 2
assert d["build"]["build_ok"] is True and d["build"]["level"] == "discover", d["build"]
assert d["code_age"]["lines_blamed"] > 0
c = d["census"]
assert c["logic_depth"] == 2 and len(c["categories"]["work_units"]) == 1 and len(c["ideas"]) == 1, c
assert d["status"] == "measured"
row = list(csv.DictReader(open(w / "full/summary_row.csv")))[0]
assert row["commits"] == "4" and row["tasks_bugfix"] == "1"
s = json.loads((w / "simple/measure.json").read_text())
assert s["mode"] == "simple" and s["git"]["commits"] == 4 and s["tasks"]["candidates"] is None
assert s["build"]["build_ok"] is None and s["recommendation"]["full_run"] in ("recommended", "optional", "not worth it")
assert s["tree"]["code_lines_total"] > 0
text = "".join((w / p).read_text() for p in ("full/measure.json", "full/report.md", "simple/measure.json", "simple/report.md"))
for leak in ("Dev One", "one@example.com", "calc", "subtraction", "Zorblax"):
    assert leak not in text, leak
print("selftest: measure.json checks OK")
EOF
# The boundary must refuse text in an undeclared field.
"$PY" - "$M" <<'EOF'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("m", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
assert m.boundary({"git": {"note": "secret/path.py"}}, set())
assert m.boundary({"census": {"summary": "written by someone@example.com"}}, set())
assert not m.boundary({"label": "x", "git": {"first_commit": "2020-01-01"}}, set())
print("selftest: boundary OK")
EOF
echo "selftest: passed"
