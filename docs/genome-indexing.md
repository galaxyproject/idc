# Genome indexing

The IDC's original pipeline: fetch genomes and build the standard indexes for
them (Bowtie2, BWA-MEM, HISAT2, STAR, Picard, samtools faidx, 2bit, ...) with
Galaxy data managers, and publish the results on `idc.galaxyproject.org`. It is
driven by two files at the root of the repository.

For databases that aren't per-genome indexes (MetaPhlAn, mOTUs, ...), see
[Requesting reference data](requesting-reference-data.md) instead.

## `genomes.yml`

The genomes to fetch and index:

```yaml
genomes:
  - dbkey: dm6          # the genome's dbkey in Galaxy
    description:        # set from UCSC for UCSC genomes
    id: dm6             # unique id of the data in Galaxy
    source: ucsc        # 'ucsc', an NCBI accession, or a URL to a FASTA file
    doi:
    version:
    checksum:
    blob:
    indexers:           # data managers (from data_managers.yml) to run on it
      - data_manager_bowtie2_index_builder
      - data_manager_bwa_mem_index_builder
    skiplist:           # data managers NOT to run on it
      - bfast
```

Only `dbkey`, `description`, `id`, `source` and `indexers` are used today; the
other fields are there for provenance the IDC would like to record. The
[catalog](catalog.md#genomes) lists the genomes currently in the file.

## `data_managers.yml`

The data managers used to build the genome data, by the name `indexers` refer
to:

```yaml
data_manager_bwa_mem_index_builder:
  tool_id: 'toolshed.g2.bx.psu.edu/repos/devteam/data_manager_bwa_mem_index_builder/bwa_mem_index_builder_data_manager/0.0.3'
  tags:
    - genome          # "genome" (an indexer) or "fetch_source"
  parameters:         # optional, passed to the data manager
    index_algorithm: bwtsw
```

The `fetch_source` data manager (`data_manager_fetch_genome_dbkeys_all_fasta`)
comes first: it downloads each genome and fills the `all_fasta` and
`__dbkeys__` tables that the indexers then read from.

## How a genome gets built

1. A PR changes `genomes.yml` (or `data_managers.yml`), is reviewed, and a
   maintainer comments `@galaxybot deploy` on it. That comment is what starts
   the Jenkins job (`.ci/jenkins.sh`); without it the job exits.
2. Ephemeris (`_idc-split-data-manager-genomes`) splits the two files into one
   task per genome and data manager, leaving out what already exists.
3. Jenkins launches a short-lived build Galaxy with the playbooks in
   [`ansible/`](https://github.com/galaxyproject/idc/tree/main/ansible), with
   the IDC repository mounted, and waits until its CVMFS client is at the
   current revision, so it sees everything already published.
4. The data managers run in two stages, all in *bundle* mode (Ephemeris
   `run-data-managers --data-manager-mode bundle`): first the genome fetch, then
   the indexers. Each task's output lands in a history
   `idc-<genome>-<data manager>`.
5. On the Stratum 0, the job opens a CVMFS transaction, syncs
   `config/tool_data_table_conf.xml`, imports every new bundle with
   `galaxy-import-data-bundle` (which moves the data under `data/` and appends
   the `.loc` rows), writes a `record/<genome>/<data manager>` marker so it is
   never imported twice, and publishes.
6. The build Galaxy is torn down. The Stratum 1s and Galaxy servers pick the new
   revision up as for any other publish; see
   [Using IDC data in Galaxy](using-idc-data.md#4-pick-up-new-data).

The same Jenkins job also has a reference-data-only mode
(`@galaxybot deploy reference-data`), kept as a fallback for publishing
[versioned reference data](requesting-reference-data.md); the normal path for
that is the GitHub Actions workflow described in
[Publishing to CVMFS](cvmfs-publish-actions.md).

## Building locally

`run_builder.sh` runs the same idea on one machine with Docker: it starts a
Galaxy container, installs the data managers, and fetches and indexes the
genomes from `genomes.yml`. Edit the variables at the top of the script first.
Some genomes need a lot of memory to index (more than 64 GB).
