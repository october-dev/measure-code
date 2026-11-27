#!/usr/bin/env python3
"""Validate the reviewer census and task mining for a measure-code run and store it as <out>/census.json.

A full measure-code run requires this step. The agent running the skill reads
the code and history in its own session (so the session's AI model sees the
source, under the user's own account) and writes a private draft like this:

  {
    "model": "model-id",              # optional: the session's model id
    "self_contained": 3,              # 0-4, how far it builds and runs without outside services
    "reviewer_judgement": 4,          # optional 0-6 gut call; kept for reference, never authoritative
    "categories": {                   # one generic sentence per instance; the count IS the list
      "complex_logic": [], "work_units": [], "net_new_capabilities": [], "substantial_features": [],
      "defect_repairs": [], "repo_evolutions": [], "hardening_work": [], "integrations": [], "performance_work": []
    },
    "defect_repairs_with_test": 2,    # how many of defect_repairs shipped with a test
    "themes": ["..."], "summary": "...", "what_could_not_be_assessed": ["..."],
    "ideas": [                        # task mining: up to 150 ideas
      {"kind": "net_new|bug_repair|repo_evolution|long_horizon", "text": "...",
       "grounding": {"commit": "<sha you opened>", "file": "<tracked path you read>"}}
    ],
    "ideas_skip_reason": "..."        # only when there are no ideas: no_mineable_history | repo_too_small |
                                      # time_limit | declined_by_user
  }

The logic depth (0-6) is computed here from the weighted category counts; it is
not typed in. Each idea must be grounded in a commit or file that exists in the
repository; grounding is checked and then discarded, never written. Ideas past
the 150 cap, duplicates, ungrounded ideas, mechanical chores and ideas grounded
in credential files are dropped and reported.

Every sentence must describe the SHAPE of the work, never the product. A
sentence holding a path, file name, identifier, URL, email, long number, a
word from the repo's own file names, or a capitalised word outside a short
list of generic technology terms is dropped whole and reported; it is never
patched. Usage:

  python3 add_census.py census-draft.json --out <measure-out-dir> --repo <repo>

Then re-run measure_code.py (cached stages make that quick) and delete the draft.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

IDEA_KINDS = ("net_new", "bug_repair", "repo_evolution", "long_horizon")
KIND_ALIASES = {"feature": "net_new", "bugfix": "bug_repair", "restructure": "repo_evolution", "exploratory": "long_horizon",
                "agentic": "long_horizon"}
IDEA_CAP = 150
CATEGORY_CAP = 40
SKIP_REASONS = {"no_mineable_history", "repo_too_small", "time_limit", "declined_by_user"}
# Our published weights: work visible in today's tree counts more than work only visible in history,
# and a fix that shipped with a test earns a bonus.
WEIGHTS = {"complex_logic": 3.0, "substantial_features": 2.5, "work_units": 2.0, "integrations": 1.5,
           "performance_work": 1.5, "hardening_work": 1.0, "net_new_capabilities": 1.0, "defect_repairs": 0.75,
           "repo_evolutions": 0.75}
TEST_BONUS = 0.5
BANDS = ((80, 6), (50, 5), (30, 4), (16, 3), (8, 2), (3, 1), (0, 0))  # weighted total -> logic depth
CATEGORIES = set(WEIGHTS)
CATEGORY_ALIASES = {"integration_work": "integrations"}
CREDENTIAL_PATH_RX = re.compile(
    r"(^|/)(\.env(\.[\w.-]+)?|[^/]*\.(pem|key|p12|pfx|keystore|jks|asc|gpg)|id_(rsa|dsa|ecdsa|ed25519)[^/]*|[^/]*credential[^/]*|"
    r"[^/]*secret[^/]*|\.npmrc|\.pypirc|\.netrc|\.git-credentials|service[-_]account[^/]*\.json|[^/]*\.tfvars|"
    r"google-services\.json|GoogleService-Info\.plist)$", re.I)
MECHANICAL_RX = re.compile(r"\b(typos?|rename (a|the|one) (variable|field|file)|bump|update (the )?readme|fix (the )?formatting|"
                           r"reformat|add (a )?comments?|lint warnings?|whitespace|change (a|the) (colou?r|label|string))\b", re.I)
GENERIC_CAPS = {
    "API", "APIs", "REST", "HTTP", "HTTPS", "JSON", "XML", "YAML", "CSV", "SQL", "NoSQL", "UI", "UX", "CLI",
    "CI", "CD", "JWT", "OAuth", "SDK", "GPU", "CPU", "AI", "ML", "LLM", "URL", "URLs", "ID", "IDs", "UUID",
    "WebSocket", "WebSockets", "GraphQL", "gRPC", "Redis", "Postgres", "PostgreSQL", "MySQL", "SQLite",
    "MongoDB", "Docker", "Kubernetes", "Unity", "Unreal", "Godot", "React", "Vue", "Angular", "Node",
    "Python", "TypeScript", "JavaScript", "Go", "Rust", "Java", "Kotlin", "Swift", "C", "C++", "C#",
    "iOS", "Android", "AWS", "GCP", "Azure", "S3", "PDF", "PNG", "HTML", "CSS", "RPC", "ORM", "OS", "LOD",
    "NPC", "NPCs", "AI-driven", "VR", "AR", "2D", "3D",
}
BAD = [
    (re.compile(r"[/\\]"), "path separator"),
    (re.compile(r"\b\w+\.(py|js|ts|tsx|cs|cpp|h|go|rs|java|json|ya?ml|md|txt|unity|prefab|asset|sql)\b", re.I), "file name"),
    (re.compile(r"\b[a-z]+[A-Z]\w*\b"), "camelCase identifier"),
    (re.compile(r"\b\w+_\w+\b"), "snake_case identifier"),
    (re.compile(r"::|->|\(\)|`|#\w|\"|'\w+'"), "code syntax or quoted string"),
    (re.compile(r"https?:|www\.", re.I), "URL"),
    (re.compile(r"@"), "email or handle"),
    (re.compile(r"\d{4,}"), "long number"),
]
COMMON = set("""able about access account action active adapter admin agent alert algorithm allow analytics animation
array asset async audio auth authentication backend batch behaviour behavior binary block board buffer build button
cache camera card cart chat check client code collision color command common component config connection console
content context controller core count data database date debug default delete dependency deploy device dialog display
document domain download editor effect email engine entity error event export feature fetch field file filter flag
form format frame game handler header health helper history image import index input interface item items job layer
layout level library list loader local lock logger login logic manager map match menu message method migration model
module network node notification object order output page panel parser path payment physics player plugin pool post
preview profile project provider queue query random render report request resource response result route router rule
runtime save scene schema score screen script search server service session settings shader shared signal sound source
state storage store stream string style system table task template test text texture theme thread time timer token
tool track type update upload user utility utils validation value version view widget window worker inventory match
lobby replay""".split())


def repo_words(repo: Path) -> set:
    try:
        raw = subprocess.run(["git", "-C", str(repo), "ls-files"], capture_output=True, check=True).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.CalledProcessError):
        raw = "\n".join(os.path.relpath(os.path.join(r, f), repo) for r, _, fs in os.walk(repo) for f in fs)
    words = set()
    for token in re.split(r"[/\\._\-\s]+", raw):
        for part in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", token):
            if len(part) >= 4:
                words.add(part.lower())
    return words


def check(sentence: str, rwords: set, max_words: int = 40) -> str | None:
    s = (sentence or "").strip()
    if not 12 <= len(s) <= 260:
        return "length must be 12-260 characters"
    if len(s.split()) > max_words:
        return f"more than {max_words} words"
    for rx, why in BAD:
        if rx.search(s):
            return why
    for i, w in enumerate(re.findall(r"[A-Za-z][A-Za-z+#-]*", s)):
        if w[0].isupper() and w not in GENERIC_CAPS and i != 0:
            return f"capitalised word '{w}' may be a product or proper name"
        lw = w.lower()
        if len(lw) >= 5 and lw in rwords and lw not in COMMON and lw.rstrip("s") not in COMMON:
            return f"word '{w}' comes from the repo's own file names"
    return None


def band_of(total: float) -> int:
    return next(b for t, b in BANDS if total >= t)


def git_ok(repo: Path, *args) -> bool:
    try:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False).returncode == 0
    except OSError:
        return False


def tracked(repo: Path) -> set:
    try:
        raw = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True, check=True).stdout.decode("utf-8", "replace")
        return {x for x in raw.split("\0") if x}
    except (OSError, subprocess.CalledProcessError):
        return {os.path.relpath(os.path.join(r, f), repo).replace(os.sep, "/") for r, _, fs in os.walk(repo) for f in fs}


def grounding_problem(g, repo: Path, files: set) -> str | None:
    if not isinstance(g, dict) or not (g.get("commit") or g.get("file")):
        return "no grounding: name the commit or file you actually read"
    c, f = g.get("commit"), g.get("file")
    if f is not None:
        f = str(f).strip()
        f = f[2:] if f.startswith("./") else f
        if CREDENTIAL_PATH_RX.search(f):
            return "grounded in a credential-shaped file; skip those files"
        if f not in files and not (repo / f).is_file():
            return "grounding file does not exist in the repository"
    if c is not None:
        if not re.fullmatch(r"[0-9a-fA-F]{7,40}", str(c)) or not git_ok(repo, "cat-file", "-e", f"{c}^{{commit}}"):
            return "grounding commit does not exist in the repository"
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Validate and store the reviewer census and task mining.")
    ap.add_argument("draft", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--repo", type=Path, required=True)
    a = ap.parse_args(argv)
    d = json.loads(a.draft.read_text("utf-8"))
    repo = a.repo.expanduser().resolve()
    rwords = repo_words(repo)
    files = tracked(repo)
    dropped = []

    def keep(text, where):
        why = check(text if isinstance(text, str) else "", rwords)
        if why:
            dropped.append(f"{where}: {why}")
            return False
        return True

    try:
        contained = int(d.get("self_contained", 0))
        judgement = d.get("reviewer_judgement", d.get("logic_depth"))
        judgement = None if judgement is None else int(judgement)
    except (TypeError, ValueError):
        print("self_contained (0-4) and reviewer_judgement (0-6) must be integers", file=sys.stderr)
        return 2
    if not 0 <= contained <= 4 or (judgement is not None and not 0 <= judgement <= 6):
        print("self_contained must be 0-4 and reviewer_judgement 0-6", file=sys.stderr)
        return 2
    model = d.get("model")
    if model is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{1,60}", str(model)):
        print("model must be a plain model id (letters, digits, . _ : -)", file=sys.stderr)
        return 2
    cats = {}
    for k, items in (d.get("categories") or {}).items():
        k = CATEGORY_ALIASES.get(k, k)
        if k not in CATEGORIES:
            print(f"unknown category '{k}', expected {sorted(CATEGORIES)}", file=sys.stderr)
            return 2
        kept = []
        for i, t in enumerate(items or []):
            if len(kept) >= CATEGORY_CAP:
                dropped.append(f"{k}[{i}]: over the {CATEGORY_CAP}-item cap for one category")
                continue
            if keep(t, f"{k}[{i}]"):
                kept.append(t.strip())
        cats[k] = cats.get(k, []) + kept
    cats = {k: cats.get(k, []) for k in sorted(CATEGORIES)}
    try:
        with_test = int(d.get("defect_repairs_with_test") or 0)
    except (TypeError, ValueError):
        with_test = 0
    with_test = max(0, min(with_test, len(cats["defect_repairs"])))
    total = round(sum(WEIGHTS[k] * len(v) for k, v in cats.items()) + TEST_BONUS * with_test, 2)
    depth = band_of(total)

    ideas, seen, grounded, over_cap, idea_drops = [], set(), 0, 0, 0
    for i, idea in enumerate(d.get("ideas") or []):
        if not isinstance(idea, dict):
            print(f"ideas[{i}] must be an object", file=sys.stderr)
            return 2
        kind = KIND_ALIASES.get(idea.get("kind"), idea.get("kind"))
        if kind not in IDEA_KINDS:
            print(f"ideas[{i}]: unknown kind '{idea.get('kind')}', expected {list(IDEA_KINDS)}", file=sys.stderr)
            return 2
        text = (idea.get("text") or "").strip()
        if len(ideas) >= IDEA_CAP:
            over_cap += 1
            continue
        norm = re.sub(r"\W+", " ", text.lower()).strip()
        why = None
        if norm in seen:
            why = "duplicate of an earlier idea"
        elif len(text.split()) < 8:
            why = "below the bar: fewer than 8 words cannot describe multi-step work"
        elif MECHANICAL_RX.search(text):
            why = "below the bar: mechanical or one-sitting work"
        else:
            why = grounding_problem(idea.get("grounding"), repo, files)
        if why:
            dropped.append(f"ideas[{i}]: {why}")
            idea_drops += 1
            continue
        if not keep(text, f"ideas[{i}]"):
            idea_drops += 1
            continue
        seen.add(norm)
        grounded += 1
        ideas.append({"kind": kind, "text": text})
    if over_cap:
        dropped.append(f"ideas: {over_cap} past the {IDEA_CAP}-idea cap")
    skip = d.get("ideas_skip_reason")
    if ideas:
        tm = {"status": "done", "skip_reason": None}
    elif skip in SKIP_REASONS:
        tm = {"status": "skipped", "skip_reason": skip}
    elif d.get("ideas"):
        tm = {"status": "skipped", "skip_reason": "all_ideas_dropped"}
    else:
        print("task mining is a required step of a full run: add grounded ideas, or set ideas_skip_reason to one of "
              f"{sorted(SKIP_REASONS)}", file=sys.stderr)
        return 2
    tm.update(grounded_ideas=grounded, dropped_ideas=idea_drops + over_cap, cap=IDEA_CAP)
    themes = [t.strip() for i, t in enumerate((d.get("themes") or [])[:8]) if keep(t, f"themes[{i}]")]
    unassessed = [t.strip() for i, t in enumerate((d.get("what_could_not_be_assessed") or [])[:5]) if keep(t, f"what_could_not_be_assessed[{i}]")]
    summary = d.get("summary")
    if summary and not keep(summary, "summary"):
        summary = None
    census = {"reviewer": "agent", "model": model, "weights_version": "october-census-weights/1",
              "logic_depth": depth, "weighted_total": total, "reviewer_judgement": judgement, "self_contained": contained,
              "categories": cats, "category_counts": {k: len(v) for k, v in cats.items()},
              "defect_repairs_with_test": with_test, "themes": themes, "summary": summary,
              "what_could_not_be_assessed": unassessed, "ideas": ideas,
              "ideas_by_kind": {k: sum(1 for x in ideas if x["kind"] == k) for k in IDEA_KINDS},
              "ideas_total": len(ideas), "task_mining": tm, "dropped_sentences": len(dropped)}
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "census.json").write_text(json.dumps(census, indent=2), encoding="utf-8")
    for line in dropped:
        print("dropped " + line, file=sys.stderr)
    print(f"logic depth {depth}/6 from weighted total {total}; task mining {tm['status']}"
          f"{' (' + tm['skip_reason'] + ')' if tm['skip_reason'] else ''}: {len(ideas)} ideas kept "
          f"({', '.join(f'{k} {v}' for k, v in census['ideas_by_kind'].items())}); dropped {len(dropped)} sentences; "
          f"wrote {a.out / 'census.json'} (grounding discarded; delete the draft)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
