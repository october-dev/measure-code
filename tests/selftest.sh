#!/usr/bin/env bash
# Builds throwaway synthetic git repos and checks full mode, simple mode, the census and task mining,
# capacity, branch selection, the build probe, edge cases (empty, non-git, shallow), verification and
# the output boundary, the status rules (a build nobody asked for is info, not partial) and dream mode.
# Usage: bash tests/selftest.sh   (PY=/path/to/python3 to pick an interpreter)
set -euo pipefail
PY="${PY:-python3}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
M="$HERE/scripts/measure_code.py"
C="$HERE/scripts/add_census.py"
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
REPO="$WORK/demo"
mkdir -p "$REPO/src/ops" "$REPO/tests" "$REPO/.github/workflows" "$REPO/svc"
cd "$REPO"
git init -q -b main
commit_as() {  # date, author name, author email, message, [committer name, committer email], [extra -m]
  git add -A
  GIT_AUTHOR_DATE="$1" GIT_COMMITTER_DATE="$1" GIT_COMMITTER_NAME="${5:-$2}" GIT_COMMITTER_EMAIL="${6:-$3}" \
    git -c user.name="$2" -c user.email="$3" commit -qm "$4" ${7:+-m "$7"}
}
printf 'def add(a, b):\n    return a + b\n' > src/calc.py
printf '# Demo\n\n## Usage\n\nRun it.\n' > README.md
commit_as "2023-03-01T10:00:00Z" "Dev One" "one@example.com" "initial calculator"
printf 'def add(a, b):\n    if a is None or b is None:\n        raise ValueError("missing")\n    return float(a) + float(b)\n' > src/calc.py
cat > tests/test_calc.py <<'PYEOF'
from src.calc import add


def test_add():
    assert add(1, 2) == 3


def test_add_missing():
    try:
        add(None, 2)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
PYEOF
printf 'on: push\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest\n' > .github/workflows/ci.yml
commit_as "2024-06-01T10:00:00Z" "Dev Two" "two@example.com" "fix crash on missing operand"
printf '\n\ndef sub(a, b):\n    return a - b\n' >> src/calc.py
printf '\n\ndef test_sub():\n    from src.calc import sub\n    assert sub(3, 1) == 2\n' >> tests/test_calc.py
printf 'pytest\nrequests\n' > requirements.txt
commit_as "2025-02-01T10:00:00Z" "Dev One" "one@example.com" "add subtraction (#12)"
{ echo 'def mul(a, b):'; for i in $(seq 1 12); do echo "    if a == $i:"; echo "        return $i * b"; done; echo '    return a * b'; } > src/ops/mul.py
printf 'from .mul import mul\n' > src/ops/__init__.py
commit_as "2025-02-10T10:00:00Z" "Claude Smith" "claude@gemini.com" "Bug 123 - add multiply" "" "" "Co-authored-by: Claude Smith <claude@gemini.com>"
printf '# Demo\n\n## Usage\n\nRun it now.\n' > README.md
commit_as "2025-02-15T10:00:00Z" "Dev Three" "123+dev3@users.noreply.github.com" "fix typo in readme"
printf '\n\ndef div(a, b):\n    return a / b\n' >> src/calc.py
printf '\n\ndef test_div():\n    from src.calc import div\n    assert div(4, 2) == 2\n' >> tests/test_calc.py
commit_as "2025-02-20T10:00:00Z" "Dev One" "one@example.com" "add division (#14)" "GitHub" "noreply@github.com"
git checkout -q -b xqbranch
printf '\n\ndef power(a, b):\n    return a ** b\n' >> src/calc.py
commit_as "2025-03-01T10:00:00Z" "Dev Two" "two@example.com" "implement power"
git checkout -q main
GIT_AUTHOR_DATE="2025-03-02T10:00:00Z" GIT_COMMITTER_DATE="2025-03-02T10:00:00Z" \
  git -c user.name="Dev Two" -c user.email="two@example.com" merge -q --no-ff xqbranch -m "Merge pull request #15 from dev/xqbranch"
printf 'module example.com/svc\n\ngo 1.20\n' > svc/go.mod
printf 'package main\n\nfunc main() {}\n' > svc/main.go
commit_as "2025-03-05T10:00:00Z" "dependabot[bot]" "bot@example.com" "bump deps" "" "" "Co-authored-by: Claude <noreply@anthropic.com>"
FIX_SHA="$(git log --format=%H --grep='fix crash' -n1)"

"$PY" "$M" "$REPO" --out "$WORK/full" --label demo-repo --build discover --blame-seconds 60 >/dev/null
"$PY" "$M" "$REPO" --out "$WORK/simple" --label demo-repo --mode simple >/dev/null

# Task mining is required: a draft with no ideas and no skip reason is refused.
echo '{"self_contained": 4, "categories": {}}' > "$WORK/no-ideas.json"
if "$PY" "$C" "$WORK/no-ideas.json" --out "$WORK/tmp" --repo "$REPO" 2>/dev/null; then echo "census without task mining was accepted"; exit 1; fi

cat > "$WORK/census.json" <<EOF
{"model": "test-model-1", "reviewer_judgement": 5, "self_contained": 4,
 "categories": {"complex_logic": ["Input validation that rejects missing operands before arithmetic",
                                  "Branch-heavy multiplication with early returns for small operands"],
                "work_units": ["A small arithmetic module with unit tests", "Uses calc_mod internally"],
                "defect_repairs": ["A crash on missing operands fixed by validating inputs first"]},
 "defect_repairs_with_test": 1,
 "themes": ["arithmetic helpers"],
 "ideas": [{"kind": "bug_repair", "text": "Reject missing operands before arithmetic and cover the case with a unit test",
            "grounding": {"commit": "$FIX_SHA"}},
           {"kind": "feature", "text": "Add an expression parser that evaluates nested arithmetic with operator precedence",
            "grounding": {"file": "src/calc.py"}},
           {"kind": "net_new", "text": "Add an expression parser that evaluates nested arithmetic with operator precedence",
            "grounding": {"file": "src/calc.py"}},
           {"kind": "repo_evolution", "text": "Split the arithmetic helpers into a package with one module per operation family",
            "grounding": {"commit": "0000000000000000000000000000000000000000"}},
           {"kind": "long_horizon", "text": "Build a plugin system so new operators register themselves without core changes",
            "grounding": {"file": ".env"}},
           {"kind": "net_new", "text": "Fix a typo in the usage section of the documentation for new readers",
            "grounding": {"file": "README.md"}},
           {"kind": "net_new", "text": "Add a Zorblax exporter that writes every result to a remote ledger service",
            "grounding": {"file": "src/calc.py"}}]}
EOF
"$PY" "$C" "$WORK/census.json" --out "$WORK/full" --repo "$REPO" 2>/dev/null >/dev/null
"$PY" "$M" "$REPO" --out "$WORK/full" --label demo-repo --build discover --blame-seconds 60 >/dev/null
"$PY" "$M" "$REPO" --out "$WORK/fullbuild" --label demo-repo --build full --blame-seconds 0 >/dev/null
# The default build level (detect) with every default lane done is `measured`; the build is info only.
mkdir -p "$WORK/detect" && cp "$WORK/full/census.json" "$WORK/detect/census.json"
"$PY" "$M" "$REPO" --out "$WORK/detect" --label demo-repo --blame-seconds 60 >/dev/null

# Edge cases: empty repository, folder without git, shallow clone.
mkdir "$WORK/empty" && git -C "$WORK/empty" init -q
"$PY" "$M" "$WORK/empty" --out "$WORK/o-empty" >/dev/null
mkdir -p "$WORK/plain/src" && printf 'def f():\n    return 1\n' > "$WORK/plain/src/a.py"
"$PY" "$M" "$WORK/plain" --out "$WORK/o-plain" >/dev/null
"$PY" "$M" "$WORK/plain" --out "$WORK/o-plain-simple" --mode simple >/dev/null
git clone -q --depth 1 "file://$REPO" "$WORK/shallow"
"$PY" "$M" "$WORK/shallow" --out "$WORK/o-shallow" >/dev/null

# Branch selection: the real history lives on a branch that is not checked out.
B="$WORK/branchy"; mkdir -p "$B" && cd "$B" && git init -q -b old
for i in $(seq 1 15); do printf "x = $i\n" > "f$i.py"; commit_as "2022-0$(( (i % 9) + 1 ))-01T10:00:00Z" "Dev One" "one@example.com" "step $i"; done
git checkout -q --orphan main && git rm -rqf . && printf 'y = 1\n' > main.py && commit_as "2025-06-01T10:00:00Z" "Dev One" "one@example.com" "fresh start"
cd "$WORK"
"$PY" "$M" "$B" --out "$WORK/o-ref-auto" --mode simple >/dev/null
"$PY" "$M" "$B" --out "$WORK/o-ref-head" --mode simple --ref head >/dev/null
"$PY" "$M" "$B" --out "$WORK/o-ref-all" --mode simple --all-refs >/dev/null

"$PY" - "$WORK" "$FIX_SHA" <<'EOF'
import csv, json, sys, pathlib
w = pathlib.Path(sys.argv[1])
load = lambda p: json.loads((w / p / "measure.json").read_text())
d = load("full")
assert d["schema"] == "october.measure/v1" and d["kind"] == "code" and d["mode"] == "full" and d["tool_version"] == "0.5.0"
g = d["git"]
assert g["commits"] == 9 and g["authors_human"] == 4 and g["authors_bot"] == 1, g
assert g["pre_cutoff_commits"] == 2 and g["history_complete"] is True, g
assert g["pr_merged_count"] == 2, ("PRs: only GitHub-committed (#N) and merge subjects on first-parent count", g["pr_merged_count"])
assert g["ai_coauthored_commits"] == 1 and g["ai_agent_commits"] == 0, ("a person named Claude is not an AI tool", g)
assert d["tests"]["test_files"] == 1 and d["tests"]["tests_declared"] == 4, d["tests"]
assert d["tests"]["frameworks"] == ["pytest"], ("test tools only", d["tests"]["frameworks"])
tk = d["tasks"]
assert tk["commit_kinds"]["bugfix"] == 1, ("'Bug 123 - add' and 'fix typo' are not fixes", tk["commit_kinds"])
assert tk["candidates"]["bugfix"] == 1 and tk["capacity"]["bugfix_with_regression_test"] == 1, tk
cap = tk["capacity"]
assert cap["mineable_commits"] >= 1 and cap["mineable_by_kind"]["feature"] >= 1 and cap["chore_commits_excluded"] >= 1, cap
assert cap["fix_subjects_not_corroborated"] >= 1, cap
assert set(tk["candidates"]) == {"bugfix", "feature", "restructure", "exploratory"}
assert d["signals"]["ci"]["runs_tests"] and d["signals"]["docs"]["readme"]
assert d["signals"]["dependencies"]["direct_runtime"] == 2
b = d["build"]
assert b["level"] == "discover" and b["build_ok"] is True and b["roots_found"] == 2 and b["roots_probed"] == 2, b
assert b["status"] == "built" and b["runnability"] is None and b["discover_index"] is not None, b
assert d["code_age"]["status"] == "complete" and d["code_age"]["coverage_share"] == 1.0
# census: logic depth computed from weighted counts, reviewer judgement kept, grounding checked
c = d["census"]
assert c["weighted_total"] == 3.0 * 2 + 2.0 * 1 + 0.75 * 1 + 0.5 * 1 and c["logic_depth"] == 2 and c["reviewer_judgement"] == 5, c
assert len(c["categories"]["work_units"]) == 1 and c["model"] == "test-model-1"
assert c["ideas_total"] == 2 and c["ideas_by_kind"] == {"net_new": 1, "bug_repair": 1, "repo_evolution": 0, "long_horizon": 0}, c
assert c["task_mining"]["status"] == "done" and c["task_mining"]["dropped_ideas"] == 5, c["task_mining"]
assert "grounding" not in json.dumps(c)
lg = d["logic"]
assert lg["authoritative"] == "reviewer_census" and lg["logic_depth"] == 2 and lg["deterministic_depth"] is not None, lg
assert d["task_mining"]["status"] == "done" and d["task_mining"]["ideas_total"] == 2
assert d["status"] == "measured" and d["status_reasons"] == [] and d["skip_reason"] is None, (d["status"], d["status_reasons"])
row = list(csv.DictReader(open(w / "full/summary_row.csv")))[0]
assert row["commits"] == "9" and row["tasks_bugfix"] == "1" and row["mined_ideas"] == "2" and row["mined_bug_repair"] == "1"
assert row["logic_depth_authoritative"] == "reviewer_census" and row["capacity"] == str(cap["mineable_commits"])
fb = load("fullbuild")["build"]
assert fb["status"] in ("environment_missing", "tests_passed"), fb["status"]
if fb["status"] == "environment_missing":  # no pytest here: the repo is not blamed, runnability is not scored
    assert fb["runnability"] is None and fb["failure_class"] == "environment" and fb["build_ok"] is True, fb
    assert "build_environment_missing" in load("fullbuild")["status_reasons"]
s = load("simple")
assert s["mode"] == "simple" and s["status"] == "estimate" and s["estimate"] is True and "simple_mode" in s["status_reasons"]
assert s["git"]["commits"] == 9 and s["tasks"]["candidates"] is None and s["task_mining"]["status"] == "not_run"
assert s["build"]["build_ok"] is None and s["recommendation"]["full_run"] in ("recommended", "optional", "not worth it")
assert s["tree"]["code_lines_total"] > 0
e = load("o-empty")
assert e["status"] == "partial" and e["skip_reason"] == "no_commits" and e["git"]["commits"] == 0
p = load("o-plain")
assert p["status"] == "partial" and p["skip_reason"] == "not_a_git_repository" and p["git"]["commits"] is None
assert p["tree"]["code_lines_total"] == 2
assert load("o-plain-simple")["status"] == "estimate"
sh = load("o-shallow")
assert sh["git"]["history_shallow"] is True and sh["git"]["history_complete"] is False and "shallow_clone" in sh["status_reasons"]
assert sh["git"]["pre_cutoff_share"] is None and sh["git"]["ai_assisted_share"] is None and sh["git"]["authors_human"] is None
assert sh["code_age"] is None and sh["tasks"]["capacity"] is None
assert not any("commit" in r for r in sh["classification"]["demo_reasons"]), sh["classification"]
ra, rh, rall = load("o-ref-auto"), load("o-ref-head"), load("o-ref-all")
assert ra["history_ref"]["measured"] == "deepest_branch" and ra["git"]["commits"] == 15 and ra["history_ref"]["head_commits"] == 1, ra["history_ref"]
assert rh["history_ref"]["measured"] == "head" and rh["git"]["commits"] == 1 and rh["history_ref"]["deeper_ref_exists"] is True
assert rall["history_ref"]["measured"] == "all_refs" and rall["git"]["commits"] == 16
text = "".join((w / p / f).read_text() for p in ("full", "simple", "o-shallow", "o-ref-auto") for f in ("measure.json", "report.md"))
for leak in ("Dev One", "one@example.com", "calc", "subtraction", "Zorblax", "Claude Smith", "gemini.com", sys.argv[2][:10], "xqbranch"):
    assert leak not in text, leak
print("selftest: measure.json checks OK")
EOF

# Verification: an untouched file re-measures clean; an edited one does not.
"$PY" "$M" "$REPO" --verify "$WORK/full/measure.json" >/dev/null
"$PY" - "$WORK/full/measure.json" "$WORK/tampered.json" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1])); d["git"]["commits"] += 100; json.dump(d, open(sys.argv[2], "w"))
EOF
if "$PY" "$M" "$REPO" --verify "$WORK/tampered.json" >/dev/null; then echo "tampered measure.json passed verification"; exit 1; fi
echo "selftest: verify OK"

# Units: matchers, classifier, bands, boundary.
"$PY" - "$M" "$C" "$WORK" <<'EOF'
import importlib.util, json, pathlib, sys
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod
m, c = load("m", sys.argv[1]), load("c", sys.argv[2])
w = pathlib.Path(sys.argv[3])
assert m.ai_trailer("Claude <noreply@anthropic.com>") and m.ai_identity("GitHub Copilot", "") and m.ai_identity("Claude Code", "")
assert not m.ai_identity("Claude", "claude@gemini.com") and not m.ai_identity("Cursor", "") and not m.ai_identity("Devin", "devin@example.com")
assert not m.BOT_RX.search("123+dev@users.noreply.github.com") and m.BOT_RX.search("dependabot[bot]")
assert m.commit_kind("Bug 123 - add multiply", 0, 1) == "feature" and m.commit_kind("fix crash", 0, 1) != "bugfix"
assert m.commit_kind("[CORE-12] fix crash on load", 3, 1) == "bugfix"
assert m.NOISE_RX.search("bump lodash") and m.NOISE_RX.search("fix typo") and not m.NOISE_RX.search("add retry to uploads")
band, _, _ = m.logic_band({"source_code_lines": 500_000, "branches_per_kloc": 80, "files_nesting_5plus_share": 0.04,
                           "top_level_modules": 30}, {"test_to_source_line_ratio": 0.1})
assert band < 6, ("large repos must not saturate the deterministic band", band)
assert m.classify_failure("ModuleNotFoundError: No module named 'x'", False) == "environment"
assert m.classify_failure("SyntaxError: invalid syntax", False) == "repo" and m.classify_failure("", True) == "timeout"
pj = w / "fw"; pj.mkdir()
(pj / "package.json").write_text(json.dumps({"dependencies": {"react": "18"}, "devDependencies": {"jest": "29"}}))
assert m.detect_test_frameworks(pj, ["package.json"]) == ["jest"]
r = m.stage_blame(w / "demo", "2025-01-01", -1)
assert r["status"] == "partial" and r["partial_reason"] == "budget_exhausted" and r["share_written_since_cutoff"] is None
assert c.band_of(0) == 0 and c.band_of(8) == 2 and c.band_of(80) == 6
assert m.boundary({"git": {"note": "secret/path.py"}}, set())
assert m.boundary({"census": {"summary": "written by someone@example.com"}}, set())
assert not m.boundary({"label": "x", "git": {"first_commit": "2020-01-01"}}, set())
print("selftest: units and boundary OK")
EOF
# Dream mode: validate and insert "Why this is special", keep it on re-runs, refuse bad drafts, honour --no-dream.
D="$HERE/scripts/add_dream.py"
cat > "$WORK/dream.txt" <<'EOF'
This little arithmetic library punches well above its size. Across 9 commits, 4 people turned a one-line helper into
input-checked math with a real test suite behind it. CI runs those tests on every push, and the crash fix shipped
together with the test that guards it. It is small, but it was built the way careful teams build big things.
EOF
"$PY" "$D" "$WORK/dream.txt" --out "$WORK/detect" --repo "$REPO" --check 2>/dev/null
if grep -q "Why this is special" "$WORK/detect/report.md"; then echo "--check wrote the section"; exit 1; fi
"$PY" "$D" "$WORK/dream.txt" --out "$WORK/detect" --repo "$REPO" >/dev/null 2>&1
"$PY" "$M" "$REPO" --verify "$WORK/detect/measure.json" >/dev/null   # the payload digest covers the inserted pitch
"$PY" "$M" "$REPO" --out "$WORK/detect" --label demo-repo --blame-seconds 60 >/dev/null 2>&1   # a re-run keeps it
refuse() {  # name, draft text: add_dream must refuse it
  printf '%s\n' "$2" > "$WORK/bad.txt"
  if "$PY" "$D" "$WORK/bad.txt" --out "$WORK/detect" --repo "$REPO" --check 2>/dev/null; then echo "dream accepted: $1"; exit 1; fi
}
refuse empty ""
refuse too-short "Great repo. Ships tests. Built with care."
refuse too-long "$(for i in 1 2 3 4 5 6 7; do printf 'It has 9 commits of careful work. '; done)"
refuse email "It has 9 commits of careful work. Ask one@example.com for more. Tests run in CI. It is small but polished."
refuse path "It has 9 commits of careful work. The heart lives in src/ops/mul.py today. Tests run in CI. It is small but polished."
refuse file-name "It has 9 commits of careful work. The core sits in one README.md file. Tests run in CI. It is small but polished."
refuse author "It has 9 commits of careful work. Dev One wrote most of it with love. Tests run in CI. It is small but polished."
refuse invented-number "It has 9 commits of careful work. It already serves 250,000 players a day. Tests run in CI. It is small but polished."
refuse url "It has 9 commits of careful work. See https://example.org/store for the page. Tests run in CI. It is small but polished."
refuse secret "It has 9 commits of careful work. The key AKIA$(printf "%s" ABCDEFGHIJKLMNOP) sits in config. Tests run in CI. It is small but polished."
refuse bullets "- It has 9 commits of careful work.
- Tests run in CI.
- It is small.
- It is polished."
# --no-dream: on the measurement, and on an existing output through add_dream.
"$PY" "$M" "$REPO" --out "$WORK/nodream" --label demo-repo --blame-seconds 0 --no-dream >/dev/null 2>&1
cp -R "$WORK/detect" "$WORK/declined" && "$PY" "$D" --no-dream --out "$WORK/declined" >/dev/null 2>&1
if "$PY" "$D" "$WORK/dream.txt" --out "$WORK/declined" 2>/dev/null; then echo "dream added after --no-dream"; exit 1; fi
# The shipped synthetic example passes the same checks against its own report.
"$PY" "$D" "$HERE/examples/starfall-client/dream.txt" --out "$HERE/examples/starfall-client" --check 2>/dev/null
"$PY" - "$WORK" <<'EOF'
import csv, json, sys, pathlib
w = pathlib.Path(sys.argv[1])
load = lambda p: json.loads((w / p / "measure.json").read_text())
d = load("detect")
assert d["status"] == "measured" and d["status_reasons"] == [], (d["status"], d["status_reasons"])
assert d["status_info"] == {"build": "not_requested"} and d["build"]["status"] == "not_requested", (d["status_info"], d["build"]["status"])
rep = (w / "detect/report.md").read_text()
assert "**Status: measured**" in rep and "info: build not_requested" in rep, rep[:400]
dr = d["dream"]
assert dr["status"] == "written" and dr["title"] == "Why this is special" and dr["sentences"] == 4 and "9 commits" in dr["text"], dr
assert rep.rstrip().endswith("careful teams build big things."), rep[-300:]
assert rep.index("## Why this is special") > rep.index("## Caveats"), "the pitch comes last, after the measured sections"
assert "not a measurement" in rep.split("## Why this is special")[1]
row = list(csv.DictReader(open(w / "detect/summary_row.csv")))[0]
assert row["status"] == "measured" and row["status_info"] == "build:not_requested" and row["dream_status"] == "written", row
nd = load("nodream")
assert nd["dream"]["status"] == "skipped" and "Why this is special" not in (w / "nodream/report.md").read_text()
assert "code_age_not_run" in nd["status_reasons"] and nd["status"] == "partial", "an explicitly skipped lane is still partial"
dc = load("declined")
assert dc["dream"]["status"] == "skipped" and "Why this is special" not in (w / "declined/report.md").read_text()
assert json.loads((w / "declined/dream.json").read_text())["status"] == "skipped"
assert load("full")["dream"]["status"] == "pending" and "Why this is special" not in (w / "full/report.md").read_text()
print("selftest: status rules and dream mode OK")
EOF
# v0.5: people by year with identity merging, ticket references, the pre-ChatGPT cutoff, labels, restriction flags.
P="$WORK/people"; mkdir -p "$P/Plugins/ThirdParty/zqlib" "$P/Content" "$P/Platforms/PS5" && cd "$P" && git init -q -b main
printf 'a = 1\n' > a.py; printf 'K=1\n' > .env; printf 'x' > Content/Hero.uasset; printf 'x' > Content/Level.umap
printf 'int f();\n' > Plugins/ThirdParty/zqlib/lib.h; printf 'x\n' > Platforms/PS5/cfg.ini
commit_as "2021-05-03T10:00:00Z" "Jane Q Dev" "jane@corp.example" "PRJ-7 initial"
printf 'a = 2\n' > a.py; commit_as "2021-05-20T10:00:00Z" "jane q dev" "jane.home@else.example" "fix crash PRJ-8"
printf 'a = 3\n' > a.py; commit_as "2022-03-01T10:00:00Z" "jqd" "991+jqdev@users.noreply.github.com" "tidy (refs #31)"
printf 'a = 4\n' > a.py; commit_as "2023-02-01T10:00:00Z" "Jane Q Dev" "991+jqdev@users.noreply.github.com" "fix load bug UTF-8 SHA-256 PRJ-8"
printf 'a = 5\n' > a.py; commit_as "2024-07-01T10:00:00Z" "Other Person" "other@corp.example" "add feature"
printf 'a = 6\n' > a.py; commit_as "2024-07-02T10:00:00Z" "dependabot[bot]" "bot@example.com" "bump PRJ-99"
cd "$WORK"
"$PY" "$M" "$P" --out "$WORK/o-people" --mode simple --label people-repo >/dev/null 2>&1
"$PY" "$M" "$P" --out "$WORK/o-people-early" --mode simple --cutoff-early 2021-06-01 --link-salt s3cret \
  --flag client_owned --flag player_personal_data=no >/dev/null 2>&1
"$PY" "$M" "$P" --out "$WORK/o-people-full" --label people-repo --blame-seconds 0 --no-dream >/dev/null 2>&1
"$PY" - "$WORK" "$M" <<'PYEOF'
import json, sys, pathlib, hashlib, importlib.util
w = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("m", sys.argv[2]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
d = json.loads((w / "o-people/measure.json").read_text())
pe = d["people"]
# Jane's three identities (two emails share a normalised name, the noreply login links the third) are one person
assert pe["merged_people"] == 2 and pe["identities"] == 5, pe
assert pe["by_year"] == {"2021": {"unique_people": 1, "active_person_months": 1}, "2022": {"unique_people": 1, "active_person_months": 1},
                         "2023": {"unique_people": 1, "active_person_months": 1}, "2024": {"unique_people": 1, "active_person_months": 1}}, pe["by_year"]
jane = max(pe["persons"], key=lambda p: p["commits"])
assert jane["commits"] == 4 and jane["months"] == ["2021-05", "2022-03", "2023-02"] and len(jane["tokens"]) == 4, jane
assert m.link_hash("email", "jane@corp.example") in jane["tokens"] and m.link_hash("gh", "jqdev") in jane["tokens"]
assert m.link_hash("x", "y") == hashlib.sha256(b"|x|y").hexdigest()[:16]
tr = d["ticket_refs"]
assert tr["commits_with_ref"] == 5 and tr["fix_commits_with_ref"] == 2, tr        # bot commits count for refs, merges do not
assert tr["by_pattern"]["project_key"] == {"commits": 4, "distinct_keys": 3}, tr["by_pattern"]   # UTF-8 and SHA-256 are not keys
assert tr["by_pattern"]["hash_number"] == {"commits": 1, "distinct_keys": 1}
assert m.link_hash("ticket", "PRJ-8") in tr["key_hashes"] and m.link_hash("ticket", "#31") in tr["key_hashes"]
assert m.link_hash("ticket", "UTF-8") not in tr["key_hashes"] and len(tr["key_hashes"]) == 4
g, c = d["git"], d["cutoffs"]
assert g["pre_chatgpt_commits"] == 3 and g["pre_chatgpt_share"] == 0.5 and d["cutoff_early"] == "2022-11-30", g
assert c["pre_chatgpt"] == {"date": "2022-11-30", "count": 3, "share": 0.5, "label": "verified", "unit": "commits"}, c
assert c["pre_2025"]["count"] == 6 and c["pre_2025"]["date"] == "2025-01-01"
r = d["restrictions"]["flags"]
assert d["restrictions"]["ownership"] == "own" and r["client_owned"]["value"] is None and r["client_owned"]["label"] == "unknown"
assert r["secrets"]["value"] is True and r["secrets"]["count"] == 1 and r["secrets"]["label"] == "verified", r["secrets"]
assert r["platform_nda"]["value"] is True and r["third_party_middleware"]["value"] is True, r
ec = d["engine_content"]
assert ec["files_by_type"]["uasset"] == 1 and ec["files_by_type"]["umap"] == 1, ec
assert ec["blueprints"] is None and ec["status"] == "not_run" and ec["label"] == "unknown", ec   # simple mode: counter not run
lb = d["labels"]
assert lb["tree.code_lines_total"]["label"] == "estimated" and lb["tree.code_lines_total"]["method"]
assert lb["git.commits"]["label"] == "verified" and lb["engine_content.blueprints"]["label"] == "unknown"
assert d["own_code"]["own_loc"] is None and d["own_code"]["label"] == "unknown"
assert all(v["label"] in m.LABELS_VOCAB for v in lb.values())
assert all(v.get("method") for v in lb.values() if v["label"] == "estimated"), "estimated needs a method"
e = json.loads((w / "o-people-early/measure.json").read_text())
assert e["git"]["pre_chatgpt_commits"] == 2 and e["cutoffs"]["pre_chatgpt"]["date"] == "2021-06-01"
assert e["people"]["link_salt_used"] and m.link_hash("email", "jane@corp.example") not in json.dumps(e), "the salt changes every key"
assert m.link_hash("ticket", "PRJ-8", "s3cret") in e["ticket_refs"]["key_hashes"]
er = e["restrictions"]
assert er["ownership"] == "client_owned" and er["flags"]["client_owned"] == {"value": True, "label": "self_reported", "method": "stated by the seller"}
assert er["flags"]["player_personal_data"]["value"] is False and er["flags"]["player_personal_data"]["label"] == "self_reported"
f = json.loads((w / "o-people-full/measure.json").read_text())
assert f["labels"]["tree.code_lines_total"]["label"] == "verified" and f["people"]["merged_people"] == 2
rep = (w / "o-people-full/report.md").read_text()
assert "## 11. People by year" in rep and "pre-ChatGPT" in rep and "| platform nda | yes" in rep, rep
row = (w / "o-people-full/summary_row.csv").read_text()
assert "pre_chatgpt_share" in row and "people_merged" in row and "commits_with_ticket_ref" in row
for o in ("o-people", "o-people-early", "o-people-full"):
    text = (w / o / "measure.json").read_text() + (w / o / "report.md").read_text()
    for leak in ("Jane", "jane", "jqdev", "corp.example", "PRJ", "zqlib", "Other Person", "Platforms"):
        assert leak not in text, (o, leak)
# shallow and plain folders: unknown, never zero
p = json.loads((w / "o-plain/measure.json").read_text())
assert p["people"]["merged_people"] is None and p["people"]["label"] == "unknown" and p["cutoffs"]["pre_chatgpt"]["count"] is None
assert p["labels"]["git.commits"]["label"] == "unknown"
em = json.loads((w / "o-empty/measure.json").read_text())
assert em["people"]["merged_people"] == 0 and em["ticket_refs"]["commits_with_ref"] == 0
sh = json.loads((w / "o-shallow/measure.json").read_text())
assert sh["people"]["merged_people"] is None and sh["git"]["pre_chatgpt_commits"] is None
print("selftest: people, tickets, cutoffs, labels and restrictions OK")
PYEOF
"$PY" "$M" "$P" --verify "$WORK/o-people-full/measure.json" >/dev/null
if "$PY" "$M" "$P" --out "$WORK/o-badflag" --mode simple --flag nonsense >/dev/null 2>&1; then echo "unknown flag accepted"; exit 1; fi
# Own code (section 8 rules), the vendored Blueprint counter on synthetic packages, settling, fresh uploads, Perforce.
U="$WORK/unreal"; mkdir -p "$U" && cd "$U" && git init -q -b main
"$PY" - "$U" <<'PYEOF'
import struct, pathlib, sys
u = pathlib.Path(sys.argv[1])
def fstr(s):
    b = s.encode("latin-1") + b"\0"
    return struct.pack("<i", len(b)) + b
def package(names, nodes, cooked=False):
    head = struct.pack("<Iiiii", 0x9E2A83C1, -7, 864, 522, 0) + struct.pack("<ii", 0, 0) + fstr("None")
    head += struct.pack("<I", 0x80000000 if cooked else 0)
    offset = len(head) + 8 + 16
    table = b"".join(fstr(n) + b"\0\0\0\0" for n in names)
    body = struct.pack("<iiii", names.index("NodeGuid"), 0, names.index("StructProperty"), 0) * nodes if "NodeGuid" in names else b""
    return head + struct.pack("<ii", len(names), offset) + b"\0" * 16 + table + body + b"\0" * 64
def w(rel, data):
    p = u / rel; p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data) if isinstance(data, bytes) else p.write_text(data)
bp = ["Blueprint", "K2Node_CallFunction", "NodeGuid", "StructProperty", "EdGraph"]
w("Game.uproject", '{"FileVersion": 3}')
w("Content/Blueprints/BP_Hero.uasset", package(bp + ["HeroThing"], 3))
w("Content/Blueprints/BP_Copy.uasset", package(bp + ["HeroThing"], 3))          # same bytes: counted once
w("Content/Blueprints/Big.uasset", "version https://git-lfs.github.com/spec/v1\noid sha256:00\nsize 9\n")
w("Content/Blueprints/Tex.uasset", package(["Texture2D", "StructProperty", "PixelFormat"], 0))
w("Content/StarterContent/BP_Door.uasset", package(bp + ["DoorThing"], 2))      # template content
w("Content/Maps/Main.umap", package(["LevelScriptBlueprint", "K2Node_Event", "NodeGuid", "StructProperty", "World"], 4))
w("Content/AI/BT_Main.uasset", package(["BehaviorTreeGraphNode_Root", "NodeGuid", "StructProperty", "BehaviorTree"], 2))
lines = lambda n, tag: "".join(f"int {tag}_{i} = {i};\n\n" for i in range(n))
w("Source/Game/Player.cpp", "// Fill out your copyright notice in the Description page of Project Settings.\n" + lines(10, "p"))
w("Source/Game/Player.h", lines(4, "h"))
w("Source/Game/Copy.cpp", "// Fill out your copyright notice in the Description page of Project Settings.\n" + lines(10, "p"))
w("Source/Game/Zq1.cpp", "// Copyright 2020 Zq Studio. All rights reserved.\n" + lines(5, "a"))
w("Source/Game/Zq2.cpp", "// Copyright (c) 2021 Zq Studio\n" + lines(5, "b"))
w("Source/Game/Acme.cpp", "// Copyright 2019 Acme Middleware Ltd. All Rights Reserved.\n" + lines(7, "c"))
w("Source/Game/Epic.cpp", "// Copyright Epic Games, Inc. All Rights Reserved.\n" + lines(6, "e"))
w("Source/Game/Player.generated.h", lines(8, "g"))
w("Source/Game/Game.Build.cs", lines(3, "r")); w("Source/Game.Target.cs", lines(3, "t"))
w("Source/Game/Android/Glue.java", lines(2, "j"))
w("Source/ThirdParty/zqlib/lib.cpp", lines(9, "v"))
w("Intermediate/Build/x.cpp", lines(5, "x"))
w("Plugins/ZqTool/ZqTool.uplugin", '{"CreatedBy": "Zq Studio"}'); w("Plugins/ZqTool/Source/Tool.cpp", lines(6, "zt"))
w("Plugins/Qxplug/Qxplug.uplugin", '{"CreatedBy": "Some Vendor GmbH"}'); w("Plugins/Qxplug/Source/Qxplug.cpp", lines(11, "vd"))
w("settle.json.txt", "x")
PYEOF
rm -f settle.json.txt
commit_as "2020-02-01T10:00:00Z" "Zq Dev" "zq@studio.example" "import project"
commit_as "2020-02-02T10:00:00Z" "Zq Dev" "zq@studio.example" "touch" 2>/dev/null || true
printf '{"plugins": {"ZqTool": "own"}, "content": {"Blueprints": "own", "Maps": "own", "AI": "bought"}}' > "$WORK/settle.json"
cd "$WORK"
"$PY" "$M" "$U" --out "$WORK/o-u" --label game-x --blame-seconds 0 --no-dream >/dev/null 2>&1
"$PY" "$M" "$U" --out "$WORK/o-u-studio" --label game-x --blame-seconds 0 --no-dream --studio-name "Zq Studio" >/dev/null 2>&1
"$PY" "$M" "$U" --out "$WORK/o-u-settled" --label game-x --blame-seconds 0 --no-dream --settle "$WORK/settle.json" \
  --project-type client_game >/dev/null 2>&1
mkdir -p "$WORK/bin"
cat > "$WORK/bin/p4" <<'SH'
#!/bin/sh
case "$*" in
  *"changes -s submitted //depot/main/..."*) for c in 1 2 3; do printf '... change %s\n... time %s\n... user u%s\n' $c $((1500000000 + c * 86400)) $c; done ;;
  *"changes -s submitted //depot/dev/..."*) for c in 3 4; do printf '... change %s\n... time %s\n... user u%s\n' $c $((1500000000 + c * 86400)) $c; done ;;
  *users*) printf '... User a\n... User b\n... User c\n' ;;
esac
SH
chmod +x "$WORK/bin/p4"
PATH="$WORK/bin:$PATH" "$PY" "$M" "$U" --out "$WORK/o-u-p4" --mode simple --p4-path //depot/main/... --p4-path //depot/dev/... >/dev/null 2>&1
"$PY" - "$WORK" "$M" <<'PYEOF'
import json, sys, pathlib, importlib.util
w = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("m", sys.argv[2]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
# the TOTAL line, thousands separators included
t = m.parse_bp_line("TOTAL: Behavior Tree graphs 3, Behavior Tree nodes 1,204, blueprint nodes 12,345, blueprints 1,002, lfs pointer 2")
assert t == {"behavior_tree_graphs": 3, "behavior_tree_nodes": 1204, "blueprint_nodes": 12345, "blueprints": 1002, "lfs_pointer": 2}, t
assert m.bp_summary(t)["other_graphs"] == {"behavior_tree": {"graphs": 3, "nodes": 1204}} and m.bp_summary(t)["cooked"] == 0
assert m.parse_bp_line("TOTAL: ") == {}
assert m.is_lfs_pointer(b"version https://git-lfs.github.com/spec/v1\n") and not m.is_lfs_pointer(b"\xc1\x83\x2a\x9e")
d = json.loads((w / "o-u/measure.json").read_text())
ec = d["engine_content"]
assert ec["status"] == "counted" and ec["blueprints"] == 2 and ec["blueprint_nodes"] == 5, ec      # the copy counted once
assert ec["level_blueprints"] == 1 and ec["level_blueprint_nodes"] == 4 and ec["duplicate_blueprints"] == 1, ec
assert ec["lfs_pointer"] == 1 and ec["other_graphs"] == {"behavior_tree": {"graphs": 1, "nodes": 2}}, ec
assert ec["label"] == "estimated" and ec["partial"] and ec["partial_reason"] == "counts incomplete", ec   # an LFS pointer
assert ec["own"]["blueprints"] == 1 and ec["own"]["blueprint_nodes"] == 3 and ec["excluded"]["blueprints"] == 1, ec  # StarterContent
oc = d["own_code"]
ex = oc["excluded"]
assert oc["unreal_projects"] == 1 and oc["lfs_pointer_assets"] == 1
assert ex["build_rules"] == {"files": 2, "loc": 6} and ex["generated"]["files"] == 1 and ex["duplicate_content"] == {"files": 1, "loc": 11}, ex
assert ex["foreign_copyright"] == {"files": 1, "loc": 8} and ex["engine_template_code"]["files"] == 1, ex
assert ex["vendored"]["loc"] == 9 and ex["build_output_or_cache"]["loc"] == 5 and ex["unsettled_plugin"]["files"] == 2, ex
# own: Player.cpp 11 (UE default notice kept), Player.h 4, Zq1/Zq2 6 each (presumed studio holder), Glue.java 2
assert oc["own_loc"] == 29 and oc["own_files"] == 5, oc
assert oc["label"] == "estimated" and "presumed" in oc["method"] and oc["unsettled"]["plugins"] == 2, oc
assert oc["content_folders"] == {"total": 4, "own": 0, "template": 1, "bought": 0, "unsettled": 3}, oc["content_folders"]
assert oc["unsettled"]["non_cpp_code_in_unreal_loc"] == 2
st = json.loads((w / "o-u-studio/measure.json").read_text())["own_code"]
assert st["own_loc"] == 35 and st["excluded"]["third_party_plugin"] == {"files": 1, "loc": 11} and "unsettled_plugin" not in st["excluded"], st
se = json.loads((w / "o-u-settled/measure.json").read_text())
assert se["own_code"]["content_folders"] == {"total": 4, "own": 2, "template": 1, "bought": 1, "unsettled": 0}, se["own_code"]["content_folders"]
assert se["own_code"]["excluded"]["unsettled_plugin"]["files"] == 1 and se["own_code"]["own_loc"] == 35
ct = se["code_table"]
assert ct["type"] == "client_game" and ct["type_label"] == "self_reported" and ct["vcs"] == "git" and ct["own_loc"] == 35, ct
assert ct["blueprints"] == 2 and ct["blueprint_nodes"] == 5 and ct["primary_language"] == "C++" and ct["contributors_people"] == 1, ct
assert ct["pct_merged"] is None and se["labels"]["code_table.pct_merged"]["label"] == "unknown"
assert set(ct) >= {"project", "engine", "commits", "prs_merged", "source_files", "test_files", "first_commit", "last_commit",
                   "assets_own", "assets_excluded"}
assert ct["assets_excluded"] >= 2 and ct["assets_own"] >= 3, ct      # StarterContent and the bought AI folder are excluded
# fresh upload: two commits a day apart
assert d["git"]["fresh_upload"] is True and d["status_info"].get("history") == "fresh_upload", d["status_info"]
rep = (w / "o-u/report.md").read_text()
assert "## 12. Own code" in rep and "Fresh upload" in rep and "counts incomplete" in rep and "| foreign copyright | 1 | 8 |" in rep, rep
# the settling list names folders, plugins and files, privately; measure.json and report.md never do
pv = (w / "o-u/.private/settling.md").read_text()
assert "Acme" in pv and "Content/StarterContent" in pv and "Plugins/Qxplug" in pv and "Some Vendor GmbH" in pv and "PRIVATE" in pv
for o in ("o-u", "o-u-studio", "o-u-settled", "o-u-p4"):
    text = (w / o / "measure.json").read_text() + (w / o / "report.md").read_text()
    for leak in ("Acme", "Qxplug", "ZqTool", "StarterContent", "Zq Studio", "BP_Hero", "Glue", "Player", "Blueprints/"):
        assert leak not in text, (o, leak)
pf = json.loads((w / "o-u-p4/measure.json").read_text())
p4 = pf["perforce"]
assert p4["status"] == "counted" and p4["submitted_changes"] == 4 and p4["paths"] == 2, p4   # change 3 sits in both streams
assert p4["submitting_accounts"] == 4 and p4["server_users"] == 3 and p4["first_change"] == "2017-07-15", p4
assert pf["code_table"]["vcs"] == "git+perforce"
assert json.loads((w / "o-people/measure.json").read_text())["perforce"]["status"] == "not_requested"
print("selftest: own code, Blueprints, settling, fresh upload and Perforce OK")
PYEOF
"$PY" "$M" "$U" --verify "$WORK/o-u/measure.json" >/dev/null
echo "selftest: passed"
