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

If you want to add a new reference genome to our community storage you probably want to include the FASTA file, the bowtie2 and Star index and so on. For this please just add and entry to our [genome file](https://github.com/bgruening/idc/blob/master/idc-workflows/ngs_genomes.yaml).

For examle adding new reference data for mouse (mm10) would look like that.

```yaml
genomes:
  - id: mm9
    name: M. musculus July 2007 (NCBI37/mm9)
```
