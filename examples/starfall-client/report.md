# Code measurement: starfall-client

Measured 2026-11-20 with measure-code 0.2.0, full mode. Counts only: no names, paths or messages.

## 1. Headline

- 9,400 commits by 14 people (+2 bots), 2019-03-11 to 2024-08-30 (60 active months, 1,140 active days)
- 165,900 lines of code in 1,036 source files; main language C#; class game
- Tests: 140 files, 1,260 test cases declared, test/source line ratio 0.12
- 100.0% of commits are dated before 2025-01-01; 0.0% of today's code lines were written after it
- Logic depth band 6/6; 908 commits change source and tests together

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

- level: detect; build systems: none; engine: unity 2021.3.16f1
- build ok: n/a; tests passed n/a, failed n/a; runnability n/a/4; note: detect level runs nothing

## 7. Logic depth

Deterministic band **6/6** (2k+ source lines; 20k+ source lines; dense branching; several deeply nested files; 5+ top-level modules; tests cover 10%+ of source lines).
Functions (estimate) 10,578; branch points per 1,000 lines 110.4; nesting depth (95th percentile) 6; files nested 5+ deep 58.

Reviewer census: logic depth **5/6**, self-contained 2/4.

| category | count |
|---|---|
| complex logic | 3 |
| work units | 3 |
| integrations | 1 |
| performance work | 1 |
| hardening work | 1 |

Themes: co-op multiplayer action; procedural content; deterministic simulation

## 8. Task candidates

Commits that change source and tests together (1-20 files), by kind.

| kind | with tests | source only | all commits |
|---|---|---|---|
| bugfix | 412 | 1,904 | 2,980 |
| feature | 268 | 1,220 | 2,610 |
| restructure | 131 | 611 | 830 |
| exploratory | 97 | 540 | 2,130 |

Generic task ideas:

- bugfix: Stop a networked lobby from accepting a player twice after a dropped reconnect
- feature: Add a replay recorder that captures input frames and plays them back deterministically
- restructure: Move save data from a flat binary blob to a versioned schema with migrations
- exploratory: Profile a slow scene load, find the asset that blocks the main thread, and make it stream

## Lanes

- history: full census
- tree: every file read
- build: detect
- code_age: complete
- review: census recorded

## Caveats

- Authors are keyed on email; one person with several emails counts more than once.
- Commit kinds come from message keywords; they are signals, not verified labels.
- Functions and nesting come from regexes and indentation, not a parser.
- AI-assisted counts see only trailers and agent accounts; tools that leave neither are invisible.
- Dates are author dates and can be rewritten by rebases or imports.
