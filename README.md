# IDC - Simon's Data Club

In memory of our friend and reference data champion, [Simon Gladman](https://www.biocommons.org.au/news/simon-gladman).

Formerly the Intergalactic (reference) Data Commission

The IDC is for Galaxy reference data what the [IUC](https://github.com/galaxyproject/tools-iuc) for Galaxy tools: A project by the Galaxy Team and Community to produce, host, and distribute reference data for use in Galaxy servers. Community contributions and Pull Request reviews are encouraged! Details on how to contribute can be found below.

### Summary

This repository is the entry point to contribute to the community maintained CVMFS data repository hosting approximately 6TB of public and open reference datasets.

Ultimately, it is envisioned that the set of files contained here would be modified with the addition of either a new genomic data set specification or a new data manager. Subsequent Pull Request acceptance would then fetch the genomic data, build the appropriate indices and upload everything to the proper position within the Galaxy project's CVMFS repositories.

Comments/discussion on the approach and contributions are very welcome!

Currently, the repository is geared to produce genomic indices for various tools using their data managers. The included `run_builder.sh` script will:

1. Create a virtualenv with the required software
2. Create a docker Galaxy instance
3. Install the data manager tools listed in `data_managers_tools.yml`
4. Dynamically create an Ephemeris .yml config file from a list of genomes and their sources
5. Fetch the genomes from the appropriate sources and install them into Galaxy's `all_fasta` data table
6. Restart Galaxy to reload the `all_fasta` data table
7. Create the tool indices using Ephemeris and the `data_managers_genomes.yml` file

The resulting genome files and tool indices will be located in the directory specified in the `run_builder.sh` script in the environment variables set at the top.

The two important files are:

* `data_managers.yml`
* `genomes.yml`

### data_managers.yml

This file contains the list of data managers that are to be installed into the target 
Galaxy building IDC data.

```yaml
NAME_OF_THE_DATA_MANAGER:
  tool_id: TOOL_ID_IN_TARGET_REPO_OF_DATA_MANAGER
  tags:
    - tag #Tag can be either "genome" or "fetch_source".
```

Other data managers are added as elements in the `tools` yml array. The first tool listed should always be the `fetch_source` data manager. In most cases this will be the `data_manager_fetch_genome_dbkeys_all_fasta` data manager that sources and downloads most genomes and populates the `all_fasta` and `__dbkeys__` data tables for later use by other data managers.

Ephemeris can be used to generate a shed-tool install file to bootstrap the required tools
and repositories into a target Galaxy for IDC installs.

```bash
pip install ephemeris
_idc-data-managers-to-tools
# defaults to:
# _idc-data-managers-to-tools --data-managers-conf=genomes.yml --shed-install-output-conf=tools.yml
shed-tools install -t tools.yml
```

### genomes.yml

This is the file that contains the list of the genomes to be fetched and indexed.

There is a lot more information in this file that Galaxy can currently use but its format has been specified with the future in mind.

At this stage this file only needs to contain the `dbkey`, `description`, `id` and `source` fields. The rest are there as discussion points currently on the kind of information we would like to have stored with Galaxy to ensure provenance of the reference data used in analyses.

Format:

```yaml
genomes:
    - dbkey: #The dbkey of the data
      description: #The description of the data, including its taxonomy, version and date
      id: #The unique id of the data in Galaxy
      source: #The source of the data. Can be: 'ucsc', an NCBI accession number or a URL to a fasta file.
      doi: #Any DOI associated with the data
      version: #Any version information associated with the data
      checksum: #A SHA256 checksum of the original
      blob: #A blob for any other pertinent information
      indexers: #A list of tags for the types of data managers to be run on this data
      skiplist: # A list of data managers with the above specified tag NOT to be run on this data

```

Example:

```yaml
genomes:
  - dbkey: dm6
    description: D. melanogaster Aug. 2014 (BDGP Release 6 + ISO1 MT/dm6) (dm6)
    id: dm6
    source: ucsc
    doi:
    version:
    checksum:
    blob:
    indexers:
      - genome
    skiplist:
      - bfast
  - dbkey: Ecoli-O157-H7-Sakai
    description: "Escherichia coli O157-H7 Sakai"
    id: Ecoli-O157-H7-Sakai
    source: https://swift.rc.nectar.org.au:8888/v1/AUTH_377/public/COMP90014/Assignment1/Ecoli-O157_H7-Sakai-chr.fna
    doi:
    version:
    checksum:
    blob:
    indexers:
      - genome
    skiplist:
      - bfast
  - dbkey: Salm-enterica-Newport
    description: "Salmonella enterica subsp. enterica serovar Newport str. USMARC-S3124.1"
    id: Salm-enterica-Newport
    source: NC_021902
    doi:
    version:
    checksum:
    blob:
    indexers:
      - genome
    skiplist:
      - bfast
```

## Testing

This repo can be tested using a machine with Docker installed and by a user with Docker privledges. As a warning however, some of the genomes will take a LOT (>64GB) of RAM to index.

It should work just by cloning the repo to the machine, modifying the environment variables in the `run_builder.sh` script to suit and then running it.

## Other data types

Work has been done on some of the other data types, tools and data managers such as those that work on multiple genomes at once like Busco, Metaphlan etc. These can be found in the `older_attempts` directory along with appropriate README.
## How to use the reference data

If you want to use the reference data, please have a look at our [ansible-role](https://github.com/galaxyproject/ansible-cvmfs
) and the [example playbook](https://github.com/usegalaxy-eu/cvmfs-example).

