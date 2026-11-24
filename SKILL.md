---
name: measure-code
description: Measure one git repository privately on this machine and write counts only (measure.json, summary_row.csv, report.md). Covers commits, authors (counted, never named), date spans and pre-cutoff share, lines by language, tests, classification, engineering signals, code age, a build probe, logic depth, an optional reviewer census and task candidates. Start with --mode simple (minutes) to see whether a full run is worth it. Use when the user asks to "measure this repo", "count commits / lines of code / authors", "size up a codebase for a buyer or data room", or to prepare inputs for master-valuate. Not a code review.
---

# measure-code

Measures one git repository and writes a privacy-preserving summary. The script makes **no network calls** and **uploads nothing**. The output holds numbers, fixed vocabulary and one-way fingerprints, plus the label the user picks. Every string in the output must sit in a declared field. Anything else, and any email address, absolute path or history author name, stops the run before a file is written.

## Two modes

| mode | what runs | time |
|---|---|---|
| `--mode simple` (**recommended first run**) | history metadata (no per-commit file lists), a sampled line count scaled by file size, path-based test count, classification. No build, no blame, no census. Ends with a **full-run recommendation**. | seconds to a few minutes |
| `--mode full` (default) | every lane: full commit census and task candidates, every file read, engineering signals, code age by blame, build probe, optional reviewer census | minutes to an hour on very large repos |

## Steps

1. **Pick the repo and an output folder** outside the repository, one per repo, for example `./measure-out/<label>/`.
2. **Agree on a label.** It is the only name in the output. Use a neutral one such as `repo-01` if the name is sensitive. Add `--tag project:<name>` to help master-valuate group repos.
3. **Simple run first:**
   ```bash
   python3 <skill-dir>/scripts/measure_code.py <repo> --out <out-dir> --label <label> --mode simple
   ```
   Read the "Full run" line in `report.md`. It says recommended, optional or not worth it, with reasons and a time estimate. Show it to the user.
4. **Full run**, if worth it:
   ```bash
   python3 <skill-dir>/scripts/measure_code.py <repo> --out <out-dir> --label <label> [--tag project:<name>] [--all-refs] [--build detect|discover|full] [--blame-seconds 300]
   ```
   - `--build detect` (default) reads manifests only. `discover` runs the project's build, and `full` also runs its tests and records pass/fail counts. Both **execute repo code**: use them only on a disposable clone, with dependencies already installed, and only if the user agrees. Build commands run with offline settings, and this script never downloads anything.
   - `--blame-seconds` bounds the code-age lane, which measures how much of today's code was written after the cutoff. The largest files go first. The lane notes when the budget cut it short.
   - The run resumes from `<out-dir>/.state/`. `--force` recomputes.
5. **Optional reviewer census.** This is the judgement lane, done by you in this session and never by the script. Read the code, then write a draft JSON with `logic_depth` (0-6), `self_contained` (0-4), `categories` (`complex_logic`, `work_units`, `integrations`, `performance_work`, `hardening_work`: one generic sentence per item, so the count is the list), up to 8 `themes`, a one-line `summary`, and `ideas` (`{"kind": "bugfix|feature|restructure|exploratory", "text": ...}`). Sentences describe the *shape* of the work, for example "Add cursor pagination to a list endpoint backed by a relational store". Never name the product, a file, a symbol or a person. Then:
   ```bash
   python3 <skill-dir>/scripts/add_census.py draft.json --out <out-dir> --repo <repo>
   python3 <skill-dir>/scripts/measure_code.py <repo> --out <out-dir> --label <label>
   ```
   Dropped sentences are listed with the reason. Without a census, `status` is `measured_without_review`.
6. **Review with the user.** Show `report.md`. `--review` prints every text value in the output. Delete `<out-dir>/.state/` and `census.json` drafts before sharing.
7. **Hand off.** `measure.json` goes to **master-valuate**, and `summary_row.csv` drops into a spreadsheet.

## Reading the numbers

- `git.pre_cutoff_share` is the share of commits before `--cutoff` (default 2025-01-01). `code_age.share_written_since_cutoff` is the share of today's code lines whose blame date is after it.
- `classification.primary_class` is one of game, frontend, backend, fullstack, mobile, ml, data, infra or library_or_tool. It also carries `is_monorepo`, and `is_likely_demo` with its reasons.
- `logic.logic_depth` is a deterministic 0-6 band. `census.logic_depth` is the reviewer's judgement. Both are kept.
- `tasks.candidates` counts non-merge commits touching 1-20 files that change source **and** tests, by kind: `bugfix`, `feature`, `restructure` and `exploratory`.
- `build.runnability` (0-4) adds one point each for: builds, declares tests, ran a suite, 90%+ pass. It is null with a `reason` when the probe could not run, never a silent zero.
- `git.ai_coauthored_commits` and `git.ai_agent_commits` count commits with AI co-author trailers or agent accounts. Tools that leave neither are invisible.
- `null` means "not measured". `0` means "measured, none found".

## Limits

Line counts use comment rules, not a parser. Functions and nesting are estimates. Authors with several emails count more than once. Commit kinds come from message keywords. Simple mode scales a file sample, and its line counts are marked as estimates.
