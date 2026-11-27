# AGENTS.md

This repository is the **measure-code** agent skill. Its instructions are in [SKILL.md](SKILL.md).

- When the user asks for measure-code, or for anything the `description` in SKILL.md covers, read `SKILL.md` and follow its steps. `<skill-dir>` in it means the folder holding SKILL.md (this repository).
- The scripts in `scripts/` need only Python 3.9+ and its standard library.
- After changing anything in this repository, run `bash tests/selftest.sh`.
