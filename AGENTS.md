# Agent notes for the IDC repository

The IDC builds and distributes Galaxy reference data on the
`idc.galaxyproject.org` CVMFS repository. Everything is requested as one file
per data manager run, `data-managers/<data_table>/<version>.yaml`, which is
linted on the PR (`.github/workflows/lint.yml`), built on test.galaxyproject.org
after the merge (`build.yml`) and published to CVMFS by a maintainer
(`deploy.yml`). Genomes and indexes work the same way (`docs/genome-indexing.md`);
`genomes.yml`, `data_managers.yml` and `.ci/jenkins.sh` belong to the earlier
Jenkins pipeline.

Human docs are in `docs/` (published as <https://galaxyproject.github.io/idc/>;
`pip install -r requirements-docs.txt && mkdocs build --strict`). The requester guide is
`docs/requesting-reference-data.md`.

## Skills

Task playbooks for agents are in `.claude/skills/<name>/SKILL.md` (Claude Code
discovers them automatically; other agents should read the matching file before
starting the task):

- `.claude/skills/request-reference-data/SKILL.md`: request a new reference
  database version, from "I need X version Y" to a linted request file and a
  drafted PR.
- `.claude/skills/check-reference-data-request/SKILL.md`: where a request is in
  the pipeline (lint, build, publish, visible in Galaxy), and why it's stuck.

## Rules

- Use the scripts in `scripts/` for validation, generation and existence
  checks; don't re-implement them.
- Requesting and checking need no secrets: the Galaxy data table API
  (`GET /api/tool_data/<table>`) and the Tool Shed are public. Never ask a user
  to paste an API key or SSH key.
- Don't push, open PRs, dispatch workflows or touch CVMFS unless the user asks.
  Publishing (`deploy.yml`) is a maintainer action.
- `schemas/request.schema.json` and `schemas/data_managers.yml` are generated
  (`scripts/generate_schema.py`); don't edit them by hand.

## Checks

```bash
pip install "pydantic>=2" pyyaml jsonschema gxformat2 pytest
python scripts/request_models.py                        # lint every request
python scripts/generate_schema.py --check               # editor schema current?
python scripts/generate_build.py --all --outdir build   # generate + gxformat2-validate
python -m pytest tests/ -q
```
