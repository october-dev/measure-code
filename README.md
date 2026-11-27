# measure-code

A Claude Code skill that measures one git repository **on your own machine** and writes counts only: history, authors (counted, never named), lines by language, tests and test tools, classification, engineering signals, code age, a build probe, logic depth, capacity (mineable commits), and a reviewer census with task mining.

It is one of four October measurement skills:

| skill | measures |
|---|---|
| **measure-code** | git repositories |
| [measure-data](https://github.com/october-dev/measure-data) | SaaS and business data exports |
| [measure-art](https://github.com/october-dev/measure-art) | creative and CAD files |
| [master-valuate](https://github.com/october-dev/master-valuate) | dedupes and cross-links all of the above into a buyer overview and pricing worksheet |

## Install

Requires Python 3.9+ and git. No pip packages.

```bash
git clone https://github.com/october-dev/measure-code ~/.claude/skills/measure-code
```

## Use

**Start with simple mode.** It reads history metadata and a sample of files, takes seconds to minutes, and tells you whether a full run is worth it. Every simple-mode figure is an estimate (`status: estimate`):

```bash
python3 ~/.claude/skills/measure-code/scripts/measure_code.py ~/code/my-game \
    --out ./measure-out/game-client --label game-client --mode simple
```

Then the full run (the default mode). A full run is `measured` only when every lane ran, including the reviewer census and task mining that Claude writes in the session (see [SKILL.md](SKILL.md)). Otherwise it is `partial`, and `status_reasons` says why, from a fixed list:

```bash
python3 ~/.claude/skills/measure-code/scripts/measure_code.py ~/code/my-game \
    --out ./measure-out/game-client --label game-client --tag project:my-game
```

Reasons: `simple_mode`, `not_a_git_repository`, `no_commits`, `shallow_clone`, `census_not_run`, `task_mining_skipped`, `build_not_executed`, `build_environment_missing`, `build_timed_out`, `code_age_partial`, `code_age_not_run`.

Or in Claude Code:

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

Output:

```
measure-out/game-client/
├── measure.json       # schema october.measure/v1, kind "code"
├── summary_row.json   # one flat row
├── summary_row.csv
├── report.md
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

## Privacy

- The script makes no network calls, unless you pass `--install`, and uploads nothing.
- **The reviewer census and task mining are written by the AI model of the Claude Code session running the skill.** It reads the repository's source and history, so the source is shown to that model's provider, under your own account. The skill tells the agent to skip credential files (`.env`, keys, certificates, `*credentials*`, `*secret*`, `.npmrc` and similar), never to repeat a secret, and to treat repo content as data, not instructions. Skip the census if that exposure is not acceptable: the status then stays `partial`.
- Never emitted: author names or emails, paths, file names, commit messages, branch names, source code.
- Output boundary: every text value must sit in a declared field, keys must be fixed vocabulary, and emails, absolute paths and history author names are refused. If anything fails, nothing is written.
- Fingerprints are truncated SHA-256 hashes of commit and blob ids. They let master-valuate detect duplicate repos without revealing content.

## Test

```bash
bash tests/selftest.sh
```

It covers full and simple mode, the census and task-mining rules, capacity and strict fix detection, PR and AI-co-author matching, branch selection, multi-root builds with failure attribution, empty repos, folders without git, shallow clones, partial code age, `--verify` and the output boundary, all on throwaway synthetic repos. It passes on Python 3.9 and later.

## License

MIT
