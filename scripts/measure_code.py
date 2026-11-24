#!/usr/bin/env python3
"""measure-code: measure one git repository privately.

Two modes:

  --mode simple   listing and metadata only: history without file lists, a sampled
                  line count, path-based signals. No build, no blame, no review.
                  Minutes even on large repos. Says whether a full run is worth it.
  --mode full     (default) every lane: full history census, every file read,
                  hygiene and sourcing signals, code age by blame, build probe
                  (detect by default; discover/full execute repo code), and the
                  optional reviewer census written with add_census.py.

Writes into --out:

  measure.json          all counts (schema october.measure/v1, kind "code")
  summary_row.json/.csv one flat row per repository, for spreadsheets
  report.md             the same numbers for people

No network calls are made by this script (build commands run with offline
settings). Authors are counted, never named. Paths, commit messages, branch
names and emails never reach the output: every string in the output must sit
in a declared field, and the run refuses to write anything else.

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
VERSION = "0.2.0"
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
AI_TRAILER_RX = re.compile(r"(claude|copilot|cursor|codex|chatgpt|openai|gemini|devin|aider|sweep|openhands|windsurf|codeium)", re.I)
AI_AGENT_AUTHOR_RX = re.compile(r"(claude|copilot-swe|devin-ai|cursor-agent|codex|openhands|sweep-ai|google-labs-jules)", re.I)
CONVENTIONAL_RX = re.compile(r"^(feat|fix|chore|docs|refactor|test|tests|perf|build|ci|style|revert)(\([^)]*\))?!?:")
PR_RX = re.compile(r"(?:Merge pull request #(\d+)|\(#(\d+)\)\s*$|Merged in .*\(pull request #(\d+)\)|See merge request .*!(\d+))")
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

BOT_RX = re.compile(r"(\[bot\]|^bot@|-bot@|dependabot|renovate|github-actions|gitlab-ci|noreply@.*ci|snyk)", re.I)
FIX_RX = re.compile(r"\b(fix|fixes|fixed|bug|bugfix|hotfix|patch|crash|regression|issue)\b", re.I)
FEAT_RX = re.compile(r"\b(feat|feature|add|adds|added|implement|implements|introduce|support)\b", re.I)
REFAC_RX = re.compile(r"\b(refactor|cleanup|clean up|rename|restructure|migrate|upgrade|bump|deprecate)\b", re.I)
PERF_RX = re.compile(r"\b(perf|performance|optimi[sz]e|speed ?up|faster|cache)\b", re.I)


def commit_kind(subject: str) -> str:
    if FIX_RX.search(subject):
        return "bugfix"
    if PERF_RX.search(subject) or REFAC_RX.search(subject):
        return "restructure"
    if FEAT_RX.search(subject):
        return "feature"
    return "exploratory"


def stage_git(repo: Path, cutoff: str, all_refs: bool, with_files: bool) -> dict:
    """with_files=False is the simple-mode path: no per-commit file lists, so no task census."""
    sep, rec = "\x1f", "\x1e"
    fmt = rec + sep.join(["%H", "%P", "%ae", "%an", "%at", "%ct", "%ce", "%s",
                          "%(trailers:key=Co-authored-by,valueonly,separator=%x1d)"])
    args = ["log", f"--format={fmt}", "--date-order"]
    if with_files:
        args += ["--name-only", "--no-renames"]
    if all_refs:
        args.append("--all")
    raw = git(repo, *args)
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
    prs = set()
    conventional = recent_subjects = 0
    for block in raw.split(rec):
        if not block.strip():
            continue
        head, _, files_blob = block.partition("\n")
        fields = head.split(sep)
        if len(fields) < 9:
            continue
        sha, parents, email, name, at, ct, cemail, subject, trailers = fields[:9]
        commits += 1
        shas.append(sha)
        plist = parents.split()
        files = [f for f in files_blob.splitlines() if f.strip()] if with_files else []
        if not plist:
            roots.append(sha)
            root_files += len(files)
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
        if trailers and AI_TRAILER_RX.search(trailers):
            ai_coauthored += 1
        if AI_AGENT_AUTHOR_RX.search(email) or AI_AGENT_AUTHOR_RX.search(name) or AI_AGENT_AUTHOR_RX.search(cemail):
            ai_agent += 1
        m = PR_RX.search(subject)
        if m:
            prs.add(next(g for g in m.groups() if g))
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
        src = [f for f in files if Path(f).suffix.lower() in LANG_BY_EXT and not vendor_kind(f)]
        tests = [f for f in src if is_test_path(f)]
        src_only = [f for f in src if not is_test_path(f)]
        kind = commit_kind(subject)
        task[f"{kind}_commits"] += 1
        if tests and src_only and 0 < len(files) <= 20:
            task["with_tests_total"] += 1
            task[f"{kind}_with_tests"] += 1
        elif src_only and 0 < len(files) <= 20:
            task[f"{kind}_source_only"] += 1
    counts = sorted(per_author.values(), reverse=True)
    total_h = sum(counts) or 1
    try:
        shallow = git(repo, "rev-parse", "--is-shallow-repository").strip() == "true"
        tag_names = [t for t in git(repo, "tag", "--list").splitlines() if t.strip()]
        branches = len([b for b in git(repo, "branch", "--all", "--format=%(refname)").splitlines() if b.strip()])
        since = (dt.datetime.fromtimestamp(last_ts or time.time(), tz=dt.timezone.utc) - dt.timedelta(days=365)).timestamp()
        last12 = int(git(repo, "rev-list", "--count", f"--since={int(since)}", "--all" if all_refs else "HEAD").strip() or 0)
    except RuntimeError:
        shallow, tag_names, branches, last12 = None, [], None, None
    today = dt.datetime.now(dt.timezone.utc)
    return {
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
        "pre_cutoff_commits": pre_cutoff, "pre_cutoff_share": round(pre_cutoff / commits, 3) if commits else None,
        "commits_last_12_months": last12, "tags": len(tag_names),
        "semver_tags": sum(1 for t in tag_names if re.match(r"^v?\d+\.\d+(\.\d+)?", t)), "branches": branches,
        "root_commit_count": len(roots), "history_shallow": shallow, "history_single_commit": commits == 1,
        "conventional_commit_share_last_200": round(conventional / recent_subjects, 3) if recent_subjects else None,
        "pr_merged_count": len(prs) if not shallow else None,
        "ai_coauthored_commits": ai_coauthored, "ai_agent_commits": ai_agent,
        "ai_assisted_share": round((ai_coauthored + ai_agent) / commits, 3) if commits else None,
        "_root_commit_files": root_files if with_files else None,
        "_fingerprints": {"root_commits": sorted(h("root:" + r) for r in roots), "commit_sketch": bottom_k(shas)},
        "_task_signals": dict(task) if with_files else None,
    }


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
        "demo_words": {w: True for w in ("tutorial", "boilerplate", "starter", "template", "course", "homework", "example project", "hello world")
                       if w in readme_text.lower()},
    }


# --------------------------------------------------------------------------
# stage: build probe
# --------------------------------------------------------------------------

OFFLINE_ENV = {"CI": "1", "GOFLAGS": "-mod=mod", "GOPROXY": "off", "CARGO_NET_OFFLINE": "true", "npm_config_offline": "true",
               "PIP_NO_INDEX": "1", "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "NUGET_PACKAGES": os.environ.get("NUGET_PACKAGES", "")}
BUILD_CMDS = {
    "npm": (["npm", "run", "build", "--if-present"], ["npm", "test", "--silent"]),
    "python": ([sys.executable, "-m", "compileall", "-q", "."], [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]),
    "go": (["go", "build", "./..."], ["go", "test", "-v", "./..."]),
    "cargo": (["cargo", "build", "--offline"], ["cargo", "test", "--offline"]),
    "maven": (["mvn", "-o", "-q", "compile"], ["mvn", "-o", "test"]),
    "gradle": (["./gradlew", "--offline", "assemble"], ["./gradlew", "--offline", "test"]),
    "dotnet": (["dotnet", "build", "--no-restore"], ["dotnet", "test", "--no-restore", "--no-build"]),
}
RESULT_PARSERS = [  # (passed, failed) from a test runner's summary, counts only
    (re.compile(r"(\d+) passed"), re.compile(r"(\d+) failed")),                                  # pytest, jest, vitest
    (re.compile(r"test result: \w+\. (\d+) passed"), re.compile(r"; (\d+) failed")),            # cargo
    (re.compile(r"Passed:\s+(\d+)"), re.compile(r"Failed:\s+(\d+)")),                             # dotnet
]


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
    return None, None


def stage_build(repo: Path, level: str, timeout: int, signals: dict, tests: dict) -> dict:
    names = {p for p, _ in list_files(repo)}
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
    out = {
        "level": level, "build_systems": systems, "engine": engine, "engine_version": engine_version,
        "host_os": {"Darwin": "darwin", "Linux": "linux", "Windows": "windows"}.get(platform.system(), "other"),
        "tests_declared": tests.get("tests_declared"),
        "dependencies_present": None, "build_ok": None, "tests_ran": None, "tests_passed": None, "tests_failed": None,
        "test_pass_rate": None, "runnability": None, "reason": None, "timed_out": False, "seconds": 0,
    }
    if level == "detect":
        out["reason"] = "detect level runs nothing"
        return out
    runner = next((s for s in ("npm", "python", "go", "cargo", "dotnet", "maven", "gradle") if s in systems), None)
    if runner is None or engine in ("unity", "unreal"):
        out["reason"] = "engine projects need the engine editor" if engine else "no supported build system"
        return out
    build_cmd, test_cmd = BUILD_CMDS[runner]
    if shutil.which(build_cmd[0]) is None and not build_cmd[0].startswith("./"):
        out["reason"] = "toolchain not installed on this machine"
        return out
    deps_present = {"npm": (repo / "node_modules").is_dir(), "python": True, "go": True, "cargo": True}.get(runner, True)
    out["dependencies_present"] = deps_present
    if not deps_present:
        out["reason"] = "dependencies are not installed; install them in the clone, then re-run (this script never downloads)"
        return out
    env = {**os.environ, **OFFLINE_ENV}
    t0 = time.time()

    def run(cmd):
        try:
            p = subprocess.run(cmd, cwd=repo, capture_output=True, timeout=timeout, check=False, env=env)
            return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")[-200_000:]
        except subprocess.TimeoutExpired:
            out["timed_out"] = True
            return None, ""
        except OSError:
            return None, ""

    code, _ = run(build_cmd)
    out["build_ok"] = None if code is None else code == 0
    if level == "full" and out["build_ok"]:
        code, text = run(test_cmd)
        out["tests_ran"] = code is not None
        passed, failed = parse_results(text, runner)
        out["tests_passed"], out["tests_failed"] = passed, failed
        if passed is not None and (passed + (failed or 0)):
            out["test_pass_rate"] = round(passed / (passed + (failed or 0)), 3)
    out["seconds"] = round(time.time() - t0, 1)
    if out["build_ok"] is None:
        out["reason"] = "build timed out" if out["timed_out"] else "build could not start"
    else:  # 0-4: builds, declares tests, ran a suite, 90%+ of tests pass
        out["runnability"] = (int(bool(out["build_ok"])) + int(bool(tests.get("tests_declared")))
                              + int(bool(out["tests_ran"])) + int((out["test_pass_rate"] or 0) >= 0.9))
        if level == "discover":
            out["reason"] = "discover level builds only; runnability counts at most 2"
    return out


# --------------------------------------------------------------------------
# stage: code age by blame (how much of today's code was written before the cutoff)
# --------------------------------------------------------------------------


def stage_blame(repo: Path, cutoff: str, budget: int) -> dict:
    cutoff_ts = dt.datetime.strptime(cutoff, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
    files = []
    for rel, _ in list_files(repo):
        lang = LANG_BY_EXT.get(Path(rel).suffix.lower())
        if lang and not vendor_kind(rel) and not is_test_path(rel):
            try:
                files.append(((repo / rel).stat().st_size, rel))
            except OSError:
                pass
    files.sort(reverse=True)  # biggest first, so a cut-off budget still covers most lines
    t0 = time.time()
    blamed = after = done = 0
    for _, rel in files:
        if time.time() - t0 > budget:
            break
        try:
            raw = git(repo, "blame", "--line-porcelain", "-w", "HEAD", "--", rel, timeout=max(5, int(budget - (time.time() - t0))))
        except (RuntimeError, subprocess.TimeoutExpired):
            continue
        times = [int(x) for x in re.findall(r"^author-time (\d+)$", raw, re.M)]
        blamed += len(times)
        after += sum(1 for t in times if t >= cutoff_ts)
        done += 1
    return {"files_blamed": done, "files_total": len(files), "lines_blamed": blamed,
            "lines_written_since_cutoff": after,
            "share_written_since_cutoff": round(after / blamed, 3) if blamed else None,
            "complete": done == len(files), "budget_seconds": budget}


# --------------------------------------------------------------------------
# derived: classification, logic band, tasks, recommendation
# --------------------------------------------------------------------------


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
    demo_reasons = []
    if git_.get("commits", 0) < 15:
        demo_reasons.append("fewer than 15 commits")
    if git_.get("history_single_commit"):
        demo_reasons.append("single-commit snapshot")
    if signals.get("demo_words"):
        demo_reasons.append("readme describes a tutorial, template or starter")
    if total < 1500:
        demo_reasons.append("under 1,500 lines of code")
    return {"primary_class": primary,
            "class_scores": {k: round(v / tot, 2) for k, v in score.most_common() if v > 0},
            "is_monorepo": bool(ws.get("monorepo_tooling")) or ws.get("package_roots", 0) >= 4,
            "is_likely_demo": len(demo_reasons) >= 2, "demo_reasons": demo_reasons}


def logic_band(logic: dict, tests: dict) -> tuple:
    loc = logic.get("source_code_lines") or 0
    reasons, band = [], 0
    for cond, why in ((loc >= 2_000, "2k+ source lines"), (loc >= 20_000, "20k+ source lines"),
                      ((logic.get("branches_per_kloc") or 0) >= 80, "dense branching"),
                      (logic.get("files_nesting_5plus", 0) >= 3, "several deeply nested files"),
                      (logic.get("top_level_modules", 0) >= 5, "5+ top-level modules"),
                      ((tests.get("test_to_source_line_ratio") or 0) >= 0.1, "tests cover 10%+ of source lines")):
        if cond:
            band += 1
            reasons.append(why)
    return min(band, 6), reasons


def task_block(sig: dict | None) -> dict:
    if sig is None:
        return {"method": "not run in simple mode", "candidates": None, "candidates_total": None,
                "source_only_candidates": None, "commit_kinds": None}
    g = lambda k: sig.get(k, 0)  # noqa: E731
    return {
        "method": "deterministic commit census (message keywords + files touched); counts only",
        "candidates": {k: g(f"{k}_with_tests") for k in TASK_KINDS},
        "candidates_total": g("with_tests_total"),
        "source_only_candidates": {k: g(f"{k}_source_only") for k in TASK_KINDS},
        "commit_kinds": {k: g(f"{k}_commits") for k in TASK_KINDS},
    }


def recommend(doc: dict) -> dict:
    g, t = doc["git"], doc["tree"]
    reasons, score = [], 0
    for cond, why in ((g["commits"] >= 200, "200+ commits of history"),
                      ((t["code_lines_total"] or 0) >= 10_000, "10k+ lines of code"),
                      ((g["pre_cutoff_share"] or 0) >= 0.3, "30%+ of history predates the cutoff"),
                      (g["authors_human"] >= 2, "more than one human author"),
                      ((doc["tests"]["test_files"] or 0) > 0, "has tests"),
                      (not doc["classification"]["is_likely_demo"], "not a demo or template")):
        if cond:
            score += 1
            reasons.append(why)
    verdict = "recommended" if score >= 5 else "optional" if score >= 3 else "not worth it"
    minutes = 1 + (t["code_lines_total"] or 0) / 150_000 + g["commits"] / 40_000 + 5
    return {"full_run": verdict, "reasons": reasons, "estimated_full_run_minutes": int(round(minutes))}


# --------------------------------------------------------------------------
# output boundary: every string must sit in a declared field
# --------------------------------------------------------------------------

STRING_FIELDS = [re.compile(p) for p in (
    r"^schema$", r"^kind$", r"^tool$", r"^tool_version$", r"^mode$", r"^status$", r"^measured_at$", r"^label$",
    r"^tags\[\d+\]$", r"^cutoff$", r"^span\.(first|last)$", r"^git\.(first|last)_commit$",
    r"^fingerprints\.\w+\[\d+\]$", r"^tree\.mode_note$", r"^lanes\.\w+$", r"^tests\.frameworks\[\d+\]$",
    r"^build\.(level|build_systems\[\d+\]|engine|engine_version|host_os|reason)$",
    r"^signals\.(frameworks|ci\.systems|hygiene\.\w+|dependencies\.(package_managers|lockfiles)|observability\.\w+|workspaces\.monorepo_tooling)\[\d+\]$",
    r"^classification\.(primary_class|demo_reasons\[\d+\])$", r"^logic\.reasons\[\d+\]$", r"^tasks\.method$",
    r"^census\.(themes\[\d+\]|ideas\[\d+\]\.(kind|text)|categories\.\w+\[\d+\]|summary|reviewer)$",
    r"^recommendation\.(full_run|reasons\[\d+\])$", r"^privacy\.\w+$",
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
        if path.startswith("census") or path == "build.engine_version":
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
    return out


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


def ratio(x) -> str:
    return "n/a" if x is None else f"{x:.2f}"


def summary_row(doc: dict) -> dict:
    g, t, ts, b, lg, tk, c = doc["git"], doc["tree"], doc["tests"], doc["build"], doc["logic"], doc["tasks"], doc["classification"]
    s = doc.get("signals", {})
    row = {
        "label": doc["label"], "mode": doc["mode"], "status": doc["status"], "measured_at": doc["measured_at"],
        "primary_language": next(iter(t["languages"]), None), "languages": len(t["languages"]),
        "code_lines": t["code_lines_total"], "source_files": t["source_files"], "test_files": ts["test_files"],
        "test_code_lines": ts["test_code_lines"], "test_ratio": ts["test_to_source_line_ratio"], "tests_declared": ts.get("tests_declared"),
        "frameworks": ";".join(s.get("frameworks", [])) or None, "ci_present": bool(s.get("ci", {}).get("systems")) if s else None,
        "ci_runs_tests": s.get("ci", {}).get("runs_tests") if s else None,
        "commits": g["commits"], "authors_human": g["authors_human"], "first_commit": g["first_commit"],
        "last_commit": g["last_commit"], "span_days": g["span_days"], "active_days": g["active_days"],
        "active_months": g["active_months"], "pre_cutoff_share": g["pre_cutoff_share"],
        "code_written_since_cutoff_share": (doc.get("code_age") or {}).get("share_written_since_cutoff"),
        "pr_merged_count": g.get("pr_merged_count"), "ai_assisted_share": g.get("ai_assisted_share"),
        "primary_class": c["primary_class"], "is_monorepo": c["is_monorepo"], "is_likely_demo": c["is_likely_demo"],
        "build_ok": b.get("build_ok"), "runnability": b.get("runnability"), "logic_depth": lg["logic_depth"],
        "reviewer_logic_depth": (doc.get("census") or {}).get("logic_depth"),
        "task_candidates": tk["candidates_total"],
    }
    for k in TASK_KINDS:
        row[f"tasks_{k}"] = (tk["candidates"] or {}).get(k) if tk["candidates"] else None
    return row


def render_report(doc: dict) -> str:
    g, t, tests, b, lg, tk, cl = doc["git"], doc["tree"], doc["tests"], doc["build"], doc["logic"], doc["tasks"], doc["classification"]
    simple = doc["mode"] == "simple"
    rec = doc["recommendation"]
    L = [f"# Code measurement: {doc['label']}" + (" (simple mode)" if simple else ""), "",
         f"Measured {doc['measured_at'][:10]} with {TOOL} {VERSION}, {doc['mode']} mode. Counts only: no names, paths or messages."]
    if t.get("mode_note"):
        L += ["", f"_Estimate: {t['mode_note']}._"]
    L += ["", "## 1. Headline", "",
          f"- {fmt(g['commits'])} commits by {fmt(g['authors_human'])} people (+{fmt(g['authors_bot'])} bots), "
          f"{g['first_commit']} to {g['last_commit']} ({fmt(g['active_months'])} active months, {fmt(g['active_days'])} active days)",
          f"- {fmt(t['code_lines_total'])} lines of code{' (estimate)' if simple else ''} in {fmt(t['source_files'])} source files; "
          f"main language {next(iter(t['languages']), 'n/a')}; class {cl['primary_class']}",
          f"- Tests: {fmt(tests['test_files'])} files, {fmt(tests.get('tests_declared'))} test cases declared, "
          f"test/source line ratio {ratio(tests['test_to_source_line_ratio'])}",
          f"- {fmt(g['pre_cutoff_share'])} of commits are dated before {doc['cutoff']}"
          + (f"; {fmt(doc['code_age']['share_written_since_cutoff'])} of today's code lines were written after it"
             if doc.get("code_age") and doc["code_age"].get("share_written_since_cutoff") is not None else ""),
          f"- Logic depth band {lg['logic_depth']}/6" + ("" if simple else f"; {fmt(tk['candidates_total'])} commits change source and tests together"),
          *([f"- Full run: **{rec['full_run']}** ({'; '.join(rec['reasons']) or 'little to measure'}); about {rec['estimated_full_run_minutes']} minutes"]
            if simple else []), "",
          "## 2. History", "", "| metric | value |", "|---|---|"]
    for k in ("commits", "merge_commits", "authors_human", "authors_bot", "bot_commits", "authors_with_10plus_commits",
              "top1_author_share", "top5_author_share", "span_days", "recency_days", "active_days", "active_months",
              "commits_last_12_months", "pre_cutoff_commits", "pre_cutoff_share", "tags", "semver_tags", "branches",
              "pr_merged_count", "conventional_commit_share_last_200", "ai_coauthored_commits", "ai_agent_commits",
              "history_shallow"):
        L.append(f"| {k.replace('_', ' ')} | {fmt(g.get(k))} |")
    L += ["", "| year | commits |", "|---|---|"] + [f"| {y} | {fmt(n)} |" for y, n in g["commits_by_year"].items()]
    L += ["", "## 3. Lines by language", "", "| language | files | code | comments | blank |", "|---|---|---|---|---|"]
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
          f"- level: {b['level']}; build systems: {', '.join(b['build_systems']) or 'none'}; engine: {b['engine'] or 'none'} {b['engine_version'] or ''}".rstrip(),
          f"- build ok: {fmt(b.get('build_ok'))}; tests passed {fmt(b.get('tests_passed'))}, failed {fmt(b.get('tests_failed'))}; "
          f"runnability {fmt(b.get('runnability'))}/4" + (f"; note: {b['reason']}" if b.get("reason") else "")]
    L += ["", "## 7. Logic depth", "", f"Deterministic band **{lg['logic_depth']}/6** ({'; '.join(lg['reasons']) or 'small or flat codebase'})."]
    if not simple:
        L += [f"Functions (estimate) {fmt(lg['functions_estimate'])}; branch points per 1,000 lines {fmt(lg['branches_per_kloc'])}; "
              f"nesting depth (95th percentile) {fmt(lg['max_nesting_levels'])}; files nested 5+ deep {fmt(lg['files_nesting_5plus'])}."]
    c = doc.get("census")
    if c:
        L += ["", f"Reviewer census: logic depth **{c['logic_depth']}/6**, self-contained {c['self_contained']}/4.", "",
              "| category | count |", "|---|---|"] + [f"| {k.replace('_', ' ')} | {fmt(len(v))} |" for k, v in c["categories"].items()]
        if c.get("themes"):
            L += ["", "Themes: " + "; ".join(c["themes"])]
    if not simple:
        L += ["", "## 8. Task candidates", "", "Commits that change source and tests together (1-20 files), by kind.", "",
              "| kind | with tests | source only | all commits |", "|---|---|---|---|"]
        for k in TASK_KINDS:
            L.append(f"| {k} | {fmt(tk['candidates'][k])} | {fmt(tk['source_only_candidates'][k])} | {fmt(tk['commit_kinds'][k])} |")
        if c and c.get("ideas"):
            L += ["", "Generic task ideas:", ""] + [f"- {i['kind']}: {i['text']}" for i in c["ideas"]]
    L += ["", "## Lanes", ""] + [f"- {k}: {v}" for k, v in doc["lanes"].items()]
    L += ["", "## Caveats", "",
          "- Authors are keyed on email; one person with several emails counts more than once.",
          "- Commit kinds come from message keywords; they are signals, not verified labels.",
          "- Functions and nesting come from regexes and indentation, not a parser.",
          "- AI-assisted counts see only trailers and agent accounts; tools that leave neither are invisible.",
          "- Dates are author dates and can be rewritten by rebases or imports.", ""]
    return "\n".join(L)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Measure one git repository privately (counts only).")
    ap.add_argument("repo", type=Path)
    ap.add_argument("--out", type=Path, required=True, help="output folder (outside the repo)")
    ap.add_argument("--mode", choices=["simple", "full"], default="full",
                    help="simple = fast listing and metadata, sampled (recommended first run); full = every lane (default)")
    ap.add_argument("--label", help="name shown in the output (default: folder name)")
    ap.add_argument("--tag", action="append", default=[], help="cross-linking tag, e.g. project:alpha (repeatable)")
    ap.add_argument("--cutoff", default="2025-01-01")
    ap.add_argument("--all-refs", action="store_true", help="count every branch and tag, not only HEAD")
    ap.add_argument("--sample-files", type=int, default=400, help="simple mode: source files to read")
    ap.add_argument("--build", choices=["detect", "discover", "full"], default="detect",
                    help="full mode: detect = read manifests (default); discover = run the build; full = build and run tests. "
                         "discover/full EXECUTE repo code: use a disposable clone")
    ap.add_argument("--test-timeout", type=int, default=900)
    ap.add_argument("--blame-seconds", type=int, default=300, help="full mode: time budget for the code-age lane (0 = skip)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--review", action="store_true", help="print every value in the output")
    a = ap.parse_args(argv)

    repo = a.repo.expanduser().resolve()
    out = a.out.expanduser().resolve()
    try:
        git(repo, "rev-parse", "--git-dir")
    except (RuntimeError, OSError):
        ap.error("not a git repository")
    if out == repo or repo in out.parents:
        ap.error("--out must be outside the repository")
    out.mkdir(parents=True, exist_ok=True)
    simple = a.mode == "simple"
    st = Stages(out, a.force, a.mode)
    lanes = {}

    gitres = st.run("git", lambda: stage_git(repo, a.cutoff, a.all_refs, with_files=not simple))
    lanes["history"] = "metadata only" if simple else "full census"
    tree = st.run("tree", lambda: stage_tree(repo, a.sample_files if simple else None))
    lanes["tree"] = "sampled" if simple else "every file read"
    tests = dict(tree["_tests"])
    if simple:
        signals = None
        frameworks = []
        build = stage_build(repo, "detect", 0, {}, tests)
        build["reason"] = "simple mode: no build probe"
        lanes["build"] = "not run (simple mode)"
        code_age = None
        lanes["code_age"] = "not run (simple mode)"
    else:
        signals = st.run("signals", lambda: stage_signals(repo))
        signals["_notebooks"] = tree["_sourcing"]["notebooks"]
        frameworks = signals["frameworks"]
        build = st.run(f"build-{a.build}", lambda: stage_build(repo, a.build, a.test_timeout, signals, tests))
        lanes["build"] = a.build
        if a.blame_seconds > 0 and not gitres["history_shallow"]:
            code_age = st.run("blame", lambda: stage_blame(repo, a.cutoff, a.blame_seconds))
            lanes["code_age"] = "complete" if code_age["complete"] else "partial (time budget reached; share covers the largest files)"
        else:
            code_age = None
            lanes["code_age"] = "skipped"
    tests["frameworks"] = frameworks
    logic = dict(tree["_logic"])
    band, reasons = logic_band(logic, tests)
    logic.update(logic_depth=band, reasons=reasons)
    census = None
    if not simple:
        cp = out / "census.json"
        if cp.exists():
            census = json.loads(cp.read_text("utf-8"))
            lanes["review"] = "census recorded"
        else:
            lanes["review"] = "not run (optional; see add_census.py)"
    cl = classify(tree["languages"], signals or {"frameworks": []}, build, gitres)

    doc = {
        "schema": SCHEMA, "kind": "code", "tool": TOOL, "tool_version": VERSION, "mode": a.mode,
        "status": "measured" if simple or census else "measured_without_review",
        "measured_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "label": a.label or repo.name, "tags": a.tag, "cutoff": a.cutoff,
        "span": {"first": gitres["first_commit"], "last": gitres["last_commit"]},
        "lanes": lanes,
        "fingerprints": dict(gitres["_fingerprints"], content_sketch=tree["_content_sketch"]),
        "git": {k: v for k, v in gitres.items() if not k.startswith("_")},
        "tree": {k: v for k, v in tree.items() if not k.startswith("_")},
        "tests": tests, "build": build, "logic": logic, "classification": cl,
        "tasks": task_block(gitres["_task_signals"]),
    }
    if not simple:
        sig = {k: v for k, v in signals.items() if not k.startswith("_")}
        sig.update(tree["_observability"] and {"health_endpoint": tree["_observability"]["health_endpoint"],
                                               "metrics": tree["_observability"]["metrics"]})
        doc["signals"] = sig
        doc["sourcing"] = tree["_sourcing"]
        doc["authenticity"] = dict(tree["_authenticity"], mock_calls=tests.pop("mock_calls"), assertions=tests.pop("assertions"))
        if gitres["_root_commit_files"] is not None and tree["tracked_files"]:
            share = round(min(1.0, gitres["_root_commit_files"] / tree["tracked_files"]), 3)
            doc["authenticity"]["initial_commit_file_share"] = share
            doc["authenticity"]["looks_like_bulk_import"] = share >= 0.8 and gitres["commits"] < 30
        doc["code_age"] = code_age
        doc["census"] = census
    else:
        tests.pop("mock_calls", None)
        tests.pop("assertions", None)
    doc["recommendation"] = recommend(doc)
    doc["privacy"] = {"authors": "counted, never named", "paths": "not emitted", "messages": "not emitted"}

    problems = boundary(doc, author_denylist(repo))
    if problems:
        log("output boundary refused the document, nothing written:")
        for p in problems[:20]:
            log("  " + p)
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
    log(f"full run: {doc['recommendation']['full_run']}")
    if a.review:
        kinds = Counter(type(v).__name__ for _, v in leaves(doc))
        print(f"REVIEW: {sum(kinds.values())} values ({', '.join(f'{k} {n}' for k, n in kinds.items())})")
        for path, v in leaves(doc):
            if isinstance(v, str) and not path.startswith("fingerprints"):
                print(f"{path} = {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
