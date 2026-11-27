# Code measurement: starfall-client

Measured 2026-11-20 with measure-code 0.4.0, full mode. Counts only: no names, paths or messages.

**Status: measured** (every lane ran, including the reviewer census and task mining); info: build not_requested

## 1. Headline

- 9,400 commits by 14 people (+2 bots), 2019-03-11 to 2024-08-30 (60 active months, 1,140 active days)
- 165,900 lines of code (comments and blank lines excluded; 189,126 non-blank lines with comments) in 1,036 source files; main language C#; class game
- Tests: 140 files, 1,260 test cases declared, test/source line ratio 0.12; test tools nunit, unity-test-framework
- 100.0% of commits are dated before 2025-01-01; 0.0% of today's code lines were written after it
- Logic depth **4/6** (from the reviewer census; deterministic band 3/6)
- Capacity: 2,476 mineable commits; 784 commits change source and tests together; 4 mined task ideas

## 2. History

| metric | value |
|---|---|
| commits | 9,400 |
| merge commits | 1,128 |
| authors human | 14 |
| authors bot | 2 |
| bot commits | 282 |
| authors with 10plus commits | 10 |
| top1 author share | 31.0% |
| top5 author share | 78.0% |
| span days | 1,999 |
| recency days | 816 |
| active days | 1,140 |
| active months | 60 |
| commits last 12 months | 0 |
| pre cutoff commits | 9,400 |
| pre cutoff share | 100.0% |
| tags | 37 |
| semver tags | 31 |
| branches | 12 |
| pr merged count | 1,034 |
| conventional commit share last 200 | 42.0% |
| ai coauthored commits | 0 |
| ai agent commits | 0 |
| history shallow | no |
| history complete | yes |
| history measured on | head (12 branches compared) |

| year | commits |
|---|---|
| 2019 | 1,210 |
| 2020 | 2,440 |
| 2021 | 2,610 |
| 2022 | 1,730 |
| 2023 | 980 |
| 2024 | 430 |

## 3. Lines by language

| language | files | code | comments | blank |
|---|---|---|---|---|
| C# | 1,020 | 152,300 | 21,900 | 30,100 |
| Shader | 88 | 11,200 | 900 | 1,800 |
| Python | 21 | 2,400 | 300 | 500 |

Median source file 118 lines, 90th percentile 486. Vendored or generated files skipped: 412.
Lines of code: first-party 165,900, third-party 66,360, generated 4,977. Seed-data files 2; notebooks 0.

Non-code files: image 1,840, engine_asset 5,230, audio 310, 3d 96

## 4. Classification

- primary class: **game** (game 71%, backend 29%)
- monorepo: no; likely demo: no

## 5. Engineering signals

| area | signal |
|---|---|
| frameworks | none detected |
| CI | github-actions; runs tests yes, lint yes, typecheck no, deploys yes, dependency audit no |
| lint and format | editorconfig |
| coverage | codecov; threshold set no |
| dependencies | 14 runtime, 0 dev; lockfiles none; update bot dependabot |
| docs | readme yes (140 lines, 9 sections); changelog yes; contributing no; licence no |
| environment | dockerfiles 0; devcontainer no; env example yes; env files committed 0 |
| observability | logging none; error tracking sentry; health endpoint no; metrics no |
| infrastructure as code | terraform files 0; kubernetes manifests 0; helm charts 0 |

Authenticity signals: median comment ratio 11.0%; assistant-phrase hits 0; generated-marker files 14; mock calls 310 vs assertions 4,120 in tests.

## 6. Build probe

- level: detect; status **not_requested**
- build systems: none; engine: unity 2021.3.16f1; project roots probed 0 of 0
- build ok: n/a; tests found in files 1,260, listed by the runner n/a, suite ran n/a; passed n/a, failed n/a
- discover index n/a; executed runnability n/a; note: detect level reads manifests only; no build was requested

## 7. Code age

Blame complete: 1,036 of 1,036 source files, 100.0% of their lines. 0.0% of today's lines were written after 2025-01-01.

## 8. Logic depth

Authoritative value **4/6** from the reviewer census.
Deterministic band 3/6 (score 2.82: size 1.00; branching density 0.42; deep nesting 0.21; tests 0.30; modularity 0.89).
Functions (estimate) 10,578; branch points per 1,000 lines 110.4; nesting depth (95th percentile) 6; files nested 5+ deep 58.

Reviewer census: weighted total 30.0 gives logic depth **4/6** (the reviewer's own judgement was 5/6); self-contained 2/4; model example-model.

| category | count | weight |
|---|---|---|
| complex logic | 3 | 3.0 |
| defect repairs | 3 | 0.75 |
| hardening work | 1 | 1.0 |
| integrations | 1 | 1.5 |
| net new capabilities | 2 | 1.0 |
| performance work | 1 | 1.5 |
| repo evolutions | 1 | 0.75 |
| substantial features | 2 | 2.5 |
| work units | 3 | 2.0 |
| defect repairs shipped with a test (bonus) | 2 | 0.5 |

Themes: co-op multiplayer action; procedural content; deterministic simulation

## 9. Capacity and task candidates

Mineable commits (capacity): **2,476**: non-merge, non-bot, not a chore, 2+ first-party non-test source files, 20-8,000 lines changed, at most 40 files in 5 folders. Excluded: 1,118 chores, 239 too spread out. Fix subjects without deleted lines (not counted as fixes): 319. Fixes shipped with a test: 172. Bulk test-add commits: 79.

| kind | mineable | with tests | source only | all commits |
|---|---|---|---|---|
| bugfix | 742 | 288 | 1,904 | 2,086 |
| feature | 841 | 268 | 1,220 | 2,610 |
| restructure | 297 | 131 | 611 | 830 |
| exploratory | 596 | 97 | 540 | 2,130 |

## 10. Task mining

Status: **done**

| kind | ideas |
|---|---|
| net new | 1 |
| bug repair | 1 |
| repo evolution | 1 |
| long horizon | 1 |
| total (cap 150) | 4 |

Generic task ideas:

- bug repair: Stop a networked lobby from accepting a player twice after a dropped reconnect
- net new: Add a replay recorder that captures input frames and plays them back deterministically
- repo evolution: Move save data from a flat binary blob to a versioned schema with migrations
- long horizon: Profile a slow scene load, find the asset that blocks the main thread, and make it stream

## Lanes

- history: full census with lines changed
- tree: every file read
- build: detect
- code_age: complete
- review: census recorded (the session's AI model read the source)
- task_mining: done
- dream: why this is special written by the agent after the audit (not a measurement)

## Provenance

- head commit fingerprint `87f4aae41790b7d5fdde6e029649aa7f`; scripts digest `7b25759288d604ad409b92cbdb81b3c1`; payload digest `b33b14a03a0840510c2628ef210aeb98`
- A buyer can check these numbers on their own copy: `measure_code.py <repo> --verify measure.json`.

## Caveats

- Authors are keyed on email; one person with several emails counts more than once.
- Commit kinds come from message keywords plus the diff shape (a fix must delete lines); they are signals, not verified labels.
- Functions and nesting come from regexes and indentation, not a parser.
- AI-assisted counts see only known agent addresses and product names in trailers and accounts; tools that leave neither are invisible.
- Dates are author dates and can be rewritten by rebases or imports.

## Why this is special

_Founder-style pitch written by the AI agent after the audit, from what it inspected in this run. It is not a measurement and changes no count above._

This is a full co-op action game client, not a prototype: 14 people put 9,400 commits into it between 2019 and 2024. Under the hood it runs a deterministic simulation with procedural levels and networked play, which is the hard kind of game code to get right. The team cared about craft, with 1,260 test cases, 37 release tags and 1,034 merged pull requests behind it. All 165,900 lines were written before 2025, so this is years of human game-making, not a weekend of generated code.
