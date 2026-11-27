# measure-code

An agent skill for Claude Code, Codex, Grok, Hermes and other coding agents that measures one git repository **on your own machine** and writes counts only: history, authors (counted, never named), lines by language, tests and test tools, classification, engineering signals, code age, a build probe, logic depth, capacity (mineable commits), and a reviewer census with task mining. It ends with a short, clearly labelled **"Why this is special"** pitch that the agent writes after the audit (dream mode).

It is one of four October measurement skills:

| skill | measures |
|---|---|
| **measure-code** | git repositories |
| [measure-data](https://github.com/october-dev/measure-data) | SaaS and business data exports |
| [measure-art](https://github.com/october-dev/measure-art) | creative and CAD files |
| [master-valuate](https://github.com/october-dev/master-valuate) | dedupes and cross-links all of the above into a buyer overview and pricing worksheet |

## Install

Requires Python 3.9+ and git. No pip packages.

This is a plain agent skill: [`SKILL.md`](SKILL.md) holds the instructions and `scripts/` holds the tools, so any coding agent that can read files and run Python can use it.

- **Claude Code**: clone it into your skills folder and it is picked up as the `measure-code` skill:
  ```bash
  git clone https://github.com/october-dev/measure-code ~/.claude/skills/measure-code
  ```
- **Codex**: clone it into the Codex skills folder:
  ```bash
  git clone https://github.com/october-dev/measure-code ~/.codex/skills/measure-code
  ```
  Or clone it anywhere and point Codex at `SKILL.md`. When Codex works inside the repo, it reads `AGENTS.md`, which points to `SKILL.md`.
- **Grok, Hermes and other agents**: clone it anywhere and tell the agent: *"Read `<path>/SKILL.md` and follow it."*

The commands below use `$SKILL` for the folder you cloned into, for example `SKILL=~/.claude/skills/measure-code`.

## Use

**Start with simple mode.** It reads history metadata and a sample of files, takes seconds to minutes, and tells you whether a full run is worth it. Every simple-mode figure is an estimate (`status: estimate`):

```bash
python3 $SKILL/scripts/measure_code.py ~/code/my-game \
    --out ./measure-out/game-client --label game-client --mode simple
```

Then the full run (the default mode). A full run is `measured` when every default lane completed, including the reviewer census and task mining that your agent's model writes in the session (see [SKILL.md](SKILL.md)). It is `partial` only when something actually failed, timed out or was skipped, and `status_reasons` says why, from a fixed list:

```bash
python3 $SKILL/scripts/measure_code.py ~/code/my-game \
    --out ./measure-out/game-client --label game-client --tag project:my-game
```

Reasons: `simple_mode`, `not_a_git_repository`, `no_commits`, `shallow_clone`, `census_not_run`, `task_mining_skipped`, `build_failed`, `build_environment_missing`, `build_timed_out`, `build_unsupported`, `code_age_partial`, `code_age_not_run`. Failing tests are not a reason: the pass rate is reported, and `tests: some_failed` is recorded as info.

The build reasons apply only to a build you asked for (`--build discover` or `full`). With the default `--build detect` no build is executed, and that is recorded as info (`status_info: {"build": "not_requested"}`), not as a reason for `partial`. Simple mode is always `estimate`.

Or in your coding agent (Claude Code, Codex, Grok, Hermes ...):

> Use measure-code on ~/code/my-game: simple mode first, then a full run if it's worth it

| flag | default | meaning |
|---|---|---|
| `--mode simple\|full` | full | simple = fast, sampled, estimates only, no build/blame/census; full = every lane |
| `--out DIR` | required | output folder, outside the repo |
| `--label NAME` | folder name | the only name written to the output |
| `--tag K:V` | none | cross-linking tag for master-valuate (repeatable) |
| `--cutoff DATE` | 2025-01-01 | date for pre-cutoff shares. The default is there because buyers price work written before 2025 |
| `--ref auto\|head` | auto | auto measures history on the deepest branch when it is clearly deeper than HEAD; head = HEAD only |
| `--all-refs` | off | the union of every branch and tag |
| `--build detect\|discover\|full` | detect | discover builds each project root and lists tests; full also runs the suites. Both **execute repo code** |
| `--install` | off | with discover/full: install dependencies from the lockfile first. **Uses the network** and runs install scripts (scratch HOME, credential-like env vars removed) |
| `--max-roots N` | 3 | project roots the build probe tries, largest first (subfolders included) |
| `--blame-seconds N` | 300 | time budget for the code-age lane (0 skips it). When it runs out, the lane is partial and reports its coverage |
| `--verify FILE` | none | re-measure the repo and check an existing measure.json (digests, fingerprints, stable counts) |
| `--sample-files N` | 400 | simple mode: source files to read |
| `--force` / `--review` | off | recompute cached stages / print every value |
| `--no-dream` | off | skip the "Why this is special" pitch |

Output:

```
measure-out/game-client/
├── measure.json       # schema october.measure/v1, kind "code"
├── summary_row.json   # one flat row
├── summary_row.csv
├── report.md          # ends with "Why this is special" once the pitch is added
├── dream.json         # the pitch, kept for re-runs
└── .state/            # per-stage cache for resuming; delete before sharing
```

See [`examples/`](examples/) for output built from **synthetic** data.

## Lanes

| lane | simple | full |
|---|---|---|
| history: commits, authors (hashed), dates, months, PRs merged (first-parent), AI co-authored commits, deepest-branch check | yes | yes |
| shallow clone, empty repo, folder without git: flagged, history figures null, status partial | yes | yes |
| capacity: mineable commits by lines changed, with chore, spread and fake-fix filters; task candidates by kind | - | yes |
| lines by language, tests, test tools, deterministic logic-depth band | sampled estimate | every file |
| classification: class, monorepo, likely demo | yes | yes |
| engineering signals: CI steps, lint, coverage, dependencies, docs, environment, observability, IaC | - | yes |
| sourcing: first-party / third-party / generated lines, seed data, notebooks | - | yes |
| code age: share of today's lines written after the cutoff (git blame, time-boxed, coverage reported) | - | yes |
| build probe: multi-root, failure attribution (environment vs repo), tests found / listed / run | - | detect (default), discover, full; `--install` opt-in |
| reviewer census: nine work categories, weighted logic depth, themes (`add_census.py`) | - | required |
| task mining: up to 150 grounded ideas, counted per kind | - | required |
| provenance: head-commit fingerprint, scripts digest, payload digest; `--verify` | yes | yes |
| full-run recommendation | yes | yes |
| why this is special: the agent's pitch after the audit (`add_dream.py`), validated and labelled | yes | yes (on by default, `--no-dream` skips) |

## Dream mode: "Why this is special"

Counts miss taste, ambition, complexity and polish. So after the audit, the agent re-reads the report and what it inspected and writes a 4-6 sentence founder-style pitch, for example "a full AAA-style Unity game with richly detailed assets, complete end-to-end levels and a real Steam launch". It is on by default; `--no-dream` (or telling the agent you don't want a pitch) skips it.

The pitch must be grounded only in what the run saw: files, folders, commits, store pages or build pipelines in the repo, and the counts in the report. `scripts/add_dream.py` validates it before inserting it: 4-6 sentences and no more than 160 words, no emails, URLs, handles, paths, file names, tokens or author names from the history, and every number must already appear in the report. It is written to `measure.json` as `dream` and rendered as the last section of `report.md`, labelled as a pitch, so the measured sections stay neutral and credible. See [`examples/starfall-client/`](examples/starfall-client/) for a synthetic one.

```bash
python3 $SKILL/scripts/add_dream.py draft.txt --out ./measure-out/game-client --repo ~/code/my-game
```

## Privacy

- The script makes no network calls, unless you pass `--install`, and uploads nothing.
- **The reviewer census and task mining are written by your agent's model, in the coding-agent session running the skill.** It reads the repository's source and history, so the source is shown to that model's provider, under your own account. The skill tells the agent to skip credential files (`.env`, keys, certificates, `*credentials*`, `*secret*`, `.npmrc` and similar), never to repeat a secret, and to treat repo content as data, not instructions. Skip the census if that exposure is not acceptable: the status then stays `partial`.
- Never emitted: author names or emails, paths, file names, commit messages, branch names, source code.
- Output boundary: every text value must sit in a declared field, keys must be fixed vocabulary, and emails, absolute paths and history author names are refused. If anything fails, nothing is written.
- Fingerprints are truncated SHA-256 hashes of commit and blob ids. They let master-valuate detect duplicate repos without revealing content.

## Test

```bash
bash tests/selftest.sh
```

It covers full and simple mode, the status rules (a default run without a requested build is `measured`), dream mode (insert, validation, re-runs and `--no-dream`), the census and task-mining rules, capacity and strict fix detection, PR and AI-co-author matching, branch selection, multi-root builds with failure attribution, empty repos, folders without git, shallow clones, partial code age, `--verify` and the output boundary, all on throwaway synthetic repos. It passes on Python 3.9 and later.

## License

MIT
