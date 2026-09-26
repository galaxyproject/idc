# IDC - Simon's Data Club

*In memory of our friend and reference data champion,
[Simon Gladman](https://www.biocommons.org.au/news/simon-gladman). Formerly the
Intergalactic (reference) Data Commission.*

The IDC is to Galaxy reference data what the
[IUC](https://github.com/galaxyproject/tools-iuc) is to Galaxy tools: a
community project that builds reference data for Galaxy servers and distributes
it through the **`idc.galaxyproject.org`** [CVMFS](https://cernvm.cern.ch/fs/)
repository. A server that mounts the repository can use the data directly,
without downloading or indexing anything itself.

The data is built by Galaxy data managers. To get something built, you open a
pull request against [galaxyproject/idc](https://github.com/galaxyproject/idc)
that adds a YAML file naming the data manager and its parameters. The request is
linted on the PR and built after the merge, and a maintainer publishes the
result to CVMFS. Reviews of other people's requests
are as welcome as new ones.

| to | read |
|---|---|
| use IDC data on a Galaxy server | [Using IDC data in Galaxy](using-idc-data.md) |
| see what has been requested and which data managers can be used | [Catalog](catalog.md) |
| get a database, genome or index built | [Requesting reference data](requesting-reference-data.md), [Genomes](genome-indexing.md) |
| understand the pipeline | [How it works](architecture.md) |
| publish a build (maintainers) | [Publishing to CVMFS](cvmfs-publish-actions.md) |

Coding agents working in the repository have skills for requesting data and
following a request; see
[`AGENTS.md`](https://github.com/galaxyproject/idc/blob/main/AGENTS.md).

## About this site

The site is built with [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/)
from `docs/`, and the catalog is generated from the repository on every build.
To preview a change:

```bash
pip install -r requirements-docs.txt
mkdocs serve          # http://127.0.0.1:8000/idc/
mkdocs build --strict # what CI runs
```
