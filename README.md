# IDC - Intergalactic (referecne) Data Commission

What the [IUC](https://github.com/galaxyproject/tools-iuc) (Intergalactic Utilities Commission) is for Galaxy tools is the IDC for (reference) Data.

This repository is the entry point to contribute to the community maintained CVMFS data repository hosting approximately 6TB of public and open reference datasets.

Currently, we forsee two distinct modes of this repositoy:

  * single data-managers that do not depend on an other data manager, e.g. metaphlan2
  * data-managers that depend on other data managers and needs to be executed in a workflow like fashion, e.g. hisat2

The **single data-managers** are easy to run and all you need is a specification on how to run your data manager in a YAML file.
An example is the [humann2 data manager.](https://github.com/bgruening/idc/blob/master/data-managers/humann2_download/chocophlan_full.yaml)

```yaml
---
data_managers:
    - id: toolshed.g2.bx.psu.edu/repos/iuc/data_manager_humann2_database_downloader/data_manager_humann2_download/0.9.9
      # HUMAnN2 database: Nucleotide database: full chocophlan
      # these params correspond to the Galaxy data manager parameters
      params:
          - 'db|database': 'chocophlan'
          - 'db|build': '{{ item }}'
      items:
          - full
      data_table_reload:
          - humann2_nucleotide_database
```

If you want to add a new reference genome to our community storage you probably want to include the FASTA file, the bowtie2 and Star index and so on. For this please just exchange the [item](https://github.com/bgruening/idc/blob/master/idc-workflows/ngs.yaml#L14) in our configuration file with the UCSC ID of your reference genome.

For examle adding new reference data for mouse (mm10) would look like that.

```yaml
---
# configuration for fetch and index genomes

data_managers:
    # Data manager ID
    - id: toolshed.g2.bx.psu.edu/repos/devteam/data_manager_fetch_genome_dbkeys_all_fasta/data_manager_fetch_genome_all_fasta_dbkey/0.0.2
      # tool parameters, nested parameters should be specified using a pipe (|)
      params:
          - 'dbkey_source|dbkey': '{{ item }}'
          - 'reference_source|reference_source_selector': 'ucsc'
          - 'reference_source|requested_dbkey': '{{ item }}'
      # Items refere to a list of variables you want to run this data manager. You can use them inside the param field with {{ item }}
      # In case of genome for example you can run this DM with multiple genomes, or you could give multiple URLs.
      items:
          - dm3
      # Name of the data-tables you want to reload after your DM are finished. This can be important for subsequent data managers
      data_table_reload:
          - all_fasta
          - __dbkeys__

    - id: toolshed.g2.bx.psu.edu/repos/devteam/data_manager_bowtie2_index_builder/bowtie2_index_builder_data_manager/2.2.6
      params:
          - 'all_fasta_source': '{{ item }}'
      items:
          - dm3
      data_table_reload:
          # Bowtie creates indices for Bowtie and TopHat
          - bowtie2_indexes
          - tophat2_indexes

```
