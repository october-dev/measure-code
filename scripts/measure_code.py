#!/usr/bin/env python3
"""measure-code: measure one git repository privately.

Two modes:

  --mode simple   listing and metadata only: history without file lists, a sampled
                  line count, path-based signals. No build, no blame, no review.
                  Minutes even on large repos. Every figure is marked as an
                  estimate. Says whether a full run is worth it.
  --mode full     (default) every lane: history census with lines changed per
                  commit, every file read, hygiene and sourcing signals, code age
                  by blame, build probe (detect by default; discover/full execute
                  repo code), and the reviewer census and task mining written
                  with add_census.py, which a full run requires.

Dream mode: after the audit the agent writes a short "Why this is special" pitch
with add_dream.py. It lands in the `dream` block of measure.json and in the last
section of report.md, labelled and kept apart from the measured sections.
--no-dream skips it.

Writes into --out:

  measure.json          all counts (schema october.measure/v1, kind "code")
  summary_row.json/.csv one flat row per repository, for spreadsheets
  report.md             the same numbers for people

`--verify <measure.json>` re-measures a copy of the repository and checks the
stable figures, fingerprints and digests of an existing measure.json.

No network calls are made by this script unless you pass --install (build
commands otherwise run with offline settings). Authors are counted, never
named. Paths, commit messages, branch names and emails never reach the output:
every string in the output must sit in a declared field, and the run refuses
to write anything else.

Each stage is cached under <out>/.state/ so an interrupted run resumes.
Python 3.9+ standard library only.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

TOOL = "measure-code"
VERSION = "0.4.0"
SCHEMA = "october.measure/v1"
SKETCH_K = 256
TASK_KINDS = ("bugfix", "feature", "restructure", "exploratory")

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def h(value: str, n: int = 16) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:n]


def bottom_k(values, k: int = SKETCH_K) -> list:
    return sorted({h(v, 16) for v in values})[:k]


def iso_day(ts) -> str | None:
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).strftime("%Y-%m-%d")


def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def log(msg: str) -> None:
    print(f"[{TOOL}] {msg}", file=sys.stderr, flush=True)


GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}


def git(repo: Path, *args: str, timeout: int = 3600) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False, env=GIT_ENV, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed")
    return out.stdout.decode("utf-8", "replace")


def pct(sorted_vals: list, q: float):
    if not sorted_vals:
        return None
    return sorted_vals[int(q * (len(sorted_vals) - 1))]


class Stages:
    def __init__(self, out: Path, force: bool, prefix: str):
        self.dir = out / ".state"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.force = force
        self.prefix = prefix

    def run(self, name: str, fn):
        path = self.dir / f"{self.prefix}-{name}.json"
        if path.exists() and not self.force:
            log(f"{name}: cached (use --force to redo)")
            return json.loads(path.read_text(encoding="utf-8"))
        t0 = time.time()
        log(f"{name}: running")
        result = fn()
        atomic_write(path, json.dumps(result, indent=1, sort_keys=True))
        log(f"{name}: done in {time.time() - t0:.1f}s")
        return result


# --------------------------------------------------------------------------
# vocabularies
# --------------------------------------------------------------------------

LANG_BY_EXT = {
    ".py": "Python", ".pyi": "Python",
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".mts": "TypeScript",
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin", ".scala": "Scala", ".groovy": "Groovy",
    ".cs": "C#", ".fs": "F#", ".vb": "Visual Basic",
    ".c": "C", ".h": "C/C++ header", ".cc": "C++", ".cpp": "C++", ".cxx": "C++",
    ".hpp": "C/C++ header", ".hh": "C/C++ header", ".hxx": "C/C++ header", ".inl": "C++",
    ".m": "Objective-C", ".mm": "Objective-C", ".swift": "Swift",
    ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP", ".pl": "Perl", ".lua": "Lua",
    ".dart": "Dart", ".ex": "Elixir", ".exs": "Elixir", ".erl": "Erlang", ".hs": "Haskell",
    ".clj": "Clojure", ".r": "R", ".jl": "Julia", ".zig": "Zig", ".nim": "Nim",
    ".gd": "GDScript", ".shader": "Shader", ".hlsl": "Shader", ".glsl": "Shader",
    ".cginc": "Shader", ".compute": "Shader", ".usf": "Shader", ".ush": "Shader",
    ".wgsl": "Shader", ".metal": "Shader",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell", ".ps1": "PowerShell", ".bat": "Batch",
    ".sql": "SQL", ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".sass": "SCSS", ".less": "Less", ".vue": "Vue", ".svelte": "Svelte", ".astro": "Astro",
    ".proto": "Protobuf", ".graphql": "GraphQL", ".gql": "GraphQL",
    ".tf": "Terraform", ".hcl": "HCL", ".nix": "Nix",
}
LANGUAGES = set(LANG_BY_EXT.values())
NON_CODE_LANGS = {
    ".json": "JSON", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML", ".xml": "XML",
    ".md": "Markdown", ".rst": "reStructuredText", ".txt": "Text", ".ini": "INI", ".cfg": "INI",
}
LINE_COMMENT = {
    "Python": ("#",), "Shell": ("#",), "Ruby": ("#",), "Perl": ("#",), "R": ("#",),
    "Elixir": ("#",), "Julia": ("#",), "Nim": ("#",), "GDScript": ("#",), "PowerShell": ("#",),
    "Terraform": ("#", "//"), "HCL": ("#", "//"), "Nix": ("#",), "SQL": ("--",), "Lua": ("--",),
    "Haskell": ("--",), "Erlang": ("%",), "Clojure": (";",), "Batch": ("REM", "::"), "Visual Basic": ("'",),
}
VENDOR_DIRS = {
    "node_modules", "vendor", "third_party", "thirdparty", "3rdparty", "bower_components", "Pods",
    "Carthage", "ThirdParty", ".venv", "venv", "site-packages", "__pycache__", ".git",
}
GENERATED_DIRS = {"dist", "build", "out", ".next", ".nuxt", "coverage", "generated", "gen", "target"}
GENERATED_SUFFIXES = (".min.js", ".min.css", ".bundle.js", ".pb.go", "_pb2.py", ".g.cs", ".designer.cs",
                      ".generated.cs", ".lock", "-lock.json", ".map", ".g.dart", ".freezed.dart")
GENERATED_MARKER = re.compile(r"(auto-?generated|do not edit|@generated|generated by)", re.I)

TEST_PATTERNS = [
    re.compile(r"(^|/)(tests?|__tests__|spec|specs|testing|test_?suite|e2e)(/|$)", re.I),
    re.compile(r"(^|/)test_[^/]+\.py$"), re.compile(r"_test\.(py|go|rb|exs?|dart)$"),
    re.compile(r"\.(test|spec)\.[cm]?[jt]sx?$"), re.compile(r"(Test|Tests|Spec)\.(java|kt|cs|swift|scala|php)$"),
    re.compile(r"(^|/)(EditMode|PlayMode)Tests?(/|$)"), re.compile(r"_spec\.rb$"),
]
TEST_CASE_RX = re.compile(
    r"^\s*(?:async\s+)?def\s+test_\w+|^\s*(?:it|test|specify)\s*(?:\(|[\"'])|^func\s+Test\w+\s*\(|#\[test\]|\[(?:Test|Fact|Theory|UnityTest)\]|@Test\b",
    re.M)
MOCK_RX = re.compile(r"\b(mock|Mock|MagicMock|patch\(|jest\.fn|vi\.fn|sinon\.|stub\(|spyOn|Substitute\.For|Mockito)")
ASSERT_RX = re.compile(r"\b(assert\w*|expect\s*\(|Assert\.\w+|should\.|t\.(?:Error|Fatal)\w*|require\.\w+)")
LLM_MARKER_RX = re.compile(r"(as an ai (?:language )?model|here(?:'s| is) the (?:updated|complete|full) (?:code|version)|"
                           r"i hope this helps|certainly! here|your code here|replace with your actual)", re.I)
HEALTH_RX = re.compile(r"[\"'/](healthz?|readyz|livez|health-check|status)[\"'/]", re.I)
METRICS_RX = re.compile(r"(prometheus|opentelemetry|statsd|micrometer|metrics\.(?:counter|gauge|histogram))", re.I)

FRAMEWORKS = {
    "react": r"\"react\"", "next": r"\"next\"", "vue": r"\"vue\"", "nuxt": r"\"nuxt\"", "svelte": r"\"svelte\"",
    "angular": r"@angular/core", "react-native": r"\"react-native\"", "electron": r"\"electron\"",
    "express": r"\"express\"", "fastify": r"\"fastify\"", "nestjs": r"@nestjs/core", "django": r"\bdjango\b",
    "flask": r"\bflask\b", "fastapi": r"\bfastapi\b", "rails": r"\brails\b", "spring": r"spring-boot",
    "laravel": r"laravel/framework", "gin": r"gin-gonic/gin", "echo": r"labstack/echo", "actix": r"\bactix-web\b",
    "axum": r"\baxum\b", "pytorch": r"\btorch\b", "tensorflow": r"\btensorflow\b", "jax": r"\bjax\b",
    "scikit-learn": r"scikit-learn|sklearn", "pandas": r"\bpandas\b", "spark": r"pyspark|spark-core",
    "airflow": r"apache-airflow", "dbt": r"dbt-core", "flutter": r"sdk:\s*flutter", "phaser": r"\"phaser\"",
    "three.js": r"\"three\"", "babylon.js": r"@babylonjs", "pygame": r"\bpygame\b", "bevy": r"\bbevy\b",
}
LOGGING = {"winston", "pino", "bunyan", "loguru", "structlog", "logrus", "zap", "zerolog", "serilog", "nlog", "log4j",
           "slf4j", "logback", "tracing"}
ERROR_TRACKING = {"sentry", "bugsnag", "rollbar", "honeybadger", "datadog", "newrelic", "raygun", "crashlytics"}
CI_MARKERS = {".github/workflows": "github-actions", ".gitlab-ci.yml": "gitlab-ci", "Jenkinsfile": "jenkins",
              ".circleci": "circleci", "azure-pipelines.yml": "azure-pipelines", "bitbucket-pipelines.yml": "bitbucket",
              ".travis.yml": "travis", ".buildkite": "buildkite", "codemagic.yaml": "codemagic", "appveyor.yml": "appveyor"}
MANIFESTS = {
    "package.json": "npm", "pnpm-lock.yaml": "pnpm", "yarn.lock": "yarn", "bun.lockb": "bun",
    "pyproject.toml": "python", "setup.py": "python", "requirements.txt": "python", "Pipfile": "python",
    "go.mod": "go", "Cargo.toml": "cargo", "pom.xml": "maven", "build.gradle": "gradle",
    "build.gradle.kts": "gradle", "CMakeLists.txt": "cmake", "Makefile": "make", "meson.build": "meson",
    "Gemfile": "bundler", "composer.json": "composer", "pubspec.yaml": "flutter", "mix.exs": "mix",
    "project.godot": "godot", "Dockerfile": "docker", "docker-compose.yml": "docker-compose",
    "compose.yaml": "docker-compose",
}
LOCKFILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock", "Cargo.lock", "go.sum",
             "Gemfile.lock", "composer.lock", "uv.lock", "packages.lock.json", "bun.lockb", "pubspec.lock"}
LINT_FILES = {".eslintrc": "eslint", ".eslintrc.js": "eslint", ".eslintrc.json": "eslint", ".eslintrc.cjs": "eslint",
              "eslint.config.js": "eslint", "eslint.config.mjs": "eslint", ".prettierrc": "prettier",
              ".prettierrc.json": "prettier", "prettier.config.js": "prettier", "ruff.toml": "ruff", ".ruff.toml": "ruff",
              ".flake8": "flake8", ".pylintrc": "pylint", "mypy.ini": "mypy", ".golangci.yml": "golangci-lint",
              ".golangci.yaml": "golangci-lint", "rustfmt.toml": "rustfmt", "clippy.toml": "clippy",
              ".stylelintrc": "stylelint", ".editorconfig": "editorconfig", ".clang-format": "clang-format",
              ".clang-tidy": "clang-tidy", ".rubocop.yml": "rubocop", "checkstyle.xml": "checkstyle",
              "biome.json": "biome", ".swiftlint.yml": "swiftlint", "tsconfig.json": "typescript"}
CONVENTIONAL_RX = re.compile(r"^(feat|fix|chore|docs|refactor|test|tests|perf|build|ci|style|revert)(\([^)]*\))?!?:")
SEED_RX = re.compile(r"(^|/)(seeds?|seeders?)(/|$)|(seed|seeder|sample[-_]?data|demo[-_]?data|fixtures?[-_]data)[^/]*\.(sql|json|csv|ya?ml|js|ts|py|rb|php)$", re.I)


def is_test_path(p: str) -> bool:
    return any(rx.search(p) for rx in TEST_PATTERNS)


def vendor_kind(p: str) -> str | None:
    parts = p.split("/")[:-1]
    if any(part in VENDOR_DIRS for part in parts):
        return "third_party"
    if any(part in GENERATED_DIRS for part in parts) or p.endswith(GENERATED_SUFFIXES):
        return "generated"
    return None


FAMILIES = {
    "image": {".png", ".jpg", ".jpeg", ".gif", ".tga", ".tif", ".tiff", ".bmp", ".psd", ".exr", ".hdr", ".webp", ".svg", ".dds", ".ktx"},
    "3d": {".fbx", ".obj", ".blend", ".gltf", ".glb", ".dae", ".3ds", ".max", ".ma", ".mb", ".stl", ".usd", ".usdz"},
    "audio": {".wav", ".mp3", ".ogg", ".flac", ".aif", ".aiff", ".bank", ".wem"},
    "video": {".mp4", ".mov", ".webm", ".avi", ".mkv"},
    "engine_asset": {".unity", ".prefab", ".mat", ".asset", ".anim", ".controller", ".meta", ".uasset", ".umap", ".tscn", ".tres", ".scn"},
    "font": {".ttf", ".otf", ".woff", ".woff2"},
    "archive": {".zip", ".7z", ".rar", ".tar", ".gz", ".unitypackage"},
    "document": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv"},
    "notebook": {".ipynb"},
}


def family_of(ext: str) -> str:
    for fam, exts in FAMILIES.items():
        if ext in exts:
            return fam
    return "other"


# --------------------------------------------------------------------------
# stage: git history
# --------------------------------------------------------------------------

BOT_RX = re.compile(r"(\[bot\]|^bot@|-bot@|^dependabot|^renovate|github-actions|gitlab-ci|^noreply@.*\bci\b|snyk-bot|^greenkeeper|^mergify|^pre-commit-ci)", re.I)
# Fix detection: a fix verb after any tracker prefix ("Bug 123 -", "[ABC-12]", "#45:") is stripped,
# and the diff must corroborate it (lines deleted in first-party source). "Bug 123 - add X" is not a fix.
TRACKER_PREFIX_RX = re.compile(r"^\s*(?:\[[^\]]{1,40}\]\s*|(?:bug|issue|ticket|task)\s*#?\d+\s*[-:.]?\s*|[A-Z][A-Z0-9]{1,9}-\d+\s*[-:.]?\s*|#\d+\s*[-:.]?\s*)+", re.I)
FIX_RX = re.compile(r"\b(fix(?:es|ed|ing)?|bug ?fix(?:es)?|hot ?fix(?:es)?|crash(?:es|ed|ing)?|regression|resolve[sd]? (?:a |the )?(?:bug|crash|issue|leak)|patch(?:es|ed)? (?:a |the )?(?:bug|crash|leak|hole))\b", re.I)
FEAT_RX = re.compile(r"\b(feat|feature|add|adds|added|implement|implements|introduce|support)\b", re.I)
REFAC_RX = re.compile(r"\b(refactor|cleanup|clean up|rename|restructure|migrate|upgrade|deprecate)\b", re.I)
PERF_RX = re.compile(r"\b(perf|performance|optimi[sz]e|speed ?up|faster|cache)\b", re.I)
# Chores that are never mineable work, whatever files they touch.
NOISE_RX = re.compile(r"^\s*(merge\b|revert\b|wip\b|initial commit\b)|\b(bump|chore|typos?|whitespace|formatting|reformat|format code|lint(?:ing)? fix|prettier|"
                      r"version bump|release v?\d|changelog|update (?:the )?readme|update dependenc|dependency update|deps\b|"
                      r"auto-?generated|regenerate|sync(?:ed)? with upstream)\b", re.I)
# AI tools: known agent addresses match anywhere; product names match only the NAME part of an identity.
# A bare first name (Claude, Cursor, Gemini, Devin, Jules) is never enough on its own.
AI_EMAIL_RX = re.compile(
    r"^(noreply@anthropic\.com|claude@anthropic\.com|copilot@github\.com|cursoragent@cursor\.com|agent@cursor\.com|"
    r"codex@openai\.com|aider@aider\.chat|openhands@all-hands\.dev|"
    r"\d+\+(claude|copilot|copilot-swe-agent|devin-ai-integration|gemini-code-assist|google-labs-jules|openhands-agent|"
    r"sweep-ai|chatgpt-codex-connector|cursor)(\[bot\])?@users\.noreply\.github\.com)$", re.I)
AI_NAME_RX = re.compile(
    r"^(claude (code|opus|sonnet|haiku|\d)[\w .-]*|claude\[bot\]|github copilot|copilot|copilot-swe-agent(\[bot\])?|"
    r"cursor agent|cursoragent|cursor-agent|openai codex|codex( cli| agent)?|chatgpt( codex)?|chatgpt-codex-connector(\[bot\])?|"
    r"gemini code assist|gemini-code-assist(\[bot\])?|google-labs-jules(\[bot\])?|devin ai|devin-ai-integration(\[bot\])?|"
    r"aider( \([^)]*\))?|openhands(-agent)?|sweep(-ai)?(\[bot\])?|windsurf( agent)?|codeium)$", re.I)
IDENT_RX = re.compile(r"^\s*(.*?)\s*<([^>]*)>\s*$")
PR_SUBJECT_RX = re.compile(r"^Merge pull request #(\d+)\b|^Merged in .*\(pull request #(\d+)\)")
PR_SQUASH_RX = re.compile(r"\(#(\d+)\)\s*$")
MR_BODY_RX = re.compile(r"See merge request \S+!(\d+)")


def ai_identity(name: str, email: str) -> bool:
    return bool(AI_EMAIL_RX.match((email or "").strip()) or AI_NAME_RX.match((name or "").strip()))


def ai_trailer(value: str) -> bool:
    for part in (value or "").split("\x1d"):
        m = IDENT_RX.match(part)
        name, email = (m.group(1), m.group(2)) if m else (part, "")
        if ai_identity(name, email):
            return True
    return False


def commit_kind(subject: str, impl_deleted: int = 0, impl_files: int = 0) -> str:
    """Strict kinds. A fix needs a fix verb (after tracker prefixes) AND deleted lines in first-party source."""
    s = TRACKER_PREFIX_RX.sub("", subject or "")
    if FIX_RX.search(s) and impl_files and impl_deleted > 0:
        return "bugfix"
    if PERF_RX.search(s) or REFAC_RX.search(s):
        return "restructure"
    if FEAT_RX.search(s):
        return "feature"
    return "exploratory"


# Capacity: one mineable commit = non-merge, non-bot, not a chore, 2+ first-party non-test source files,
# 20-8,000 lines changed in first-party source, at most 40 files and 5 directories.
CAPACITY_RULE = {"min_impl_files": 2, "max_files": 40, "max_dirs": 5, "min_churn": 20, "max_churn": 8000}
TASK_RULE = {"max_files": 30, "max_dirs": 4, "min_churn": 10, "max_churn": 8000}


def repo_state(repo: Path) -> dict:
    """is_git, has_commits, subtree (repo is a folder inside a larger work tree)."""
    st = {"is_git": False, "has_commits": False, "subtree": False}
    try:
        git(repo, "rev-parse", "--git-dir")
    except (RuntimeError, OSError):
        return st
    st["is_git"] = True
    try:
        top = Path(git(repo, "rev-parse", "--show-toplevel").strip()).resolve()
        st["subtree"] = top != repo.resolve()
    except RuntimeError:
        pass
    try:
        git(repo, "rev-parse", "--verify", "-q", "HEAD^{commit}")
        st["has_commits"] = True
    except RuntimeError:
        pass
    return st


def choose_ref(repo: Path, policy: str, pathspec: list, max_refs: int = 200) -> dict:
    """Find where the real history lives. Counts commits on HEAD and on every local and remote branch
    (no names are kept). policy auto measures the deepest branch when it is clearly deeper than HEAD."""
    def count(rev):
        try:
            return int(git(repo, "rev-list", "--count", rev, *pathspec).strip() or 0)
        except RuntimeError:
            return 0
    head = count("HEAD")
    out = {"policy": policy, "refs_considered": 0, "head_commits": head, "deepest_ref_commits": head,
           "measured": "head", "measured_commits": head, "_rev": ["HEAD"]}
    if policy == "all":
        out.update(measured="all_refs", _rev=["--all"], measured_commits=count("--all"))
        return out
    try:
        raw = git(repo, "for-each-ref", "--sort=-committerdate", "--format=%(objectname) %(objecttype)", "refs/heads", "refs/remotes")
    except RuntimeError:
        raw = ""
    shas = []
    for line in raw.splitlines():
        sha, _, typ = line.partition(" ")
        if typ == "commit" and sha not in shas:
            shas.append(sha)
    shas = shas[:max_refs]
    out["refs_considered"] = len(shas)
    best_sha, best = None, head
    for sha in shas:
        n = count(sha)
        if n > best:
            best_sha, best = sha, n
    out["deepest_ref_commits"] = best
    out["deeper_ref_exists"] = best_sha is not None and best >= head * 1.25 and best - head >= 10
    if policy == "auto" and out["deeper_ref_exists"]:
        out.update(measured="deepest_branch", measured_commits=best, _rev=[best_sha])
    return out


HISTORY_KEYS_KEPT_WHEN_SHALLOW = {"commits", "merge_commits", "non_merge_commits", "last_commit", "recency_days", "tags",
                                  "semver_tags", "branches", "history_shallow", "history_single_commit", "history_complete",
                                  "history_note"}


def null_history(g: dict, note: str) -> dict:
    """Shallow clones: history-derived counts and shares would describe a truncated history, so they are nulled."""
    for k in list(g):
        if k.startswith("_") or k in HISTORY_KEYS_KEPT_WHEN_SHALLOW:
            continue
        g[k] = None
    g["history_complete"] = False
    g["history_note"] = note
    g["_task_signals"] = None
    return g


def empty_history(reason: str) -> dict:
    keys = ("merge_commits", "non_merge_commits", "authors_human", "authors_bot", "bot_commits", "authors_with_10plus_commits",
            "top1_author_share", "top5_author_share", "first_commit", "last_commit", "span_days", "recency_days", "active_days",
            "active_months", "commits_by_year", "commits_by_month", "pre_cutoff_commits", "pre_cutoff_share",
            "commits_last_12_months", "tags", "semver_tags", "branches", "root_commit_count", "history_shallow",
            "conventional_commit_share_last_200", "pr_merged_count", "ai_coauthored_commits", "ai_agent_commits", "ai_assisted_share")
    g = {k: None for k in keys}
    g.update(commits=0 if reason == "no_commits" else None, history_single_commit=False, history_complete=False,
             history_note=reason, _root_commit_files=None, _fingerprints={"root_commits": [], "commit_sketch": []},
             _task_signals=None)
    return g


def count_prs(repo: Path, rev: list, pathspec: list):
    """Merged PRs on first-parent history only. A trailing (#N) counts only when the hosting service committed it."""
    sep, rec = "\x1f", "\x1e"
    try:
        raw = git(repo, "log", "--first-parent", f"--format={rec}%s{sep}%cn{sep}%ce{sep}%b", *rev, *pathspec)
    except RuntimeError:
        return None
    prs = set()
    for block in raw.split(rec):
        f = block.split(sep)
        if len(f) < 4:
            continue
        subject, cname, cemail, body = f[0], f[1], f[2].strip().lower(), f[3]
        m = PR_SUBJECT_RX.search(subject)
        if m:
            prs.add(next(x for x in m.groups() if x))
            continue
        m = PR_SQUASH_RX.search(subject)
        if m and (cemail == "noreply@github.com" or cname.strip() == "GitHub"):
            prs.add(m.group(1))
            continue
        m = MR_BODY_RX.search(body)
        if m:
            prs.add("mr" + m.group(1))
    return len(prs)


def stage_git(repo: Path, cutoff: str, rev: list, with_files: bool, pathspec: list) -> dict:
    """with_files=False is the simple-mode path: no per-commit line counts, so no task census or capacity."""
    sep, rec = "\x1f", "\x1e"
    fmt = rec + sep.join(["%H", "%P", "%ae", "%an", "%at", "%ct", "%ce", "%cn", "%s",
                          "%(trailers:key=Co-authored-by,valueonly,separator=%x1d)"])
    args = ["log", f"--format={fmt}", "--date-order"]
    if with_files:
        args += ["--numstat", "--no-renames"]
    raw = git(repo, *args, *rev, *pathspec)
    cutoff_ts = dt.datetime.strptime(cutoff, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
    commits = merges = bot_commits = pre_cutoff = 0
    roots, shas, root_files = [], [], 0
    per_author = Counter()
    bot_authors = set()
    by_year, by_month = Counter(), Counter()
    days = set()
    first_ts = last_ts = None
    task = Counter()
    ai_coauthored = ai_agent = 0
    conventional = recent_subjects = 0
    for block in raw.split(rec):
        if not block.strip():
            continue
        head, _, files_blob = block.partition("\n")
        fields = head.split(sep)
        if len(fields) < 10:
            continue
        sha, parents, email, name, at, ct, cemail, cname, subject, trailers = fields[:10]
        commits += 1
        shas.append(sha)
        plist = parents.split()
        changes = []
        if with_files:
            for line in files_blob.splitlines():
                parts = line.split("\t", 2)
                if len(parts) == 3:
                    a_, d_, path = parts
                    changes.append((path, int(a_) if a_.isdigit() else 0, int(d_) if d_.isdigit() else 0))
        if not plist:
            roots.append(sha)
            root_files += len(changes)
        is_merge = len(plist) > 1
        merges += is_merge
        ts = float(at or ct or 0)
        first_ts = ts if first_ts is None else min(first_ts, ts)
        last_ts = ts if last_ts is None else max(last_ts, ts)
        d = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc)
        by_year[str(d.year)] += 1
        by_month[d.strftime("%Y-%m")] += 1
        days.add(d.strftime("%Y-%m-%d"))
        pre_cutoff += ts < cutoff_ts
        if trailers and ai_trailer(trailers):
            ai_coauthored += 1
        if ai_identity(name, email) or ai_identity(cname, cemail):
            ai_agent += 1
        if not is_merge and recent_subjects < 200:
            recent_subjects += 1
            conventional += bool(CONVENTIONAL_RX.match(subject))
        if BOT_RX.search(email) or BOT_RX.search(name):
            bot_commits += 1
            bot_authors.add(h(email.lower()))
            continue
        per_author[h(email.strip().lower() or name.strip().lower())] += 1
        if is_merge or not with_files:
            continue
        src = [c for c in changes if Path(c[0]).suffix.lower() in LANG_BY_EXT and not vendor_kind(c[0])]
        tests = [c for c in src if is_test_path(c[0])]
        impl = [c for c in src if not is_test_path(c[0])]
        churn = sum(c[1] + c[2] for c in src)
        dirs = len({str(Path(c[0]).parent) for c in src})
        noise = bool(NOISE_RX.search(subject))
        kind = commit_kind(subject, sum(c[2] for c in impl), len(impl))
        task[f"{kind}_commits"] += 1
        task["lines_changed_first_party"] += churn
        if FIX_RX.search(TRACKER_PREFIX_RX.sub("", subject)) and kind != "bugfix":
            task["fix_subject_not_corroborated"] += 1
        if noise:
            task["chore_commits"] += 1
            continue
        if len(tests) >= 5 and len(impl) < 3:
            task["bulk_test_commits"] += 1
        cr = CAPACITY_RULE
        if (len(impl) >= cr["min_impl_files"] and len(src) <= cr["max_files"] and dirs <= cr["max_dirs"]
                and cr["min_churn"] <= churn <= cr["max_churn"]):
            task["mineable_commits"] += 1
            task[f"{kind}_mineable"] += 1
        elif len(src) > cr["max_files"] or dirs > cr["max_dirs"]:
            task["too_spread_commits"] += 1
        tr = TASK_RULE
        in_band = impl and len(src) <= tr["max_files"] and dirs <= tr["max_dirs"] and tr["min_churn"] <= churn <= tr["max_churn"]
        if in_band and tests:
            task["with_tests_total"] += 1
            task[f"{kind}_with_tests"] += 1
            if kind == "bugfix":
                task["bugfix_with_regression_test"] += 1
        elif in_band:
            task[f"{kind}_source_only"] += 1
    counts = sorted(per_author.values(), reverse=True)
    total_h = sum(counts) or 1
    try:
        shallow = git(repo, "rev-parse", "--is-shallow-repository").strip() == "true"
    except RuntimeError:
        shallow = None
    try:
        tag_names = [t for t in git(repo, "tag", "--list").splitlines() if t.strip()]
        branches = len([b for b in git(repo, "branch", "--all", "--format=%(refname)").splitlines() if b.strip()])
        since = (dt.datetime.fromtimestamp(last_ts or time.time(), tz=dt.timezone.utc) - dt.timedelta(days=365)).timestamp()
        last12 = int(git(repo, "rev-list", "--count", f"--since={int(since)}", *rev, *pathspec).strip() or 0)
    except RuntimeError:
        tag_names, branches, last12 = [], None, None
    today = dt.datetime.now(dt.timezone.utc)
    single = commits == 1
    g = {
        "commits": commits, "merge_commits": merges, "non_merge_commits": commits - merges,
        "authors_human": len(counts), "authors_bot": len(bot_authors), "bot_commits": bot_commits,
        "authors_with_10plus_commits": sum(1 for c in counts if c >= 10),
        "top1_author_share": round(counts[0] / total_h, 3) if counts else None,
        "top5_author_share": round(sum(counts[:5]) / total_h, 3) if counts else None,
        "first_commit": iso_day(first_ts), "last_commit": iso_day(last_ts),
        "span_days": int((last_ts - first_ts) / 86400) if commits else 0,
        "recency_days": int((today.timestamp() - last_ts) / 86400) if last_ts else None,
        "active_days": len(days), "active_months": len(by_month),
        "commits_by_year": dict(sorted(by_year.items())), "commits_by_month": dict(sorted(by_month.items())),
        "pre_cutoff_commits": pre_cutoff, "pre_cutoff_share": round(pre_cutoff / commits, 3) if commits and not single else None,
        "commits_last_12_months": last12, "tags": len(tag_names),
        "semver_tags": sum(1 for t in tag_names if re.match(r"^v?\d+\.\d+(\.\d+)?", t)), "branches": branches,
        "root_commit_count": len(roots), "history_shallow": shallow, "history_single_commit": single,
        "history_complete": not shallow and not single, "history_note": "single-commit snapshot" if single else None,
        "conventional_commit_share_last_200": round(conventional / recent_subjects, 3) if recent_subjects else None,
        "pr_merged_count": count_prs(repo, rev, pathspec),
        "ai_coauthored_commits": ai_coauthored, "ai_agent_commits": ai_agent,
        "ai_assisted_share": round((ai_coauthored + ai_agent) / commits, 3) if commits and not single else None,
        "_root_commit_files": root_files if with_files else None,
        "_fingerprints": {"root_commits": sorted(h("root:" + r) for r in roots), "commit_sketch": bottom_k(shas)},
        "_task_signals": dict(task) if with_files else None,
    }
    if shallow:
        null_history(g, "shallow clone: history is truncated, so history-derived counts and shares are not reported")
    return g


# --------------------------------------------------------------------------
# stage: tree (every file in full mode, a sample in simple mode)
# --------------------------------------------------------------------------


def list_files(repo: Path) -> list:
    """Tracked files with blob ids and sizes."""
    try:
        raw = git(repo, "ls-files", "-s", "-z")
    except RuntimeError:
        raw = ""
    out = []
    for entry in raw.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        parts = meta.split()
        if len(parts) >= 2 and parts[0] != "160000":
            out.append((path, parts[1]))
    if not out:
        for root, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in VENDOR_DIRS and not d.startswith(".")]
            for f in files:
                out.append((Path(root, f).relative_to(repo).as_posix(), ""))
    return out


BRANCH_RX = re.compile(r"\b(if|elif|else if|for|foreach|while|case|catch|except|switch|match)\b|&&|\|\||\?\?")
FUNC_RX = {
    "Python": re.compile(r"^\s*(async\s+)?def\s+\w+", re.M),
    "GDScript": re.compile(r"^\s*func\s+\w+", re.M),
    "Go": re.compile(r"^func\s", re.M),
    "Rust": re.compile(r"\bfn\s+\w+"),
    "Ruby": re.compile(r"^\s*def\s+", re.M),
    "Lua": re.compile(r"\bfunction\b"),
    "JavaScript": re.compile(r"\bfunction\b|=>|^\s*(async\s+)?\w+\s*\([^)]*\)\s*\{", re.M),
    "TypeScript": re.compile(r"\bfunction\b|=>|^\s*(public|private|protected|static|async|\s)*\w+\s*\([^)]*\)\s*(:\s*[\w<>\[\], |]+)?\s*\{", re.M),
    "_default": re.compile(r"^\s*(?:(?:public|private|protected|internal|static|virtual|override|async|final|inline|const|unsafe|extern)\s+)*[\w:<>\[\],*&]+\s+\**\w+\s*\([^;{)]*\)\s*(?:const\s*)?(?:->\s*[\w:<>]+\s*)?\{?\s*$", re.M),
}


def read_text(path: Path, limit: int = 4_000_000) -> str | None:
    try:
        with path.open("rb") as f:
            data = f.read(limit)
    except OSError:
        return None
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", "replace")


def analyse_file(text: str, lang: str) -> dict:
    prefixes = LINE_COMMENT.get(lang, ("//",))
    code = comment = blank = 0
    in_block = False
    indents = []
    unit = 0
    for line in text.splitlines():
        s = line.strip()
        if not s:
            blank += 1
            continue
        if in_block:
            comment += 1
            in_block = "*/" not in s
            continue
        if prefixes == ("//",) and s.startswith("/*"):
            comment += 1
            in_block = "*/" not in s
            continue
        if s.startswith(prefixes):
            comment += 1
            continue
        code += 1
        lead = len(line[: len(line) - len(line.lstrip(" \t"))].expandtabs(4))
        if lead and (unit == 0 or lead < unit):
            unit = max(2, lead)
        indents.append(lead)
    indents.sort()
    p98 = pct(indents, 0.98) or 0
    return {"code": code, "comment": comment, "blank": blank, "nesting": (p98 // unit) if unit else 0,
            "branches": len(BRANCH_RX.findall(text)), "functions": len(FUNC_RX.get(lang, FUNC_RX["_default"]).findall(text))}


def stage_tree(repo: Path, sample: int | None, seed: int = 7) -> dict:
    """sample=None reads every file; otherwise reads `sample` source files and scales by bytes."""
    files = list_files(repo)
    langs = defaultdict(Counter)
    other, families = Counter(), Counter()
    loc_split = Counter()
    blobs = []
    src_sizes, comment_ratios = [], []
    big500 = big1000 = deep = 0
    branches = functions = 0
    test_files = test_code = tests_declared = mocks = asserts = 0
    llm_markers = generated_markers = health = metrics = 0
    nestings = []
    top_dirs = set()
    seeds = notebooks = 0
    candidates = []
    for rel, blob in files:
        if blob:
            blobs.append(blob)
        ext = Path(rel).suffix.lower()
        if ext == ".ipynb":
            notebooks += 1
        if SEED_RX.search(rel) and not is_test_path(rel) and "migration" not in rel.lower():
            seeds += 1
        vk = vendor_kind(rel)
        lang = LANG_BY_EXT.get(ext)
        if lang is None:
            if ext in NON_CODE_LANGS:
                other[NON_CODE_LANGS[ext]] += 1
            else:
                families[family_of(ext)] += 1
            continue
        candidates.append((rel, lang, vk))
    sampled = candidates
    scale_note = None
    if sample is not None and len(candidates) > sample:
        rnd = random.Random(seed)
        sampled = rnd.sample(candidates, sample)
        scale_note = f"{sample} of {len(candidates)} source files read; totals scaled by file size"
    bytes_by_lang, sampled_bytes_by_lang = Counter(), Counter()
    if sample is not None:
        for rel, lang, vk in candidates:
            if vk is None:
                try:
                    bytes_by_lang[lang] += (repo / rel).stat().st_size
                except OSError:
                    pass
    for rel, lang, vk in sampled:
        text = read_text(repo / rel)
        if text is None:
            continue
        head = text[:600]
        if vk is None and GENERATED_MARKER.search(head):
            vk = "generated"
            generated_markers += 1
        a = analyse_file(text, lang)
        if vk:
            loc_split[vk] += a["code"]
            continue
        loc_split["first_party"] += a["code"]
        if sample is not None:
            sampled_bytes_by_lang[lang] += len(text.encode("utf-8", "replace"))
        L = langs[lang]
        L["files"] += 1
        L["code_lines"] += a["code"]
        L["comment_lines"] += a["comment"]
        L["blank_lines"] += a["blank"]
        if a["code"] + a["comment"]:
            comment_ratios.append(a["comment"] / (a["code"] + a["comment"]))
        if is_test_path(rel):
            test_files += 1
            test_code += a["code"]
            L["test_files"] += 1
            tests_declared += len(TEST_CASE_RX.findall(text))
            mocks += len(MOCK_RX.findall(text))
            asserts += len(ASSERT_RX.findall(text))
            continue
        src_sizes.append(a["code"])
        branches += a["branches"]
        functions += a["functions"]
        nestings.append(a["nesting"])
        deep += a["nesting"] >= 5
        big500 += a["code"] > 500
        big1000 += a["code"] > 1000
        llm_markers += len(LLM_MARKER_RX.findall(text))
        health += bool(HEALTH_RX.search(text))
        metrics += bool(METRICS_RX.search(text))
        if "/" in rel:
            top_dirs.add(h(rel.split("/")[0]))
    if sample is not None:  # scale sampled counts to the whole tree by bytes per language
        for lang, L in langs.items():
            f = bytes_by_lang[lang] / sampled_bytes_by_lang[lang] if sampled_bytes_by_lang[lang] else 1.0
            for k in list(L):
                L[k] = int(round(L[k] * f))
    src_sizes.sort()
    nestings.sort()
    code_total = sum(v["code_lines"] for v in langs.values())
    if sample is not None and loc_split["first_party"]:
        f_all = code_total / loc_split["first_party"]
        test_code = int(test_code * f_all)
        tests_declared = int(tests_declared * f_all)
    src_code = code_total - test_code
    sourcing = {
        "loc_first_party": code_total,
        "loc_third_party": loc_split["third_party"] if sample is None else None,
        "loc_generated": loc_split["generated"] if sample is None else None,
        "seed_data_files": seeds, "notebooks": notebooks,
    }
    return {
        "mode_note": scale_note,
        "languages": {k: dict(v) for k, v in sorted(langs.items(), key=lambda kv: -kv[1]["code_lines"])},
        "code_lines_total": code_total,
        "code_lines_definition": "non-blank lines that are not comments, tracked first-party files",
        "nonblank_lines_total": code_total + sum(v["comment_lines"] for v in langs.values()),
        "comment_lines_total": sum(v["comment_lines"] for v in langs.values()),
        "source_files": sum(v["files"] for v in langs.values()) - sum(v.get("test_files", 0) for v in langs.values()),
        "tracked_files": len(files),
        "vendored_or_generated_files_skipped": sum(1 for _, _, vk in candidates if vk),
        "config_and_docs_files": dict(other.most_common()),
        "other_files_by_family": dict(families.most_common()),
        "median_file_lines": pct(src_sizes, 0.5), "p90_file_lines": pct(src_sizes, 0.9),
        "files_over_500_lines": big500 if sample is None else None,
        "files_over_1000_lines": big1000 if sample is None else None,
        "jvm_dotnet_share": round(sum(langs[x]["code_lines"] for x in ("Java", "Kotlin", "Scala", "C#") if x in langs) / code_total, 3) if code_total else None,
        "_tests": {"test_files": sum(v.get("test_files", 0) for v in langs.values()), "test_code_lines": test_code,
                   "test_to_source_line_ratio": round(test_code / src_code, 3) if src_code else None,
                   "tests_declared": tests_declared,
                   "mock_calls": mocks if sample is None else None, "assertions": asserts if sample is None else None},
        "_logic": {"functions_estimate": functions, "branch_points": branches,
                   "branches_per_kloc": round(branches / (sum(src_sizes) / 1000), 1) if sum(src_sizes) else None,
                   "max_nesting_levels": pct(nestings, 0.95) or 0, "files_nesting_5plus": deep,
                   "files_nesting_5plus_share": round(deep / len(nestings), 3) if nestings else None,
                   "top_level_modules": len(top_dirs), "source_code_lines": src_code},
        "_authenticity": {"comment_ratio_median": round(pct(sorted(comment_ratios), 0.5), 3) if comment_ratios else None,
                          "llm_phrase_hits": llm_markers, "generated_marker_files": generated_markers},
        "_observability": {"health_endpoint": health > 0, "metrics": metrics > 0},
        "_sourcing": sourcing,
        "_content_sketch": bottom_k(blobs),
    }


# --------------------------------------------------------------------------
# stage: repository signals (docs, hygiene, CI, dependencies, frameworks)
# --------------------------------------------------------------------------


DEMO_PHRASES = {  # explicit statements in the README, not single words
    "generator_boilerplate": r"bootstrapped with \[?create[- ]react[- ]app|this template should help|generated by create-next-app|"
                             r"this project was generated with \[?angular cli",
    "template_or_starter": r"\bthis (?:is an? )?(?:project |repo(?:sitory)? )?(?:is )?(?:a |an )?(?:simple )?(?:template|starter|boilerplate)\b|"
                           r"\b(?:starter|template) (?:kit|project|repo(?:sitory)?) for\b|use this template",
    "tutorial_or_course": r"\b(?:follow(?:ing)? (?:along )?(?:with )?(?:the|this|a) tutorial|tutorial series|course project|"
                          r"homework|class assignment|bootcamp)\b",
    "hello_world": r"\bhello,? world\b",
}


def stage_signals(repo: Path) -> dict:
    names = [p for p, _ in list_files(repo)]
    base = Counter(Path(p).name for p in names)
    lower = {p.lower() for p in names}
    root_files = {p for p in names if "/" not in p}

    def read(p, limit=300_000):
        return read_text(repo / p, limit) or ""

    readme = next((p for p in sorted(root_files) if p.lower().startswith("readme")), None)
    readme_text = read(readme) if readme else ""
    manifests_text = ""
    for p in names:
        if Path(p).name in ("package.json", "pyproject.toml", "requirements.txt", "go.mod", "Cargo.toml", "pom.xml",
                            "build.gradle", "build.gradle.kts", "Gemfile", "composer.json", "pubspec.yaml", "Pipfile",
                            "setup.py", "setup.cfg", "manifest.json") and not vendor_kind(p):
            manifests_text += read(p, 200_000) + "\n"
    deps_runtime = deps_dev = 0
    for p in names:
        if Path(p).name == "package.json" and not vendor_kind(p):
            try:
                pkg = json.loads(read(p))
                deps_runtime += len(pkg.get("dependencies") or {})
                deps_dev += len(pkg.get("devDependencies") or {})
            except ValueError:
                pass
        elif Path(p).name == "requirements.txt" and not vendor_kind(p):
            deps_runtime += sum(1 for line in read(p).splitlines() if line.strip() and not line.startswith(("#", "-")))
        elif Path(p).name == "go.mod":
            deps_runtime += len(re.findall(r"^\s+[\w.\-/]+\s+v\d", read(p), re.M))
        elif Path(p).name == "Cargo.toml":
            m = re.search(r"\[dependencies\](.*?)(\n\[|\Z)", read(p), re.S)
            deps_runtime += len(re.findall(r"^\w[\w-]*\s*=", m.group(1), re.M)) if m else 0
    ci_files = [p for p in names if any(p == m or p.startswith(m + "/") for m in CI_MARKERS)]
    ci_text = "\n".join(read(p, 100_000) for p in ci_files[:50]).lower()
    ci = sorted({tool for m, tool in CI_MARKERS.items() if any(p == m or p.startswith(m + "/") for p in names)})
    lint = sorted({tool for f, tool in LINT_FILES.items() if base.get(f)})
    if re.search(r"\[tool\.(ruff|black|isort|mypy|pylint)\]", manifests_text):
        lint = sorted(set(lint) | set(re.findall(r"\[tool\.(ruff|black|isort|mypy|pylint)\]", manifests_text)))
    frameworks = sorted(k for k, rx in FRAMEWORKS.items() if re.search(rx, manifests_text, re.I))
    deps_lower = manifests_text.lower()
    return {
        "docs": {"readme": readme is not None, "readme_lines": len(readme_text.splitlines()) if readme else 0,
                 "readme_sections": len(re.findall(r"^#{1,3} ", readme_text, re.M)),
                 "changelog": any(p.lower().startswith(("changelog", "history.md", "changes")) for p in root_files),
                 "contributing": any(p.lower().startswith("contributing") for p in names),
                 "license": any(p.lower().startswith(("license", "licence", "copying")) for p in root_files),
                 "pr_template": any("pull_request_template" in p for p in lower),
                 "issue_templates": any("issue_template" in p for p in lower),
                 "docs_folder_files": sum(1 for p in names if p.lower().startswith("docs/"))},
        "environment": {"dockerfiles": sum(1 for p in names if Path(p).name.lower().startswith("dockerfile")),
                        "compose": any(Path(p).name in ("docker-compose.yml", "docker-compose.yaml", "compose.yaml", "compose.yml") for p in names),
                        "devcontainer": any(p.startswith(".devcontainer") for p in names),
                        "nix": any(Path(p).name in ("flake.nix", "shell.nix", "default.nix") for p in names),
                        "env_example": any(Path(p).name in (".env.example", ".env.sample", ".env.template", "env.example") for p in names),
                        "env_files_committed": sum(1 for p in names if Path(p).name in (".env", ".env.local", ".env.production"))},
        "hygiene": {"linters_and_formatters": lint, "dep_update_tooling": sorted(
                        {t for t, f in (("dependabot", ".github/dependabot.yml"), ("renovate", "renovate.json")) if f in names}
                        | ({"renovate"} if base.get(".renovaterc") or base.get(".renovaterc.json") else set())),
                    "coverage_tooling": sorted({t for t, f in (("codecov", "codecov.yml"), ("coveralls", ".coveralls.yml"),
                                                               ("nyc", ".nycrc"), ("coverage.py", ".coveragerc")) if base.get(f)}
                                               | ({"jest"} if "coveragethreshold" in deps_lower else set())),
                    "coverage_threshold_set": bool(re.search(r"coverageThreshold|fail_under|--cov-fail-under|minimum_coverage", manifests_text + ci_text, re.I))},
        "ci": {"systems": ci, "config_files": len(ci_files),
               "runs_tests": bool(re.search(r"\b(test|pytest|jest|vitest|go test|cargo test|rspec|phpunit|dotnet test|gradle\w* test|mvn\w* test)\b", ci_text)),
               "runs_lint": bool(re.search(r"\b(lint|eslint|ruff|flake8|golangci|clippy|rubocop|prettier --check)\b", ci_text)),
               "runs_typecheck": bool(re.search(r"\b(tsc|mypy|pyright|typecheck|type-check)\b", ci_text)),
               "deploys": bool(re.search(r"\b(deploy|publish|release)\b", ci_text)),
               "dependency_audit": bool(re.search(r"(npm audit|pip-audit|safety check|snyk|trivy|osv-scanner|cargo audit|govulncheck)", ci_text))},
        "dependencies": {"package_managers": sorted({t for f, t in MANIFESTS.items() if base.get(f) and t not in ("docker", "docker-compose", "make", "cmake")}),
                         "manifests": sum(1 for p in names if Path(p).name in MANIFESTS and not vendor_kind(p)),
                         "lockfiles": sorted(f for f in base if f in LOCKFILES),
                         "direct_runtime": deps_runtime, "direct_dev": deps_dev},
        "frameworks": frameworks,
        "observability": {"logging": sorted(x for x in LOGGING if re.search(rf"[\"'\s/]{re.escape(x)}[\"'@\s=<>~^]", deps_lower)),
                          "error_tracking": sorted(x for x in ERROR_TRACKING if x in deps_lower)},
        "iac": {"terraform_files": sum(1 for p in names if p.endswith(".tf")),
                "kubernetes_manifests": sum(1 for p in names if p.endswith((".yaml", ".yml")) and re.search(r"(^|/)(k8s|kubernetes|manifests|deploy)(/|$)", p)),
                "helm_charts": sum(1 for p in names if Path(p).name == "Chart.yaml"),
                "ansible": any(re.search(r"(^|/)(playbooks?|roles)/", p) for p in names),
                "pulumi": any(Path(p).name.startswith("Pulumi.") for p in names),
                "cloudformation": sum(1 for p in names if re.search(r"(cloudformation|cfn).*\.(ya?ml|json)$", p, re.I))},
        "workspaces": {"monorepo_tooling": sorted({t for t, f in (("pnpm", "pnpm-workspace.yaml"), ("lerna", "lerna.json"), ("nx", "nx.json"),
                                                                ("turbo", "turbo.json"), ("rush", "rush.json"), ("go-work", "go.work")) if f in names}
                                                  | ({"npm-workspaces"} if '"workspaces"' in manifests_text else set())),
                       "package_roots": len({str(Path(p).parent) for p in names if Path(p).name in ("package.json", "pyproject.toml", "go.mod", "Cargo.toml", "pom.xml") and not vendor_kind(p)})},
        "demo_words": {k: True for k, rx in DEMO_PHRASES.items() if re.search(rx, readme_text, re.I)},
        "scaffolds": scaffold_hits(names),
    }


# Test runners only (never app frameworks): (name, manifest regex, config file names)
TEST_RUNNERS = [
    ("pytest", r"\bpytest\b", ("pytest.ini", "conftest.py")),
    ("jest", r"[\"']jest[\"']\s*:", ("jest.config.js", "jest.config.ts", "jest.config.cjs", "jest.config.mjs")),
    ("vitest", r"[\"']vitest[\"']\s*:", ("vitest.config.ts", "vitest.config.js", "vitest.config.mts")),
    ("mocha", r"[\"']mocha[\"']\s*:", (".mocharc.json", ".mocharc.js", ".mocharc.yml")),
    ("jasmine", r"[\"']jasmine[\"']\s*:", ("jasmine.json",)),
    ("karma", r"[\"']karma[\"']\s*:", ("karma.conf.js",)),
    ("playwright", r"@playwright/test", ("playwright.config.ts", "playwright.config.js")),
    ("cypress", r"[\"']cypress[\"']\s*:", ("cypress.config.ts", "cypress.config.js", "cypress.json")),
    ("junit", r"junit-jupiter|<artifactId>junit</artifactId>|junit:junit|org\.junit", ()),
    ("testng", r"\btestng\b", ("testng.xml",)),
    ("nunit", r"\bNUnit\b", ()),
    ("xunit", r"\bxunit\b", ()),
    ("mstest", r"MSTest\.TestFramework", ()),
    ("unity-test-framework", r"com\.unity\.test-framework", ()),
    ("phpunit", r"phpunit/phpunit", ("phpunit.xml", "phpunit.xml.dist")),
    ("rspec", r"\brspec\b", (".rspec",)),
    ("minitest", r"\bminitest\b", ()),
    ("gut", r"", ("addons/gut/plugin.cfg",)),
    ("googletest", r"\bgtest\b|GTest::|googletest", ()),
    ("catch2", r"\bCatch2\b", ()),
    ("flutter_test", r"flutter_test:", ()),
]


def detect_test_frameworks(repo: Path, names: list) -> list:
    """Test tools named by manifests, config files or test-file conventions. App frameworks never appear here."""
    first_party = [p for p in names if not vendor_kind(p)]
    base = {Path(p).name for p in first_party}
    text = ""
    for p in first_party:
        n = Path(p).name
        if n in ("package.json", "pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.cfg", "setup.py", "tox.ini",
                 "Pipfile", "pom.xml", "build.gradle", "build.gradle.kts", "Gemfile", "composer.json", "pubspec.yaml",
                 "CMakeLists.txt", "manifest.json", "packages.config") or n.endswith(".csproj"):
            text += (read_text(repo / p, 200_000) or "") + "\n"
    found = set()
    for name, rx, cfgs in TEST_RUNNERS:
        if (rx and re.search(rx, text)) or any(c in base or c in first_party for c in cfgs):
            found.add(name)
    tests = [p for p in first_party if is_test_path(p)]
    if any(p.endswith("_test.go") for p in tests):
        found.add("go-test")
    if "Cargo.toml" in base and (any(p.endswith(".rs") for p in tests) or "#[test]" in "".join(
            (read_text(repo / p, 100_000) or "") for p in first_party if p.endswith(".rs"))[:5_000_000]):
        found.add("cargo-test")
    if "mix.exs" in base and any(p.endswith("_test.exs") for p in tests):
        found.add("exunit")
    py_tests = [p for p in tests if p.endswith(".py")]
    if py_tests and "pytest" not in found:
        if any(re.search(r"^\s*(import unittest|from unittest)", read_text(repo / p, 50_000) or "", re.M) for p in py_tests[:50]):
            found.add("unittest")
    return sorted(found)


# --------------------------------------------------------------------------
# stage: build probe
# --------------------------------------------------------------------------

OFFLINE_ENV = {"CI": "1", "GOFLAGS": "-mod=mod", "GOPROXY": "off", "CARGO_NET_OFFLINE": "true", "npm_config_offline": "true",
               "PIP_NO_INDEX": "1", "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "NUGET_PACKAGES": os.environ.get("NUGET_PACKAGES", "")}
CREDENTIAL_ENV_RX = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|ACCESS_KEY|PRIVATE_KEY|CREDENTIAL|^AWS_|^GH_|^GITHUB_|^NPM_|^PYPI_|^AZURE_|^GOOGLE_APPLICATION)", re.I)
RUNNER_ORDER = ("npm", "python", "go", "cargo", "dotnet", "maven", "gradle")
RUNNER_MANIFESTS = {"package.json": "npm", "pyproject.toml": "python", "setup.py": "python", "requirements.txt": "python",
                    "go.mod": "go", "Cargo.toml": "cargo", "pom.xml": "maven", "build.gradle": "gradle", "build.gradle.kts": "gradle"}
BUILD_CMDS = {  # (build, test, list tests)
    "npm": (["npm", "run", "build", "--if-present"], ["npm", "test", "--silent"], None),
    "python": (["{py}", "-m", "compileall", "-q", "-x", r"[/\\](tests?|testing|test_?data|fixtures?)[/\\]", "."], ["{py}", "-m", "pytest", "-q", "-p", "no:cacheprovider"],
               ["{py}", "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"]),
    "go": (["go", "build", "./..."], ["go", "test", "-v", "./..."], ["go", "test", "-list", ".", "./..."]),
    "cargo": (["cargo", "build", "--offline"], ["cargo", "test", "--offline"], ["cargo", "test", "--offline", "--", "--list"]),
    "maven": (["mvn", "-o", "-q", "compile"], ["mvn", "-o", "test"], None),
    "gradle": (["./gradlew", "--offline", "assemble"], ["./gradlew", "--offline", "test"], None),
    "dotnet": (["dotnet", "build", "--no-restore"], ["dotnet", "test", "--no-restore", "--no-build"],
               ["dotnet", "test", "--no-restore", "--no-build", "--list-tests"]),
}
RESULT_PARSERS = [  # (passed, failed) from a test runner's summary, counts only
    (re.compile(r"(\d+) passed"), re.compile(r"(\d+) failed")),                                  # pytest, jest, vitest
    (re.compile(r"test result: \w+\. (\d+) passed"), re.compile(r"; (\d+) failed")),            # cargo
    (re.compile(r"Passed:\s+(\d+)"), re.compile(r"Failed:\s+(\d+)")),                             # dotnet
]
# Failure attribution. ENVIRONMENT = this machine lacks a tool, package, SDK or network; the repo is not blamed.
ENV_FAILURE_RX = re.compile(
    r"(command not found|is not recognized as an internal|No module named|ModuleNotFoundError|Cannot find module|MODULE_NOT_FOUND|"
    r"ERR_MODULE_NOT_FOUND|ENOTFOUND|EAI_AGAIN|ECONNREFUSED|getaddrinfo|network is unreachable|could not resolve host|"
    r"module lookup disabled|GOPROXY=off|cannot find module providing|no required module provides|missing go\.sum entry|"
    r"no matching package named|failed to download|attempting to make an HTTP request, but --offline|Could not resolve dependencies|"
    r"Could not resolve all|Cannot access .* in offline mode|JAVA_HOME|No Java runtime|Unable to locate a Java|"
    r"compatible \.NET SDK was not found|Unable to find package|NU1101|NU1301|assets file .* not found|pkg-config|"
    r"EBADENGINE|Unsupported engine|requires a different Python|xcrun: error|cannot open shared object file|"
    r"fatal error: [^\n]*: No such file or directory|ImportError: lib|sh: \S+: not found|env: \S+: No such file|"
    r"dyld\[\d+\]: |dyld: Library not loaded|Bad CPU type in executable)", re.I)
REPO_FAILURE_RX = re.compile(
    r"(SyntaxError|IndentationError|TabError|error TS\d+|error\[E\d+\]|cannot find symbol|COMPILATION ERROR|BUILD FAILED|"
    r"undefined: |syntax error|error CS\d+|TypeError|ReferenceError|NameError|AssertionError|AttributeError|--- FAIL|"
    r"\bFAILED\b|Tests? failed|\d+ failed|error: could not compile)", re.I)
NO_TEST_SCRIPT_RX = re.compile(r"(Missing script: \"?test|no test specified|no tests ran|collected 0 items|no test files)", re.I)


def classify_failure(text: str, timed_out: bool) -> str:
    if timed_out:
        return "timeout"
    if ENV_FAILURE_RX.search(text or ""):
        return "environment"
    if REPO_FAILURE_RX.search(text or ""):
        return "repo"
    return "unknown"


def parse_results(text: str, runner: str):
    if runner == "go":
        p, f = len(re.findall(r"^\s*--- PASS", text, re.M)), len(re.findall(r"^\s*--- FAIL", text, re.M))
        return (p, f) if p or f else (None, None)
    if runner in ("maven", "gradle"):
        runs = re.findall(r"Tests run: (\d+), Failures: (\d+), Errors: (\d+)", text)
        if runs:
            r = runs[-1]
            failed = int(r[1]) + int(r[2])
            return int(r[0]) - failed, failed
    for prx, frx in RESULT_PARSERS:
        ps = [int(x) for x in prx.findall(text)]
        if ps:
            return sum(ps) if runner == "cargo" else ps[-1], sum(int(x) for x in frx.findall(text)) if runner == "cargo" else int((frx.findall(text) or [0])[-1])
    if runner == "python":
        fs = re.findall(r"(\d+) failed", text)
        if fs:
            return 0, int(fs[-1])
    return None, None


def parse_listed(text: str, runner: str):
    """Number of tests a runner lists without running them."""
    if runner == "python":
        m = re.findall(r"(\d+) tests? collected|collected (\d+) items?", text)
        if m:
            return int(next(x for x in m[-1] if x))
        n = len(re.findall(r"^\S+::\S+", text, re.M))
        return n if n else (0 if "no tests collected" in text.lower() else None)
    if runner == "go":
        return len(re.findall(r"^(Test|Benchmark|Example|Fuzz)\w*$", text, re.M))
    if runner == "cargo":
        return len(re.findall(r": (test|bench)$", text, re.M))
    if runner == "dotnet":
        _, _, tail = text.partition("The following Tests are available:")
        return len([x for x in tail.splitlines() if x.startswith((" ", "\t")) and x.strip()]) if tail else None
    return None


RUNNER_EXTS = {"npm": {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".vue", ".svelte"}, "python": {".py"},
               "go": {".go"}, "cargo": {".rs"}, "dotnet": {".cs", ".fs", ".vb"}, "maven": {".java", ".kt", ".scala", ".groovy"},
               "gradle": {".java", ".kt", ".scala", ".groovy"}}


def project_roots(names: list) -> list:
    """(folder, runner, source files under it), largest first. Nested roots of the same runner are left to their parent."""
    by_dir = defaultdict(set)
    for p in names:
        if vendor_kind(p):
            continue
        n = Path(p).name
        runner = RUNNER_MANIFESTS.get(n) or ("dotnet" if n.endswith((".sln", ".csproj")) else None)
        if runner:
            by_dir[Path(p).parent.as_posix()].add(runner)
    roots = []
    for d, runners in by_dir.items():
        prefix = "" if d == "." else d + "/"
        for runner in (r for r in RUNNER_ORDER if r in runners):
            if runner == "gradle" and "maven" in runners:
                continue
            if any(o != d and (o == "." or d.startswith(o + "/")) and runner in rs for o, rs in by_dir.items()):
                continue  # a nested project of the same kind is built by its parent
            exts = RUNNER_EXTS[runner]
            weight = sum(1 for p in names if p.startswith(prefix) and Path(p).suffix.lower() in exts and not vendor_kind(p))
            roots.append((d, runner, weight))
    roots.sort(key=lambda r: (-r[2], r[0].count("/"), r[0]))
    return roots


def build_env(scratch_home: str | None) -> dict:
    env = {k: v for k, v in os.environ.items() if not CREDENTIAL_ENV_RX.search(k)}
    env.update(OFFLINE_ENV)
    if scratch_home:
        env.update(HOME=scratch_home, XDG_CACHE_HOME=os.path.join(scratch_home, ".cache"), npm_config_userconfig=os.path.join(scratch_home, ".npmrc"))
    return env


def probe_root(repo: Path, rel: str, runner: str, level: str, timeout: int, install: bool, scratch: str | None,
               test_tools: list) -> dict:
    cwd = repo if rel == "." else repo / rel
    r = {"runner": runner, "depth": 0 if rel == "." else rel.count("/") + 1, "status": None, "failure_class": None,
         "dependencies_present": None, "install_attempted": False, "install_ok": None, "build_ok": None,
         "tests_collected": None, "tests_ran": None, "tests_passed": None, "tests_failed": None, "timed_out": False, "seconds": 0}
    t0 = time.time()
    py = sys.executable
    env = build_env(scratch)

    def run(cmd, extra_env=None, t=timeout):
        cmd = [py if c == "{py}" else c for c in cmd]
        try:
            e = {k: v for k, v in {**env, **(extra_env or {})}.items() if v is not None}
            pr = subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=t, check=False, env=e)
            return pr.returncode, (pr.stdout + pr.stderr).decode("utf-8", "replace")[-200_000:]
        except subprocess.TimeoutExpired:
            r["timed_out"] = True
            return None, ""
        except OSError as e:
            return None, f"command not found: {type(e).__name__}"

    build_cmd, test_cmd, list_cmd = BUILD_CMDS[runner]
    tool = build_cmd[0]
    if runner == "gradle" and not (cwd / "gradlew").exists():
        build_cmd, test_cmd = (["gradle", "--offline", "assemble"], ["gradle", "--offline", "test"])
        tool = "gradle"
    if tool not in ("{py}",) and not tool.startswith("./") and shutil.which(tool) is None:
        r.update(status="environment_missing", failure_class="environment")
        return r
    if install:
        net = {k: None for k in ("GOPROXY", "GOFLAGS", "CARGO_NET_OFFLINE", "npm_config_offline", "PIP_NO_INDEX")}  # unset: network on
        cmds = []
        if runner == "npm":
            if (cwd / "pnpm-lock.yaml").exists() and shutil.which("pnpm"):
                cmds = [["pnpm", "install", "--frozen-lockfile"]]
            elif (cwd / "yarn.lock").exists() and shutil.which("yarn"):
                cmds = [["yarn", "install", "--frozen-lockfile"]]
            elif (cwd / "package-lock.json").exists():
                cmds = [["npm", "ci", "--no-audit", "--no-fund"]]
            else:
                cmds = [["npm", "install", "--no-audit", "--no-fund"]]
        elif runner == "python":
            venv = os.path.join(scratch, f"venv-{h(rel, 8)}")
            code, _ = run([sys.executable, "-m", "venv", venv], net)
            target = []
            if code == 0:
                py = os.path.join(venv, "Scripts" if os.name == "nt" else "bin", "python")
            else:  # some interpreters cannot build a venv with pip; install into a scratch folder instead
                tdir = os.path.join(scratch, f"site-{h(rel, 8)}")
                target = ["--target", tdir]
                env["PYTHONPATH"] = tdir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
            pip = ["{py}", "-m", "pip", "install", "-q", "--disable-pip-version-check", *target]
            for req in ("requirements.txt", "requirements-dev.txt", "requirements_dev.txt", "requirements-test.txt",
                        "requirements/dev.txt", "requirements/tests.txt", "requirements/test.txt"):
                if (cwd / req).exists():
                    cmds.append([*pip, "-r", req])
            if (cwd / "pyproject.toml").exists() or (cwd / "setup.py").exists():
                cmds.append([[*pip, "."]] if target else [[*pip, "-e", "."], [*pip, "."]])  # alternatives, first that works
            if "pytest" in test_tools or "unittest" not in test_tools:
                cmds.append([*pip, "pytest"])
        elif runner == "go":
            cmds = [["go", "mod", "download"]]
        elif runner == "cargo":
            cmds = [["cargo", "fetch"]]
        elif runner == "dotnet":
            cmds = [["dotnet", "restore"]]
        elif runner == "maven":
            cmds = [["mvn", "-q", "dependency:go-offline"]]
        elif runner == "gradle":
            cmds = [[build_cmd[0], "dependencies", "-q"]]
        r["install_attempted"] = True
        ok, text = True, ""
        for c in cmds:
            for alt in (c if isinstance(c[0], list) else [c]):
                code, text = run(alt, net)
                if code == 0:
                    break
            if code != 0:
                ok = False
                break
        r["install_ok"] = ok
        if not ok:
            r.update(status="timed_out" if r["timed_out"] else "environment_missing",
                     failure_class="timeout" if r["timed_out"] else "environment", seconds=round(time.time() - t0, 1))
            return r
    if runner == "npm":
        r["dependencies_present"] = (cwd / "node_modules").is_dir() or (repo / "node_modules").is_dir()
        if not r["dependencies_present"]:
            r.update(status="environment_missing", failure_class="environment")
            return r
    code, text = run(build_cmd)
    if code is None and not r["timed_out"]:
        r.update(status="environment_missing", failure_class="environment", seconds=round(time.time() - t0, 1))
        return r
    r["build_ok"] = None if code is None else code == 0
    if not r["build_ok"]:
        fc = classify_failure(text, r["timed_out"])
        r.update(failure_class=fc, status={"environment": "environment_missing", "timeout": "timed_out"}.get(fc, "build_failed"))
        if fc in ("environment", "timeout"):
            r["build_ok"] = None
        r["seconds"] = round(time.time() - t0, 1)
        return r
    if list_cmd:
        code, text = run(list_cmd)
        if code is not None and not ENV_FAILURE_RX.search(text):
            r["tests_collected"] = parse_listed(text, runner)
            if r["dependencies_present"] is None:
                r["dependencies_present"] = True
        elif ENV_FAILURE_RX.search(text or ""):
            r["dependencies_present"] = False
    elif r["dependencies_present"] is None:
        r["dependencies_present"] = True
    r["status"] = "built"
    if level == "full":
        code, text = run(test_cmd)
        passed, failed = parse_results(text, runner)
        if passed is None and (code is None or code != 0):
            fc = classify_failure(text, r["timed_out"])
            if NO_TEST_SCRIPT_RX.search(text):
                r.update(tests_ran=False, status="built_no_test_command")
            elif fc in ("environment", "timeout"):
                r.update(tests_ran=False, failure_class=fc, status="environment_missing" if fc == "environment" else "timed_out")
            else:
                r.update(tests_ran=True, failure_class=fc, status="tests_failed")
        else:
            r.update(tests_ran=True, tests_passed=passed, tests_failed=failed,
                     status="tests_passed" if code == 0 else "tests_failed")
            if code != 0:
                r["failure_class"] = classify_failure(text, r["timed_out"]) if not failed else "repo"
                if r["failure_class"] == "environment" and not failed:
                    r["status"] = "environment_missing"
    r["seconds"] = round(time.time() - t0, 1)
    return r


def stage_build(repo: Path, level: str, timeout: int, tests: dict, install: bool = False, max_roots: int = 3,
                test_tools: list | None = None) -> dict:
    names = [p for p, _ in list_files(repo)]
    base = {Path(p).name for p in names}
    systems = sorted({tool for f, tool in MANIFESTS.items() if f in base})
    if any(p.endswith((".sln", ".csproj")) for p in names):
        systems.append("dotnet")
    engine = engine_version = None
    pv = next((p for p in names if p.endswith("ProjectSettings/ProjectVersion.txt")), None)
    if pv:
        engine = "unity"
        m = re.search(r"m_EditorVersion:\s*([\w.]+)", (repo / pv).read_text("utf-8", "replace"))
        engine_version = m.group(1) if m else None
    elif any(p.endswith(".uproject") for p in names):
        engine = "unreal"
        up = next(p for p in names if p.endswith(".uproject"))
        try:
            ev = json.loads((repo / up).read_text("utf-8", "replace")).get("EngineAssociation")
            engine_version = ev if isinstance(ev, str) and re.fullmatch(r"\d+\.\d+", ev) else None
        except ValueError:
            pass
    elif "godot" in systems:
        engine = "godot"
    roots = project_roots(names)
    out = {
        "level": level, "install": bool(install), "network_used": False,
        "build_systems": systems, "engine": engine, "engine_version": engine_version,
        "host_os": {"Darwin": "darwin", "Linux": "linux", "Windows": "windows"}.get(platform.system(), "other"),
        "tests_found": tests.get("tests_declared"), "tests_declared": tests.get("tests_declared"),
        "roots_found": len(roots), "roots_probed": 0, "roots_skipped": 0, "roots": [],
        "status": "not_requested" if level == "detect" else "not_run", "failure_class": None, "repo_intrinsic_failure": None,
        "dependencies_present": None, "build_ok": None, "tests_collected": None, "tests_ran": None, "tests_passed": None,
        "tests_failed": None, "test_pass_rate": None, "discover_index": None, "runnability": None, "reason": None,
        "timed_out": False, "seconds": 0,
    }
    if level == "detect":
        out["reason"] = "detect level reads manifests only; no build was requested"
        return out
    if engine in ("unity", "unreal") and not any(r[1] != "dotnet" for r in roots):
        out.update(status="environment_missing", failure_class="environment",
                   reason="engine projects need the engine editor; the repo is not blamed")
        return out
    if not roots:
        out.update(status="unsupported", reason="no supported build system")
        return out
    scratch = None
    if install:
        import tempfile
        scratch = tempfile.mkdtemp(prefix="measure-code-build-")
        out["network_used"] = True
    t0 = time.time()
    results = []
    try:
        for rel, runner, _ in roots[:max_roots]:
            if time.time() - t0 > 3 * timeout:
                break
            results.append(probe_root(repo, rel, runner, level, timeout, install, scratch, test_tools or []))
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)
    out["roots"] = results
    out["roots_probed"] = len(results)
    out["roots_skipped"] = len(roots) - len(results)
    out["seconds"] = round(time.time() - t0, 1)
    out["timed_out"] = any(r["timed_out"] for r in results)
    definite = [r for r in results if r["build_ok"] is not None]
    deps = [r["dependencies_present"] for r in results if r["dependencies_present"] is not None]
    out["dependencies_present"] = all(deps) if deps else None
    out["build_ok"] = all(r["build_ok"] for r in definite) if definite else None
    listed = [r["tests_collected"] for r in results if r["tests_collected"] is not None]
    out["tests_collected"] = sum(listed) if listed else None
    ran = [r for r in results if r["tests_ran"]]
    out["tests_ran"] = bool(ran) if level == "full" and definite else None
    if ran:
        ps = [r["tests_passed"] for r in ran if r["tests_passed"] is not None]
        fs = [r["tests_failed"] for r in ran if r["tests_failed"] is not None]
        out["tests_passed"] = sum(ps) if ps else None
        out["tests_failed"] = sum(fs) if fs else None
        if out["tests_passed"] is not None and out["tests_passed"] + (out["tests_failed"] or 0):
            out["test_pass_rate"] = round(out["tests_passed"] / (out["tests_passed"] + (out["tests_failed"] or 0)), 3)
    classes = [r["failure_class"] for r in results if r["failure_class"]]
    out["failure_class"] = ("repo" if "repo" in classes else "environment" if "environment" in classes else
                            "timeout" if "timeout" in classes else "unknown" if classes else "none")
    out["repo_intrinsic_failure"] = "repo" in classes
    if not definite:
        st = {r["status"] for r in results}
        out["status"] = "timed_out" if st == {"timed_out"} else "environment_missing"
        out["reason"] = ("the build timed out" if out["status"] == "timed_out" else
                         "the toolchain, dependencies or network this repo needs are missing here; the repo is not blamed. "
                         "Install dependencies in the clone or pass --install (uses the network), then re-run")
        return out
    out["status"] = ("build_failed" if not out["build_ok"] else "built" if level == "discover" else
                     "tests_failed" if any(r["status"] == "tests_failed" for r in results) else
                     "environment_missing" if not ran and any(r["status"] == "environment_missing" for r in results) else
                     "built_no_tests_run" if not ran else "tests_passed")
    # discover index 0-3: dependencies present, build ok, runner lists 1+ tests
    out["discover_index"] = (int(bool(out["dependencies_present"])) + int(bool(out["build_ok"]))
                             + int(bool(out["tests_collected"])))
    if level == "full" and out["status"] != "environment_missing":
        # executed runnability 0-4: build ok, a suite ran, any test passed, 90%+ passed. Executed terms only.
        out["runnability"] = (int(bool(out["build_ok"])) + int(bool(ran)) + int(bool(out["tests_passed"]))
                              + int((out["test_pass_rate"] or 0) >= 0.9))
    elif level == "full":
        out["reason"] = "the build ran but the test suite could not start here (missing tool or package); the repo is not blamed"
    else:
        out["reason"] = "discover level builds and lists tests only; runnability needs --build full"
    return out


# --------------------------------------------------------------------------
# stage: code age by blame (how much of today's code was written before the cutoff)
# --------------------------------------------------------------------------


def stage_blame(repo: Path, cutoff: str, budget: int) -> dict:
    """Blame date of every line of first-party, non-test source at HEAD. Largest files go first. The share is
    reported only when every file was blamed; otherwise the result is marked partial with its line coverage."""
    cutoff_ts = dt.datetime.strptime(cutoff, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
    files = []
    for rel, _ in list_files(repo):
        lang = LANG_BY_EXT.get(Path(rel).suffix.lower())
        if lang and not vendor_kind(rel) and not is_test_path(rel):
            try:
                with (repo / rel).open("rb") as f:
                    n = f.read().count(b"\n")
                files.append((n, rel))
            except OSError:
                pass
    files.sort(reverse=True)  # biggest first, so a cut-off budget still covers most lines
    lines_total = sum(n for n, _ in files)
    t0 = time.time()
    blamed = after = done = failed = 0
    budget_hit = False
    for _, rel in files:
        if time.time() - t0 > budget:
            budget_hit = True
            break
        try:
            raw = git(repo, "blame", "--line-porcelain", "-w", "HEAD", "--", rel, timeout=max(5, int(budget - (time.time() - t0))))
        except (RuntimeError, subprocess.TimeoutExpired):
            failed += 1
            continue
        times = [int(x) for x in re.findall(r"^author-time (\d+)$", raw, re.M)]
        blamed += len(times)
        after += sum(1 for t in times if t >= cutoff_ts)
        done += 1
    complete = done == len(files)
    coverage = round(min(1.0, blamed / lines_total), 3) if lines_total else None
    return {"status": "complete" if complete else "partial",
            "partial_reason": None if complete else ("budget_exhausted" if budget_hit else "blame_failed_on_some_files"),
            "files_blamed": done, "files_failed": failed, "files_total": len(files), "lines_total": lines_total,
            "lines_blamed": blamed, "coverage_share": coverage,
            "lines_written_since_cutoff": after,
            "share_written_since_cutoff": round(after / blamed, 3) if blamed and complete else None,
            "share_in_blamed_lines": round(after / blamed, 3) if blamed else None,
            "complete": complete, "budget_seconds": budget, "cutoff": cutoff}


# --------------------------------------------------------------------------
# derived: classification, logic band, tasks, recommendation
# --------------------------------------------------------------------------


SCAFFOLDS = {  # file sets an untouched project generator leaves behind
    "create-react-app": ("src/logo.svg", "src/reportWebVitals.js"),
    "create-react-app-ts": ("src/logo.svg", "src/react-app-env.d.ts"),
    "vite-react": ("src/assets/react.svg", "public/vite.svg"),
    "vite-vue": ("src/assets/vue.svg", "public/vite.svg"),
    "create-next-app": ("public/next.svg", "public/vercel.svg"),
    "angular-cli": ("src/app/app.component.spec.ts", "src/favicon.ico"),
}


def scaffold_hits(names) -> list:
    ns = set(names)
    return sorted(k for k, files in SCAFFOLDS.items() if all(f in ns for f in files))


def classify(langs: dict, signals: dict, build: dict, git_: dict) -> dict:
    fw = set(signals.get("frameworks", []))
    loc = {k: v.get("code_lines", 0) for k, v in langs.items()}
    total = sum(loc.values()) or 1
    share = lambda *names: sum(loc.get(n, 0) for n in names) / total  # noqa: E731
    score = Counter()
    if build.get("engine") or fw & {"phaser", "three.js", "babylon.js", "pygame", "bevy"} or share("Shader", "GDScript") > 0.05:
        score["game"] += 3
    score["frontend"] += 2 * bool(fw & {"react", "next", "vue", "nuxt", "svelte", "angular"}) + 3 * share("CSS", "SCSS", "Less", "HTML", "Vue", "Svelte", "Astro")
    score["backend"] += 2 * bool(fw & {"express", "fastify", "nestjs", "django", "flask", "fastapi", "rails", "spring", "laravel", "gin", "echo", "actix", "axum"}) \
        + bool(signals.get("environment", {}).get("dockerfiles")) + share("Go", "Java", "PHP", "Ruby", "Elixir")
    score["mobile"] += 3 * bool(fw & {"react-native", "flutter"}) + 3 * share("Swift", "Kotlin", "Dart", "Objective-C")
    score["ml"] += 2 * bool(fw & {"pytorch", "tensorflow", "jax", "scikit-learn"}) + min(2, signals.get("_notebooks", 0) / 5)
    score["data"] += 2 * bool(fw & {"airflow", "dbt", "spark"}) + 3 * share("SQL")
    iac = signals.get("iac", {})
    score["infra"] += 3 * share("Terraform", "HCL", "Nix") + min(2, (iac.get("terraform_files", 0) + iac.get("helm_charts", 0)) / 10)
    if score["frontend"] >= 1 and score["backend"] >= 1:
        score["fullstack"] = score["frontend"] + score["backend"]
    best = score.most_common(1)
    primary = best[0][0] if best and best[0][1] >= 1 else "library_or_tool"
    tot = sum(v for v in score.values() if v > 0) or 1
    ws = signals.get("workspaces", {})
    # Strong signals decide; weak ones are recorded but only decide together. Truncated history is never evidence.
    strong, weak = [], []
    if signals.get("demo_words"):
        strong.append("readme describes a tutorial, template or starter")
    if signals.get("scaffolds"):
        strong.append("project generator files left as generated")
    commits = git_.get("commits") or 0
    history_ok = git_.get("history_complete") is not False or git_.get("history_single_commit")
    if history_ok and not git_.get("history_shallow") and git_.get("commits") is not None:
        if commits < 15:
            weak.append("fewer than 15 commits")
        if git_.get("history_single_commit"):
            weak.append("single-commit snapshot")
    if total < 1500:
        weak.append("under 1,500 lines of code")
    demo = (bool(strong) and (total < 5000 or commits < 30)) or len(weak) >= 3
    return {"primary_class": primary,
            "class_scores": {k: round(v / tot, 2) for k, v in score.most_common() if v > 0},
            "is_monorepo": bool(ws.get("monorepo_tooling")) or ws.get("package_roots", 0) >= 4,
            "is_likely_demo": demo, "demo_reasons": strong + weak,
            "demo_strong_signals": len(strong), "demo_weak_signals": len(weak)}


def logic_band(logic: dict, tests: dict) -> tuple:
    """Deterministic 0-6 band from densities, not size steps, so large repos do not all saturate.
    size (log scale, max 1) + branching density (max 1.5) + share of deeply nested files (max 1.5)
    + test/source line ratio (max 1) + modularity (max 1)."""
    import math
    loc = logic.get("source_code_lines") or 0
    bpk = logic.get("branches_per_kloc") or 0
    nest = logic.get("files_nesting_5plus_share") or 0
    tr = tests.get("test_to_source_line_ratio") or 0
    mods = logic.get("top_level_modules") or 0
    parts = {
        "size": min(1.0, max(0.0, math.log10(loc / 1000) / 2)) if loc > 1000 else 0.0,   # 1k lines -> 0, 100k -> 1
        "branching density": 1.5 * min(1.0, max(0.0, (bpk - 60) / 180)),                  # 60/kloc -> 0, 240 -> 1.5
        "deep nesting": 1.5 * min(1.0, nest / 0.4),                                       # 40% of files nested 5+ -> 1.5
        "tests": min(1.0, tr / 0.4),                                                      # test lines 40% of source -> 1
        "modularity": min(1.0, max(0, mods - 1) / 9),                                     # 10+ top-level modules -> 1
    }
    total = sum(parts.values())
    band = int(min(6, round(total)))
    reasons = [f"{k} {v:.2f}" for k, v in parts.items()]
    return band, reasons, round(total, 2)


def task_block(sig: dict | None, reason: str = "not run in simple mode") -> dict:
    if sig is None:
        return {"method": reason, "candidates": None, "candidates_total": None,
                "source_only_candidates": None, "commit_kinds": None, "capacity": None}
    g = lambda k: sig.get(k, 0)  # noqa: E731
    return {
        "method": "deterministic commit census (strict message kinds, lines changed, files and folders touched); counts only",
        "candidates": {k: g(f"{k}_with_tests") for k in TASK_KINDS},
        "candidates_total": g("with_tests_total"),
        "source_only_candidates": {k: g(f"{k}_source_only") for k in TASK_KINDS},
        "commit_kinds": {k: g(f"{k}_commits") for k in TASK_KINDS},
        "capacity": {
            "mineable_commits": g("mineable_commits"),
            "mineable_by_kind": {k: g(f"{k}_mineable") for k in TASK_KINDS},
            "rule": dict(CAPACITY_RULE),
            "chore_commits_excluded": g("chore_commits"), "too_spread_commits_excluded": g("too_spread_commits"),
            "bulk_test_commits": g("bulk_test_commits"),
            "fix_subjects_not_corroborated": g("fix_subject_not_corroborated"),
            "bugfix_with_regression_test": g("bugfix_with_regression_test"),
            "lines_changed_first_party": g("lines_changed_first_party"),
        },
    }


# Reasons a full run is `partial`: something failed, timed out or was skipped. A build the user did not ask for
# (the default --build detect) is not one of them; it is recorded as status_info.build = "not_requested".
STATUS_REASONS = ("simple_mode", "not_a_git_repository", "no_commits", "shallow_clone", "census_not_run",
                  "task_mining_skipped", "build_failed", "tests_failed", "build_environment_missing", "build_timed_out",
                  "build_unsupported", "code_age_partial", "code_age_not_run")
BUILD_REASON = {"build_failed": "build_failed", "tests_failed": "tests_failed", "environment_missing": "build_environment_missing",
                "timed_out": "build_timed_out", "unsupported": "build_unsupported"}


def recommend(doc: dict) -> dict:
    g, t = doc["git"], doc["tree"]
    reasons, score = [], 0
    commits = g.get("commits") or 0
    for cond, why in ((commits >= 200, "200+ commits of history"),
                      ((t["code_lines_total"] or 0) >= 10_000, "10k+ lines of code"),
                      ((g.get("pre_cutoff_share") or 0) >= 0.3, "30%+ of history predates the cutoff"),
                      ((g.get("authors_human") or 0) >= 2, "more than one human author"),
                      ((doc["tests"]["test_files"] or 0) > 0, "has tests"),
                      (not doc["classification"]["is_likely_demo"], "not a demo or template")):
        if cond:
            score += 1
            reasons.append(why)
    verdict = "recommended" if score >= 5 else "optional" if score >= 3 else "not worth it"
    minutes = 1 + (t["code_lines_total"] or 0) / 150_000 + commits / 40_000 + 5
    return {"full_run": verdict, "reasons": reasons, "estimated_full_run_minutes": int(round(minutes))}


# --------------------------------------------------------------------------
# output boundary: every string must sit in a declared field
# --------------------------------------------------------------------------

STRING_FIELDS = [re.compile(p) for p in (
    r"^schema$", r"^kind$", r"^tool$", r"^tool_version$", r"^mode$", r"^status$", r"^status_reasons\[\d+\]$", r"^skip_reason$",
    r"^status_info\.\w+$", r"^dream\.(title|status|text|basis|skip_reason)$",
    r"^measured_at$", r"^label$", r"^tags\[\d+\]$", r"^cutoff$", r"^span\.(first|last)$", r"^git\.(first|last)_commit$",
    r"^git\.history_note$", r"^history_ref\.(policy|measured)$",
    r"^fingerprints\.\w+\[\d+\]$", r"^tree\.(mode_note|code_lines_definition)$", r"^lanes\.\w+$", r"^tests\.frameworks\[\d+\]$",
    r"^build\.(level|build_systems\[\d+\]|engine|engine_version|host_os|reason|status|failure_class)$",
    r"^build\.roots\[\d+\]\.(runner|status|failure_class)$",
    r"^code_age\.(status|partial_reason|cutoff)$",
    r"^signals\.(frameworks|scaffolds|ci\.systems|hygiene\.\w+|dependencies\.(package_managers|lockfiles)|observability\.\w+|workspaces\.monorepo_tooling)\[\d+\]$",
    r"^classification\.(primary_class|demo_reasons\[\d+\])$", r"^logic\.(reasons\[\d+\]|authoritative)$", r"^tasks\.method$",
    r"^task_mining\.(status|skip_reason|kinds_note)$",
    r"^census\.(themes\[\d+\]|ideas\[\d+\]\.(kind|text)|categories\.\w+\[\d+\]|summary|reviewer|model|weights_version|"
    r"task_mining\.(status|skip_reason)|what_could_not_be_assessed\[\d+\])$",
    r"^recommendation\.(full_run|reasons\[\d+\])$", r"^privacy\.\w+$", r"^provenance\.\w+$",
)]
EMAIL_RX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ABS_PATH_RX = re.compile(r"(^|[\s\"'(])(/(Users|home|Volumes|mnt|var|tmp|opt|private)/|[A-Za-z]:\\)")
KEY_RX = re.compile(r"^[a-z0-9_]+$|^\d{4}(-\d{2})?$")


def leaves(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from leaves(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from leaves(v, f"{path}[{i}]")
    else:
        yield path, obj


def keys_ok(obj, path="") -> list:
    bad = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            vocab = k in LANGUAGES or path.endswith(("config_and_docs_files", "class_scores")) or k in NON_CODE_LANGS.values()
            if not (KEY_RX.match(str(k)) or vocab):
                bad.append(f"{path}.{k}: undeclared key")
            bad += keys_ok(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for v in obj:
            bad += keys_ok(v, path)
    return bad


def boundary(doc: dict, denylist: set) -> list:
    problems = keys_ok(doc)
    deny = {d.lower() for d in denylist if d and len(d) >= 4}
    for path, v in leaves(doc):
        if not isinstance(v, str):
            continue
        if not any(rx.match(path) for rx in STRING_FIELDS):
            problems.append(f"{path}: text in an undeclared field")
            continue
        if path in ("label",) or path.startswith("tags"):
            continue  # chosen by the user
        if EMAIL_RX.search(v):
            problems.append(f"{path}: looks like an email address")
        if ABS_PATH_RX.search(v):
            problems.append(f"{path}: looks like an absolute path")
        if path.startswith("census") or path in ("build.engine_version", "dream.text"):
            low = v.lower()
            if any(re.search(r"(?<![a-z0-9])" + re.escape(d) + r"(?![a-z0-9])", low) for d in deny):
                problems.append(f"{path}: contains an author name or email from the history")
    return problems


def author_denylist(repo: Path) -> set:
    out = set()
    try:
        for line in git(repo, "log", "--all", "--format=%ae%x1f%an%x1f%ce%x1f%cn").splitlines():
            out.update(p.lower() for p in line.split("\x1f") if len(p) >= 4)
    except RuntimeError:
        pass
    # hosting services and bots commit too; they are not people, and "GitHub" must stay usable in sentences
    return {d for d in out if d not in SERVICE_IDENTITIES and "[bot]" not in d and "noreply" not in d}


SERVICE_IDENTITIES = {"github", "gitlab", "bitbucket", "web-flow", "github enterprise", "azure devops", "gitea", "codeberg"}


# --------------------------------------------------------------------------
# report and flat row
# --------------------------------------------------------------------------


def fmt(n) -> str:
    if n is None:
        return "n/a"
    if isinstance(n, bool):
        return "yes" if n else "no"
    if isinstance(n, float):
        return f"{n:.1%}" if n <= 1 else f"{n:,.1f}"
    return f"{n:,}"


def of(x, top) -> str:
    return "n/a" if x is None else f"{x}/{top}"


def ratio(x) -> str:
    return "n/a" if x is None else f"{x:.2f}"


def summary_row(doc: dict) -> dict:
    g, t, ts, b, lg, tk, c = doc["git"], doc["tree"], doc["tests"], doc["build"], doc["logic"], doc["tasks"], doc["classification"]
    s = doc.get("signals") or {}
    ca = doc.get("code_age") or {}
    tm = doc.get("task_mining") or {}
    cap = tk.get("capacity") or {}
    row = {
        "label": doc["label"], "mode": doc["mode"], "status": doc["status"], "estimate": doc["estimate"],
        "status_reasons": ";".join(doc["status_reasons"]) or None, "skip_reason": doc["skip_reason"],
        "status_info": ";".join(f"{k}:{v}" for k, v in (doc.get("status_info") or {}).items()) or None,
        "measured_at": doc["measured_at"],
        "primary_language": next(iter(t["languages"]), None), "languages": len(t["languages"]),
        "code_lines": t["code_lines_total"], "nonblank_lines": t.get("nonblank_lines_total"), "source_files": t["source_files"],
        "test_files": ts["test_files"], "test_code_lines": ts["test_code_lines"], "test_ratio": ts["test_to_source_line_ratio"],
        "tests_declared": ts.get("tests_declared"), "test_frameworks": ";".join(ts.get("frameworks") or []) or None,
        "frameworks": ";".join(s.get("frameworks", [])) or None, "ci_present": bool(s.get("ci", {}).get("systems")) if s else None,
        "ci_runs_tests": s.get("ci", {}).get("runs_tests") if s else None,
        "commits": g["commits"], "authors_human": g["authors_human"], "first_commit": g["first_commit"],
        "last_commit": g["last_commit"], "span_days": g["span_days"], "active_days": g["active_days"],
        "active_months": g["active_months"], "history_shallow": g.get("history_shallow"), "history_complete": g.get("history_complete"),
        "history_ref": (doc.get("history_ref") or {}).get("measured"),
        "pre_cutoff_share": g["pre_cutoff_share"],
        "code_written_since_cutoff_share": ca.get("share_written_since_cutoff"),
        "code_age_status": ca.get("status"), "code_age_coverage": ca.get("coverage_share"),
        "pr_merged_count": g.get("pr_merged_count"), "ai_assisted_share": g.get("ai_assisted_share"),
        "primary_class": c["primary_class"], "is_monorepo": c["is_monorepo"], "is_likely_demo": c["is_likely_demo"],
        "build_status": b.get("status"), "build_failure_class": b.get("failure_class"), "build_ok": b.get("build_ok"),
        "tests_found": b.get("tests_found"), "tests_collected": b.get("tests_collected"), "tests_ran": b.get("tests_ran"),
        "discover_index": b.get("discover_index"), "runnability": b.get("runnability"),
        "logic_depth": lg["logic_depth"], "logic_depth_authoritative": lg["authoritative"],
        "deterministic_logic_depth": lg["deterministic_depth"], "reviewer_logic_depth": lg["reviewer_depth"],
        "capacity": cap.get("mineable_commits"), "task_candidates": tk["candidates_total"],
        "task_mining_status": tm.get("status"), "mined_ideas": tm.get("ideas_total"),
        "dream_status": (doc.get("dream") or {}).get("status"),
    }
    for k in IDEA_KINDS:
        row[f"mined_{k}"] = (tm.get("ideas_by_kind") or {}).get(k) if tm.get("ideas_by_kind") else None
    for k in TASK_KINDS:
        row[f"tasks_{k}"] = (tk["candidates"] or {}).get(k) if tk["candidates"] else None
    return row


AUTH_LABEL = {"deterministic": "deterministic band", "reviewer_census": "reviewer census"}


def render_report(doc: dict) -> str:
    g, t, tests, b, lg, tk, cl = doc["git"], doc["tree"], doc["tests"], doc["build"], doc["logic"], doc["tasks"], doc["classification"]
    simple = doc["mode"] == "simple"
    rec = doc["recommendation"]
    est = " (estimate)" if simple else ""
    L = [f"# Code measurement: {doc['label']}" + (" (simple mode)" if simple else ""), "",
         f"Measured {doc['measured_at'][:10]} with {TOOL} {VERSION}, {doc['mode']} mode. Counts only: no names, paths or messages.",
         "", f"**Status: {doc['status']}**" + (f" ({', '.join(doc['status_reasons'])})" if doc["status_reasons"] else
                                              " (every lane ran, including the reviewer census and task mining)")
         + "".join(f"; info: {k} {v}" for k, v in (doc.get("status_info") or {}).items())]
    if simple:
        L += ["", "_Every figure in this report is an estimate from metadata and a file sample. Run full mode for measured numbers._"]
    if t.get("mode_note"):
        L += ["", f"_Estimate: {t['mode_note']}._"]
    if g.get("history_note"):
        L += ["", f"_History: {g['history_note']}._"]
    ref = doc.get("history_ref") or {}
    if ref.get("measured") == "deepest_branch":
        L += ["", f"_History was measured on the deepest branch ({fmt(ref['measured_commits'])} commits) because HEAD holds only "
                  f"{fmt(ref['head_commits'])}. Lines of code and tests describe the checked-out tree; check that branch out to measure its tree._"]
    elif ref.get("deeper_ref_exists"):
        L += ["", f"_A branch holds {fmt(ref['deepest_ref_commits'])} commits against {fmt(ref['head_commits'])} on HEAD; "
                  "re-run with --ref auto to measure it._"]
    L += ["", "## 1. Headline", "",
          f"- {fmt(g['commits'])} commits by {fmt(g['authors_human'])} people (+{fmt(g['authors_bot'])} bots), "
          f"{g['first_commit'] or 'n/a'} to {g['last_commit'] or 'n/a'} ({fmt(g['active_months'])} active months, {fmt(g['active_days'])} active days)",
          f"- {fmt(t['code_lines_total'])} lines of code{', estimated' if simple else ''} (comments and blank lines excluded; {fmt(t.get('nonblank_lines_total'))} "
          f"non-blank lines with comments) in {fmt(t['source_files'])} source files; main language {next(iter(t['languages']), 'n/a')}; "
          f"class {cl['primary_class']}",
          f"- Tests{est}: {fmt(tests['test_files'])} files, {fmt(tests.get('tests_declared'))} test cases declared, "
          f"test/source line ratio {ratio(tests['test_to_source_line_ratio'])}; test tools {', '.join(tests.get('frameworks') or []) or 'none detected'}",
          f"- {fmt(g['pre_cutoff_share'])} of commits are dated before {doc['cutoff']}"
          + (f"; {fmt(doc['code_age']['share_written_since_cutoff'])} of today's code lines were written after it"
             if doc.get("code_age") and doc["code_age"].get("share_written_since_cutoff") is not None else ""),
          f"- Logic depth **{lg['logic_depth']}/6** (from the {AUTH_LABEL.get(lg['authoritative'], lg['authoritative'])}"
          + (f"; deterministic band {lg['deterministic_depth']}/6" if lg["authoritative"] != "deterministic" else "") + ")",
          *([] if simple else [f"- Capacity: {fmt((tk.get('capacity') or {}).get('mineable_commits'))} mineable commits; "
                               f"{fmt(tk['candidates_total'])} commits change source and tests together; "
                               f"{fmt((doc.get('task_mining') or {}).get('ideas_total'))} mined task ideas"]),
          *([f"- Full run: **{rec['full_run']}** ({'; '.join(rec['reasons']) or 'little to measure'}); about {rec['estimated_full_run_minutes']} minutes"]
            if simple else []), "",
          "## 2. History", "", "| metric | value |", "|---|---|"]
    for k in ("commits", "merge_commits", "authors_human", "authors_bot", "bot_commits", "authors_with_10plus_commits",
              "top1_author_share", "top5_author_share", "span_days", "recency_days", "active_days", "active_months",
              "commits_last_12_months", "pre_cutoff_commits", "pre_cutoff_share", "tags", "semver_tags", "branches",
              "pr_merged_count", "conventional_commit_share_last_200", "ai_coauthored_commits", "ai_agent_commits",
              "history_shallow", "history_complete"):
        L.append(f"| {k.replace('_', ' ')} | {fmt(g.get(k))} |")
    if ref:
        L.append(f"| history measured on | {ref.get('measured', 'n/a').replace('_', ' ')} ({fmt(ref.get('refs_considered'))} branches compared) |")
    L += ["", "| year | commits |", "|---|---|"] + [f"| {y} | {fmt(n)} |" for y, n in (g.get("commits_by_year") or {}).items()]
    L += ["", "## 3. Lines by language" + est, "", "| language | files | code | comments | blank |", "|---|---|---|---|---|"]
    for name, v in t["languages"].items():
        L.append(f"| {name} | {fmt(v.get('files', 0))} | {fmt(v.get('code_lines', 0))} | {fmt(v.get('comment_lines', 0))} | {fmt(v.get('blank_lines', 0))} |")
    L += ["", f"Median source file {fmt(t['median_file_lines'])} lines, 90th percentile {fmt(t['p90_file_lines'])}. "
          f"Vendored or generated files skipped: {fmt(t['vendored_or_generated_files_skipped'])}."]
    if doc.get("sourcing"):
        s = doc["sourcing"]
        L += [f"Lines of code: first-party {fmt(s['loc_first_party'])}, third-party {fmt(s['loc_third_party'])}, "
              f"generated {fmt(s['loc_generated'])}. Seed-data files {fmt(s['seed_data_files'])}; notebooks {fmt(s['notebooks'])}."]
    if t["other_files_by_family"]:
        L += ["", "Non-code files: " + ", ".join(f"{k} {fmt(v)}" for k, v in t["other_files_by_family"].items())]
    L += ["", "## 4. Classification", "", f"- primary class: **{cl['primary_class']}** "
          f"({', '.join(f'{k} {v:.0%}' for k, v in cl['class_scores'].items()) or 'no strong signal'})",
          f"- monorepo: {fmt(cl['is_monorepo'])}; likely demo: {fmt(cl['is_likely_demo'])}"
          + (f" ({'; '.join(cl['demo_reasons'])})" if cl["demo_reasons"] else "")]
    s = doc.get("signals")
    if s:
        L += ["", "## 5. Engineering signals", "", "| area | signal |", "|---|---|",
              f"| frameworks | {', '.join(s['frameworks']) or 'none detected'} |",
              f"| CI | {', '.join(s['ci']['systems']) or 'none'}; runs tests {fmt(s['ci']['runs_tests'])}, lint {fmt(s['ci']['runs_lint'])}, "
              f"typecheck {fmt(s['ci']['runs_typecheck'])}, deploys {fmt(s['ci']['deploys'])}, dependency audit {fmt(s['ci']['dependency_audit'])} |",
              f"| lint and format | {', '.join(s['hygiene']['linters_and_formatters']) or 'none'} |",
              f"| coverage | {', '.join(s['hygiene']['coverage_tooling']) or 'none'}; threshold set {fmt(s['hygiene']['coverage_threshold_set'])} |",
              f"| dependencies | {fmt(s['dependencies']['direct_runtime'])} runtime, {fmt(s['dependencies']['direct_dev'])} dev; "
              f"lockfiles {', '.join(s['dependencies']['lockfiles']) or 'none'}; update bot {', '.join(s['hygiene']['dep_update_tooling']) or 'none'} |",
              f"| docs | readme {fmt(s['docs']['readme'])} ({fmt(s['docs']['readme_lines'])} lines, {fmt(s['docs']['readme_sections'])} sections); "
              f"changelog {fmt(s['docs']['changelog'])}; contributing {fmt(s['docs']['contributing'])}; licence {fmt(s['docs']['license'])} |",
              f"| environment | dockerfiles {fmt(s['environment']['dockerfiles'])}; devcontainer {fmt(s['environment']['devcontainer'])}; "
              f"env example {fmt(s['environment']['env_example'])}; env files committed {fmt(s['environment']['env_files_committed'])} |",
              f"| observability | logging {', '.join(s['observability']['logging']) or 'none'}; error tracking "
              f"{', '.join(s['observability']['error_tracking']) or 'none'}; health endpoint {fmt(s.get('health_endpoint'))}; metrics {fmt(s.get('metrics'))} |",
              f"| infrastructure as code | terraform files {fmt(s['iac']['terraform_files'])}; kubernetes manifests {fmt(s['iac']['kubernetes_manifests'])}; helm charts {fmt(s['iac']['helm_charts'])} |"]
    if doc.get("authenticity"):
        a = doc["authenticity"]
        L += ["", f"Authenticity signals: median comment ratio {fmt(a['comment_ratio_median'])}; "
              f"assistant-phrase hits {fmt(a['llm_phrase_hits'])}; generated-marker files {fmt(a['generated_marker_files'])}; "
              f"mock calls {fmt(a.get('mock_calls'))} vs assertions {fmt(a.get('assertions'))} in tests."]
    L += ["", "## 6. Build probe", "",
          f"- level: {b['level']}{' with dependency install (network used)' if b.get('install') else ''}; status **{b.get('status')}**"
          f"{'; failure attributed to: ' + b['failure_class'] if b.get('failure_class') not in (None, 'none') else ''}",
          f"- build systems: {', '.join(b['build_systems']) or 'none'}; engine: {b['engine'] or 'none'} {b['engine_version'] or ''}".rstrip()
          + f"; project roots probed {fmt(b.get('roots_probed'))} of {fmt(b.get('roots_found'))}",
          f"- build ok: {fmt(b.get('build_ok'))}; tests found in files {fmt(b.get('tests_found'))}, listed by the runner "
          f"{fmt(b.get('tests_collected'))}, suite ran {fmt(b.get('tests_ran'))}; passed {fmt(b.get('tests_passed'))}, failed {fmt(b.get('tests_failed'))}",
          f"- discover index {of(b.get('discover_index'), 3)}; executed runnability {of(b.get('runnability'), 4)}"
          + (f"; note: {b['reason']}" if b.get("reason") else "")]
    ca = doc.get("code_age")
    if ca:
        L += ["", "## 7. Code age", "",
              f"Blame {ca['status']}: {fmt(ca['files_blamed'])} of {fmt(ca['files_total'])} source files, "
              f"{fmt(ca['coverage_share'])} of their lines. "
              + (f"{fmt(ca['share_written_since_cutoff'])} of today's lines were written after {ca['cutoff']}."
                 if ca.get("share_written_since_cutoff") is not None else
                 f"No whole-repo share is reported ({(ca.get('partial_reason') or '').replace('_', ' ')}); in the blamed lines "
                 f"{fmt(ca.get('share_in_blamed_lines'))} were written after {ca['cutoff']}.")]
    L += ["", "## 8. Logic depth", "",
          f"Authoritative value **{lg['logic_depth']}/6** from the {AUTH_LABEL.get(lg['authoritative'], lg['authoritative'])}.",
          f"Deterministic band {lg['deterministic_depth']}/6 (score {lg['deterministic_score']}: {'; '.join(lg['reasons'])})."]
    if not simple:
        L += [f"Functions (estimate) {fmt(lg['functions_estimate'])}; branch points per 1,000 lines {fmt(lg['branches_per_kloc'])}; "
              f"nesting depth (95th percentile) {fmt(lg['max_nesting_levels'])}; files nested 5+ deep {fmt(lg['files_nesting_5plus'])}."]
    c = doc.get("census")
    if c:
        L += ["", f"Reviewer census: weighted total {fmt(c.get('weighted_total'))} gives logic depth **{c['logic_depth']}/6**"
              + (f" (the reviewer's own judgement was {c['reviewer_judgement']}/6)" if c.get("reviewer_judgement") is not None else "")
              + f"; self-contained {c['self_contained']}/4" + (f"; model {c['model']}" if c.get("model") else "") + ".", "",
              "| category | count | weight |", "|---|---|---|"] + [
                  f"| {k.replace('_', ' ')} | {fmt(len(v))} | {CENSUS_WEIGHTS.get(k, '')} |" for k, v in c["categories"].items()]
        if c.get("defect_repairs_with_test") is not None:
            L.append(f"| defect repairs shipped with a test (bonus) | {fmt(c['defect_repairs_with_test'])} | {CENSUS_WEIGHTS['defect_repairs_with_test']} |")
        if c.get("themes"):
            L += ["", "Themes: " + "; ".join(c["themes"])]
    if not simple:
        cap = tk.get("capacity") or {}
        L += ["", "## 9. Capacity and task candidates", ""]
        if cap:
            r_ = cap["rule"]
            L += [f"Mineable commits (capacity): **{fmt(cap['mineable_commits'])}**: non-merge, non-bot, not a chore, "
                  f"{r_['min_impl_files']}+ first-party non-test source files, {r_['min_churn']:,}-{r_['max_churn']:,} lines changed, "
                  f"at most {r_['max_files']} files in {r_['max_dirs']} folders. Excluded: {fmt(cap['chore_commits_excluded'])} chores, "
                  f"{fmt(cap['too_spread_commits_excluded'])} too spread out. Fix subjects without deleted lines (not counted as fixes): "
                  f"{fmt(cap['fix_subjects_not_corroborated'])}. Fixes shipped with a test: {fmt(cap['bugfix_with_regression_test'])}. "
                  f"Bulk test-add commits: {fmt(cap['bulk_test_commits'])}.", ""]
        L += ["| kind | mineable | with tests | source only | all commits |", "|---|---|---|---|---|"]
        for k in TASK_KINDS:
            L.append(f"| {k} | {fmt((cap.get('mineable_by_kind') or {}).get(k))} | {fmt((tk['candidates'] or {}).get(k))} | "
                     f"{fmt((tk['source_only_candidates'] or {}).get(k))} | {fmt((tk['commit_kinds'] or {}).get(k))} |")
        tm = doc.get("task_mining") or {}
        L += ["", "## 10. Task mining", "", f"Status: **{tm.get('status')}**" + (f" ({tm['skip_reason'].replace('_', ' ')})" if tm.get("skip_reason") else "")]
        if tm.get("ideas_by_kind"):
            L += ["", "| kind | ideas |", "|---|---|"] + [f"| {k.replace('_', ' ')} | {fmt(v)} |" for k, v in tm["ideas_by_kind"].items()]
            L += [f"| total (cap {tm.get('cap')}) | {fmt(tm.get('ideas_total'))} |"]
        if c and c.get("ideas"):
            L += ["", "Generic task ideas:", ""] + [f"- {i['kind'].replace('_', ' ')}: {i['text']}" for i in c["ideas"]]
    L += ["", "## Lanes", ""] + [f"- {k}: {v}" for k, v in doc["lanes"].items()]
    pv = doc.get("provenance") or {}
    if pv:
        L += ["", "## Provenance", "", f"- head commit fingerprint `{pv.get('head_commit_fingerprint')}`; scripts digest `{pv.get('scripts_digest')}`; "
              f"payload digest `{pv.get('payload_digest')}`",
              "- A buyer can check these numbers on their own copy: `measure_code.py <repo> --verify measure.json`."]
    L += ["", "## Caveats", "",
          "- Authors are keyed on email; one person with several emails counts more than once.",
          "- Commit kinds come from message keywords plus the diff shape (a fix must delete lines); they are signals, not verified labels.",
          "- Functions and nesting come from regexes and indentation, not a parser.",
          "- AI-assisted counts see only known agent addresses and product names in trailers and accounts; tools that leave neither are invisible.",
          "- Dates are author dates and can be rewritten by rebases or imports.", ""]
    L += dream_mod().section(doc.get("dream"))
    return "\n".join(L)


# --------------------------------------------------------------------------
# provenance and verification
# --------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent


def dream_mod():
    """add_dream.py next to this script: the dream slot, its validation and its report section."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("add_dream", SCRIPT_DIR / "add_dream.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def scripts_digest() -> str:
    hh = hashlib.sha256()
    for name in ("measure_code.py", "add_census.py", "add_dream.py"):
        f = SCRIPT_DIR / name
        if f.exists():
            hh.update(f.read_bytes())
    return hh.hexdigest()[:32]


def payload_digest(doc: dict) -> str:
    d = json.loads(json.dumps(doc))
    (d.get("provenance") or {}).pop("payload_digest", None)
    return hashlib.sha256(json.dumps(d, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32]


STABLE_FIELDS = ("git.commits", "git.merge_commits", "git.authors_human", "git.authors_bot", "git.first_commit", "git.last_commit",
                 "git.pre_cutoff_commits", "git.pr_merged_count", "git.ai_coauthored_commits", "tree.code_lines_total",
                 "tree.source_files", "tree.tracked_files", "tests.test_files", "tests.tests_declared", "tasks.candidates_total",
                 "tasks.capacity.mineable_commits", "fingerprints.root_commits", "fingerprints.commit_sketch",
                 "fingerprints.content_sketch", "provenance.head_commit_fingerprint", "provenance.measured_commit_fingerprint",
                 "code_age.share_written_since_cutoff")


def dig(doc, path):
    cur = doc
    for k in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def verify(claimed_path: Path, repo: Path, a) -> int:
    claimed = json.loads(claimed_path.read_text("utf-8"))
    pv = claimed.get("provenance") or {}
    lines, ok = [], True
    integ = pv.get("payload_digest") == payload_digest(claimed)
    lines.append(("payload digest (file unchanged since it was written)", "match" if integ else "MISMATCH"))
    ok &= integ
    same_tool = pv.get("scripts_digest") == scripts_digest()
    lines.append(("scripts digest (same measure-code scripts)", "match" if same_tool else
                  f"different scripts (file made with {claimed.get('tool_version')}, this copy is {VERSION}); figures may differ for that reason"))
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="measure-code-verify-"))
    try:
        argv = [str(repo), "--out", str(tmp / "out"), "--mode", claimed.get("mode", "full"), "--cutoff", claimed.get("cutoff", "2025-01-01"),
                "--label", claimed.get("label") or "verify", "--build", "detect", "--force",
                "--blame-seconds", str((claimed.get("code_age") or {}).get("budget_seconds") or 0)]
        policy = (claimed.get("history_ref") or {}).get("policy")
        if policy == "all":
            argv.append("--all-refs")
        elif policy in ("auto", "head"):
            argv += ["--ref", policy]
        rc = main(argv)
        if rc != 0:
            print("verify: re-measurement failed")
            return 2
        fresh = json.loads((tmp / "out" / "measure.json").read_text("utf-8"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for f in STABLE_FIELDS:
        x, y = dig(claimed, f), dig(fresh, f)
        if f == "code_age.share_written_since_cutoff" and (x is None or y is None):
            lines.append((f, "skipped (blame incomplete on one side)"))
            continue
        same = x == y
        ok &= same
        lines.append((f, "match" if same else f"MISMATCH (file {str(x)[:40]}, re-measured {str(y)[:40]})"))
    lines.append(("census and task mining", "not re-derivable: judgement lane, check the sentences by reading the code"))
    if (claimed.get("dream") or {}).get("status") == "written":
        lines.append(("why this is special", "not re-derivable: a labelled pitch written by the agent, not a measurement"))
    print(f"VERIFY {claimed_path.name} against a fresh measurement")
    for k, v in lines:
        print(f"  {k}: {v}")
    print("verify: " + ("all stable figures match" if ok else "differences found"))
    return 0 if ok else 1


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

IDEA_KINDS = ("net_new", "bug_repair", "repo_evolution", "long_horizon")
CENSUS_WEIGHTS = {"complex_logic": 3.0, "substantial_features": 2.5, "work_units": 2.0, "integrations": 1.5,
                  "performance_work": 1.5, "hardening_work": 1.0, "net_new_capabilities": 1.0, "defect_repairs": 0.75,
                  "repo_evolutions": 0.75, "defect_repairs_with_test": 0.5}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Measure one git repository privately (counts only).")
    ap.add_argument("repo", type=Path)
    ap.add_argument("--out", type=Path, help="output folder (outside the repo); required unless --verify")
    ap.add_argument("--mode", choices=["simple", "full"], default="full",
                    help="simple = fast listing and metadata, sampled, every figure an estimate (recommended first run); full = every lane (default)")
    ap.add_argument("--label", help="name shown in the output (default: folder name)")
    ap.add_argument("--tag", action="append", default=[], help="cross-linking tag, e.g. project:alpha (repeatable)")
    ap.add_argument("--cutoff", default="2025-01-01",
                    help="date for pre-cutoff shares (default 2025-01-01: buyers price work written before 2025)")
    ap.add_argument("--ref", choices=["auto", "head"], default="auto",
                    help="auto (default) = measure history on the deepest branch when it is clearly deeper than HEAD; head = HEAD only")
    ap.add_argument("--all-refs", action="store_true", help="count the union of every branch and tag")
    ap.add_argument("--sample-files", type=int, default=400, help="simple mode: source files to read")
    ap.add_argument("--build", choices=["detect", "discover", "full"], default="detect",
                    help="full mode: detect = read manifests (default); discover = build and list tests; full = build and run tests. "
                         "discover/full EXECUTE repo code: use a disposable clone")
    ap.add_argument("--install", action="store_true",
                    help="with --build discover/full: install dependencies from the lockfile first. USES THE NETWORK and runs "
                         "install scripts, with a scratch HOME and credential-like environment variables removed")
    ap.add_argument("--max-roots", type=int, default=3, help="build probe: project roots to probe, largest first")
    ap.add_argument("--test-timeout", type=int, default=900)
    ap.add_argument("--blame-seconds", type=int, default=300, help="full mode: time budget for the code-age lane (0 = skip)")
    ap.add_argument("--verify", type=Path, metavar="MEASURE_JSON",
                    help="re-measure the repo and check an existing measure.json (digests, fingerprints, stable counts)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--review", action="store_true", help="print every value in the output")
    ap.add_argument("--no-dream", action="store_true",
                    help="skip dream mode: no 'Why this is special' pitch is written after the audit")
    a = ap.parse_args(argv)

    repo = a.repo.expanduser().resolve()
    if not repo.is_dir():
        ap.error("repository folder not found")
    if a.verify:
        return verify(a.verify.expanduser().resolve(), repo, a)
    if not a.out:
        ap.error("--out is required")
    out = a.out.expanduser().resolve()
    if out == repo or repo in out.parents:
        ap.error("--out must be outside the repository")
    out.mkdir(parents=True, exist_ok=True)
    simple = a.mode == "simple"
    st = Stages(out, a.force, a.mode)
    lanes, reasons, info = {}, [], {}
    state = repo_state(repo)
    pathspec = ["--", "."] if state["subtree"] else []
    ref = None
    head_fp = measured_fp = None
    if not state["is_git"]:
        gitres = empty_history("not_a_git_repository")
        reasons.append("not_a_git_repository")
        lanes["history"] = "not run: the folder is not a git repository; only the file tree was measured"
    elif not state["has_commits"]:
        gitres = empty_history("no_commits")
        reasons.append("no_commits")
        lanes["history"] = "not run: the repository has no commits yet"
    else:
        ref = st.run("ref", lambda: choose_ref(repo, "all" if a.all_refs else a.ref, pathspec))
        gitres = st.run("git", lambda: stage_git(repo, a.cutoff, ref["_rev"], not simple, pathspec))
        lanes["history"] = ("metadata only" if simple else "full census with lines changed") + (
            "; folder inside a larger repository, history limited to this folder" if state["subtree"] else "")
        if gitres.get("history_shallow"):
            reasons.append("shallow_clone")
        try:
            head_fp = h("head:" + git(repo, "rev-parse", "HEAD").strip(), 32)
            measured_fp = h("head:" + git(repo, "rev-parse", ref["_rev"][0]).strip(), 32) if ref["_rev"][0] != "--all" else None
        except RuntimeError:
            pass
    tree = st.run("tree", lambda: stage_tree(repo, a.sample_files if simple else None))
    lanes["tree"] = "sampled (estimate)" if simple else "every file read"
    tests = dict(tree["_tests"])
    names = [p_ for p_, _ in list_files(repo)]
    tests["frameworks"] = detect_test_frameworks(repo, names)
    if simple:
        signals = None
        build = stage_build(repo, "detect", 0, tests)
        build["reason"] = "simple mode: no build probe"
        lanes["build"] = "not run (simple mode)"
        code_age = None
        lanes["code_age"] = "not run (simple mode)"
    else:
        signals = st.run("signals", lambda: stage_signals(repo))
        signals["_notebooks"] = tree["_sourcing"]["notebooks"]
        build = st.run(f"build-{a.build}{'-install' if a.install else ''}",
                       lambda: stage_build(repo, a.build, a.test_timeout, tests, a.install, a.max_roots, tests["frameworks"]))
        lanes["build"] = a.build + (" with dependency install (network used)" if a.install else "")
        if a.build == "detect":
            info["build"] = "not_requested"
        elif build["status"] in BUILD_REASON:
            reasons.append(BUILD_REASON[build["status"]])
        if a.blame_seconds > 0 and state["has_commits"] and not gitres.get("history_shallow"):
            code_age = st.run("blame", lambda: stage_blame(repo, a.cutoff, a.blame_seconds))
            lanes["code_age"] = "complete" if code_age["complete"] else \
                f"partial ({code_age['partial_reason'].replace('_', ' ')}; {code_age['coverage_share']:.0%} of lines blamed; no whole-repo share)"
            if not code_age["complete"]:
                reasons.append("code_age_partial")
        else:
            code_age = None
            lanes["code_age"] = "skipped"
            if state["has_commits"] and not gitres.get("history_shallow"):
                reasons.append("code_age_not_run")
    logic = dict(tree["_logic"])
    band, lreasons, lscore = logic_band(logic, tests)
    census = None
    if not simple:
        cp = out / "census.json"
        if cp.exists():
            census = json.loads(cp.read_text("utf-8"))
            lanes["review"] = "census recorded (the session's AI model read the source)"
        else:
            lanes["review"] = "not run: a full run needs the reviewer census and task mining (see add_census.py)"
            reasons.append("census_not_run")
    reviewer = census.get("logic_depth") if census else None
    logic.update(logic_depth=reviewer if reviewer is not None else band,
                 authoritative="reviewer_census" if reviewer is not None else "deterministic",
                 deterministic_depth=band, deterministic_score=lscore, reasons=lreasons,
                 reviewer_depth=reviewer, reviewer_judgement=census.get("reviewer_judgement") if census else None)
    if simple:
        task_mining = {"status": "not_run", "skip_reason": "simple_mode", "ideas_total": None, "ideas_by_kind": None, "cap": 150}
    elif census:
        tmc = census.get("task_mining") or {}
        task_mining = {"status": tmc.get("status", "done" if census.get("ideas") else "skipped"),
                       "skip_reason": tmc.get("skip_reason"), "ideas_total": len(census.get("ideas") or []),
                       "ideas_by_kind": census.get("ideas_by_kind") or {k: sum(1 for i in census.get("ideas") or [] if i.get("kind") == k) for k in IDEA_KINDS},
                       "cap": 150, "grounded_ideas": tmc.get("grounded_ideas"), "dropped_ideas": tmc.get("dropped_ideas"),
                       "kinds_note": "net_new = new capability; bug_repair = a defect fix; repo_evolution = refactor, migration or "
                                     "performance; long_horizon = multi-step work across modules"}
        if task_mining["status"] != "done":
            reasons.append("task_mining_skipped")
    else:
        task_mining = {"status": "not_run", "skip_reason": "census_not_run", "ideas_total": None, "ideas_by_kind": None, "cap": 150}
    lanes["task_mining"] = task_mining["status"] + (f" ({task_mining['skip_reason'].replace('_', ' ')})" if task_mining.get("skip_reason") else "")
    cl = classify(tree["languages"], signals or {"frameworks": []}, build, gitres)
    reasons = [r for r in STATUS_REASONS if r in reasons or (r == "simple_mode" and simple)]
    status = "estimate" if simple else ("measured" if not reasons else "partial")
    ref_block = {k: v for k, v in ref.items() if not k.startswith("_")} if ref else None

    doc = {
        "schema": SCHEMA, "kind": "code", "tool": TOOL, "tool_version": VERSION, "mode": a.mode,
        "status": status, "estimate": simple, "status_reasons": reasons, "skip_reason": reasons[0] if reasons else None,
        "status_info": info,
        "measured_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "label": a.label or repo.name, "tags": a.tag, "cutoff": a.cutoff,
        "span": {"first": gitres["first_commit"], "last": gitres["last_commit"]},
        "lanes": lanes, "history_ref": ref_block,
        "fingerprints": dict(gitres["_fingerprints"], content_sketch=tree["_content_sketch"]),
        "git": {k: v for k, v in gitres.items() if not k.startswith("_")},
        "tree": {k: v for k, v in tree.items() if not k.startswith("_")},
        "tests": tests, "build": build, "logic": logic, "classification": cl,
        "tasks": task_block(gitres["_task_signals"], "not run in simple mode" if simple else
                            "not run: history is truncated or missing" if not state["has_commits"] or gitres.get("history_shallow") else ""),
        "task_mining": task_mining,
    }
    if not simple:
        sig = {k: v for k, v in signals.items() if not k.startswith("_")}
        sig.update(tree["_observability"] and {"health_endpoint": tree["_observability"]["health_endpoint"],
                                               "metrics": tree["_observability"]["metrics"]})
        doc["signals"] = sig
        doc["sourcing"] = tree["_sourcing"]
        doc["authenticity"] = dict(tree["_authenticity"], mock_calls=tests.pop("mock_calls"), assertions=tests.pop("assertions"))
        if gitres["_root_commit_files"] is not None and tree["tracked_files"] and gitres.get("history_complete") is not False:
            share = round(min(1.0, gitres["_root_commit_files"] / tree["tracked_files"]), 3)
            doc["authenticity"]["initial_commit_file_share"] = share
            doc["authenticity"]["looks_like_bulk_import"] = share >= 0.8 and (gitres["commits"] or 0) < 30
        doc["code_age"] = code_age
        doc["census"] = census
    else:
        tests.pop("mock_calls", None)
        tests.pop("assertions", None)
    doc["recommendation"] = recommend(doc)
    doc["privacy"] = {"authors": "counted, never named", "paths": "not emitted", "messages": "not emitted",
                      "review_lane": "the reviewer census is written by the AI model of the session running the skill, "
                                     "which reads the source under the user's own account; the script itself makes no network calls"}
    deny = author_denylist(repo) if state["is_git"] else set()
    dm = dream_mod()
    doc["dream"] = dm.pending()
    doc["dream"] = dm.resolve(out, dm.ground_text(doc, render_report(doc)), a.no_dream, deny)
    lanes["dream"] = {"written": "why this is special written by the agent after the audit (not a measurement)",
                      "skipped": "skipped (--no-dream)", "pending": "pending: the agent writes it after the audit (add_dream.py)",
                      "needs_rewrite": "the saved pitch no longer matches these numbers; write it again"}[doc["dream"]["status"]]
    doc["provenance"] = {"head_commit_fingerprint": head_fp, "measured_commit_fingerprint": measured_fp,
                         "scripts_digest": scripts_digest(), "payload_digest": None}
    doc["provenance"]["payload_digest"] = payload_digest(doc)

    problems = boundary(doc, deny)
    if problems:
        log("output boundary refused the document, nothing written:")
        for p_ in problems[:20]:
            log("  " + p_)
        return 2
    row = summary_row(doc)
    atomic_write(out / "measure.json", json.dumps(doc, indent=2))
    atomic_write(out / "summary_row.json", json.dumps(row, indent=2))
    with (out / "summary_row.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        w.writeheader()
        w.writerow(row)
    atomic_write(out / "report.md", render_report(doc))
    log(f"wrote measure.json, summary_row.json/.csv and report.md to {out}")
    log(f"status: {status}" + (f" ({', '.join(reasons)})" if reasons else ""))
    log(f"full run: {doc['recommendation']['full_run']}")
    kinds = Counter(type(v).__name__ for _, v in leaves(doc))
    print(f"REVIEW: {sum(kinds.values())} values ({', '.join(f'{k} {n}' for k, n in kinds.items())}); "
          f"never collected: author names and emails, file paths and names, commit messages, branch and tag names, "
          f"environment variable names, secrets, source code")
    if doc["dream"]["status"] == "written":
        print(f"  model-written: dream.text = {doc['dream']['text']}")
    if census:
        for path, v in leaves(doc):
            if path.startswith("census") and isinstance(v, str) and path not in ("census.reviewer", "census.model", "census.weights_version"):
                print(f"  model-written: {path} = {v}")
    if a.review:
        for path, v in leaves(doc):
            if not path.startswith("fingerprints"):
                print(f"{path} = {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
