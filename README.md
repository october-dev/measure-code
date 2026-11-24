# measure-code

A Claude Code skill that measures one git repository **on your own machine** and writes counts only: history, authors (counted, never named), lines by language, tests, classification, engineering signals, code age, a build probe, logic depth, task candidates and an optional reviewer census.

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

**Start with simple mode.** It reads history metadata and a sample of files, takes seconds to minutes, and tells you whether a full run is worth it:

```bash
python3 ~/.claude/skills/measure-code/scripts/measure_code.py ~/code/my-game \
    --out ./measure-out/game-client --label game-client --mode simple
```

Then the full run (the default mode):

```bash
python3 ~/.claude/skills/measure-code/scripts/measure_code.py ~/code/my-game \
    --out ./measure-out/game-client --label game-client --tag project:my-game
```

Or in Claude Code:

> Use measure-code on ~/code/my-game: simple mode first, then a full run if it's worth it

| flag | default | meaning |
|---|---|---|
| `--mode simple\|full` | full | simple = fast, sampled, no build/blame/census; full = every lane |
| `--out DIR` | required | output folder, outside the repo |
| `--label NAME` | folder name | the only name written to the output |
| `--tag K:V` | none | cross-linking tag for master-valuate (repeatable) |
| `--cutoff DATE` | 2025-01-01 | date for pre-cutoff shares |
| `--all-refs` | off | every branch and tag, not just HEAD |
| `--build detect\|discover\|full` | detect | discover runs the build, and full also runs tests. Both **execute repo code** |
| `--blame-seconds N` | 300 | time budget for the code-age lane (0 skips it) |
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
| history: commits, authors (hashed), dates, months, PRs merged, AI co-authored commits, shallow-clone check | yes | yes |
| task candidates: commits changing source and tests, by kind | - | yes |
| lines by language, tests, logic-depth band | sampled estimate | every file |
| classification: class, monorepo, likely demo | yes | yes |
| engineering signals: CI steps, lint, coverage, dependencies, docs, environment, observability, IaC | - | yes |
| sourcing: first-party / third-party / generated lines, seed data, notebooks | - | yes |
| code age: share of today's lines written after the cutoff (git blame, time-boxed) | - | yes |
| build probe | - | detect (default), discover, full |
| reviewer census: logic depth, categories, themes, task ideas (`add_census.py`) | - | optional |
| full-run recommendation | yes | yes |

## Privacy

- No network calls. Nothing is uploaded.
- Never emitted: author names or emails, paths, file names, commit messages, branch names, source code.
- Output boundary: every text value must sit in a declared field, keys must be fixed vocabulary, and emails, absolute paths and history author names are refused. If anything fails, nothing is written.
- Fingerprints are truncated SHA-256 hashes of commit and blob ids. They let master-valuate detect duplicate repos without revealing content.

## Test

```bash
bash tests/selftest.sh
```

It covers full mode, simple mode, the census, the build probe and the output boundary on a throwaway synthetic repo.

## License

MIT
