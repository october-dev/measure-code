#!/usr/bin/env python3
"""Validate a reviewer census for a measure-code run and store it as <out>/census.json.

The census is the one judgement lane: the agent running the skill reads the
code in its own session and writes a JSON file like this:

  {
    "logic_depth": 4,                 # 0-6, how much real logic the code carries
    "self_contained": 3,              # 0-4, how far it builds and runs without outside services
    "categories": {                   # one generic sentence per item; the count IS the list
      "complex_logic": ["..."], "work_units": ["..."], "integrations": ["..."],
      "performance_work": ["..."], "hardening_work": ["..."]
    },
    "themes": ["..."],                # up to 8 short generic themes
    "summary": "...",                 # one or two generic sentences
    "ideas": [{"kind": "bugfix|feature|restructure|exploratory", "text": "..."}]
  }

Every sentence must describe the SHAPE of the work, never the product. A
sentence holding a path, file name, identifier, URL, email, long number, a
word from the repo's own file names, or a capitalised word outside a short
list of generic technology terms is dropped whole and reported; it is never
patched. Usage:

  python3 add_census.py census-draft.json --out <measure-out-dir> --repo <repo>

Then re-run measure_code.py (cached stages make that quick).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

KINDS = {"bugfix", "feature", "restructure", "exploratory"}
CATEGORIES = {"complex_logic", "work_units", "integrations", "performance_work", "hardening_work"}
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Validate and store a reviewer census.")
    ap.add_argument("draft", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--repo", type=Path, required=True)
    a = ap.parse_args(argv)
    d = json.loads(a.draft.read_text("utf-8"))
    rwords = repo_words(a.repo)
    dropped = []

    def keep(text, where):
        why = check(text, rwords)
        if why:
            dropped.append(f"{where}: {why}")
            return False
        return True

    try:
        depth, contained = int(d["logic_depth"]), int(d.get("self_contained", 0))
    except (KeyError, TypeError, ValueError):
        print("logic_depth (0-6) is required", file=sys.stderr)
        return 2
    if not (0 <= depth <= 6 and 0 <= contained <= 4):
        print("logic_depth must be 0-6 and self_contained 0-4", file=sys.stderr)
        return 2
    cats = {}
    for k, items in (d.get("categories") or {}).items():
        if k not in CATEGORIES:
            print(f"unknown category '{k}', expected {sorted(CATEGORIES)}", file=sys.stderr)
            return 2
        cats[k] = [t.strip() for i, t in enumerate(items or []) if keep(t, f"{k}[{i}]")]
    ideas = []
    for i, idea in enumerate(d.get("ideas") or []):
        if idea.get("kind") not in KINDS:
            print(f"ideas[{i}]: unknown kind '{idea.get('kind')}', expected {sorted(KINDS)}", file=sys.stderr)
            return 2
        if keep(idea.get("text"), f"ideas[{i}]"):
            ideas.append({"kind": idea["kind"], "text": idea["text"].strip()})
    themes = [t.strip() for i, t in enumerate((d.get("themes") or [])[:8]) if keep(t, f"themes[{i}]")]
    summary = d.get("summary")
    if summary and not keep(summary, "summary"):
        summary = None
    census = {"reviewer": "agent", "logic_depth": depth, "self_contained": contained,
              "categories": {k: cats.get(k, []) for k in sorted(CATEGORIES)}, "themes": themes,
              "summary": summary, "ideas": ideas, "dropped_sentences": len(dropped)}
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "census.json").write_text(json.dumps(census, indent=2), encoding="utf-8")
    for line in dropped:
        print("dropped " + line, file=sys.stderr)
    print(f"kept {sum(len(v) for v in cats.values()) + len(ideas) + len(themes)} sentences, dropped {len(dropped)}; "
          f"wrote {a.out / 'census.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
