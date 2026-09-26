# IDC - Simon's Data Club

*In memory of our friend and reference data champion,
[Simon Gladman](https://www.biocommons.org.au/news/simon-gladman). Formerly the
Intergalactic (reference) Data Commission.*

The IDC is for Galaxy reference data what the
[IUC](https://github.com/galaxyproject/tools-iuc) is for Galaxy tools: a project
of the Galaxy team and community to produce, host and distribute reference data
for Galaxy servers. Everything it builds is published to the
**`idc.galaxyproject.org`** [CVMFS](https://cernvm.cern.ch/fs/) repository,
which any Galaxy server can mount read-only and use without downloading or
indexing anything itself.

Contributions happen by pull request to
[galaxyproject/idc](https://github.com/galaxyproject/idc), and reviews are as
welcome as requests.

## What the IDC builds

**Genome indexes.** Genomes listed in `genomes.yml` are fetched and indexed by
the data managers listed in `data_managers.yml` (Bowtie2, BWA-MEM, HISAT2, STAR,
Picard, samtools faidx, 2bit, ...). A maintainer triggers the Jenkins job, which
runs them on a dedicated build Galaxy and imports the results onto CVMFS. See
[Genome indexing](genome-indexing.md).

**Versioned reference databases.** Databases built by a data manager, one
version per request: MetaPhlAn, mOTUs and SameStr so far, and anything else a
data manager installed on the build Galaxy can produce. A request is a single
YAML file under `data-managers/`. CI lints it, merging builds it on
test.galaxyproject.org as a data-manager bundle workflow, and a maintainer
publishes the bundle to CVMFS. See
[Requesting reference data](requesting-reference-data.md) and
[How it works](architecture.md).

## Where to go next

| I want to... | read |
|---|---|
| use IDC data on my Galaxy server | [Using IDC data in Galaxy](using-idc-data.md) |
| see what is requested and which data managers can be used | [Catalog](catalog.md) |
| get a new database or version built | [Requesting reference data](requesting-reference-data.md) |
| get a genome indexed | [Genome indexing](genome-indexing.md) |
| understand the pipeline end to end | [How it works](architecture.md) |
| publish a build to CVMFS (maintainers) | [Publishing to CVMFS](cvmfs-publish-actions.md) |

Coding agents working in the repository have skills for requesting data and
checking a request's status; see
[`AGENTS.md`](https://github.com/galaxyproject/idc/blob/main/AGENTS.md).

## About this site

The site is built with [MkDocs](https://www.mkdocs.org/) and
[Material for MkDocs](https://squidfunk.github.io/mkdocs-material/) from the
`docs/` directory, and the catalog is generated from the repository on every
build. To preview a change locally:

```bash
pip install -r requirements-docs.txt
mkdocs serve          # http://127.0.0.1:8000/idc/
mkdocs build --strict # what CI runs
```
