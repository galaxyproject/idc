# Genomes

Genomes and their indexes are requested like any other reference data, with one
file per data manager run under `data-managers/`, built and published as
described in [Requesting reference data](requesting-reference-data.md). This
page covers what is specific to them.

## The genome itself

A genome is fetched by the `data_manager_fetch_genome_dbkeys_all_fasta` data
manager, which writes the `all_fasta` and `__dbkeys__` tables. The request goes
in `data-managers/all_fasta/`, named after the dbkey:

```yaml
# data-managers/all_fasta/GCF_000001405.40.yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/galaxyproject/idc/main/schemas/request.schema.json
tool_id: toolshed.g2.bx.psu.edu/repos/devteam/data_manager_fetch_genome_dbkeys_all_fasta/data_manager_fetch_genome_all_fasta_dbkey/0.0.3
data_tables:
  - all_fasta
  - __dbkeys__
params:
  dbkey_source:
    dbkey_source_selector: new
    dbkey: GCF_000001405.40
    dbkey_name: "Homo sapiens (GRCh38.p14)"
  reference_source:
    reference_source_selector: ncbi      # or ucsc, url
    requested_identifier: GCF_000001405.40
  sequence_id: GCF_000001405.40
  sequence_name: "Homo sapiens (GRCh38.p14)"
description: Human reference genome GRCh38.p14 (RefSeq)
```

`python scripts/tool_schemas.py <tool_id>` lists the other sources and options,
such as fetching from UCSC by its dbkey or from a URL. Many genomes are already
served, so check
`https://test.galaxyproject.org/api/tool_data/all_fasta` for your dbkey first.

A genome that is already served from outside the IDC still gets a request: the
recipe that reproduces it, with the checksum of the FASTA the data manager
writes. The build then uses the served copy only if it has that checksum.
[`data-managers/all_fasta/hg38canon.yaml`](https://github.com/galaxyproject/idc/blob/main/data-managers/all_fasta/hg38canon.yaml)
reproduces the `hg38canon` served from `data.galaxyproject.org`: UCSC's hg38,
cut down to the canonical chromosomes in karyotypic order by the custom sort.

## Indexes

An index is built from a genome in `all_fasta`, so its request names the genome
with `depends_on` and goes in the index's table, again named after the dbkey:

```yaml
# data-managers/bowtie2_indexes/GCF_000001405.40.yaml
tool_id: toolshed.g2.bx.psu.edu/repos/devteam/data_manager_bowtie2_index_builder/bowtie2_index_builder_data_manager/2.3.4.3
data_tables:
  - bowtie2_indexes
depends_on:
  all_fasta: GCF_000001405.40
params: {}
```

If the genome is already served (and matches its `sha256`, if pinned), the
build indexes that copy; otherwise it's fetched first, in the same workflow.

Each indexer needs a `CHAIN_WIRING` entry in
[`scripts/generate_build.py`](https://github.com/galaxyproject/idc/blob/main/scripts/generate_build.py)
the first time it's requested, naming the input that takes the `all_fasta`
entry. Most indexers have no conditional to set, so the entry needs no
`tool_state`. For STAR it is

```python
("rnastar_index2x_versioned", "all_fasta"): {
    "connect_param": "all_fasta_source",
},
```

## genomes.yml and data_managers.yml

The genomes listed in `genomes.yml` were built by the IDC's earlier pipeline,
which combined that file with the indexers in `data_managers.yml` and ran them
from Jenkins (`.ci/jenkins.sh`). The files are still in the repository and the
[catalog](catalog.md#genomes-in-genomesyml) lists their genomes, but new genomes
and indexes are requested as above.
