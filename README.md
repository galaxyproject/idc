# IDC - Intergalactic (referecne) Data Commission

What the [IUC](https://github.com/galaxyproject/tools-iuc) (Intergalactic Utilities Commission) is for Galaxy tools is the IDC for (reference) Data.

This repository is the entry point to contribute to the community maintained CVMFS data repository hosting approximately 6TB of public and open reference datasets.

Currently, we forsee two distinct modes of this repositoy:

  * single data-managers that do not depend on an other data manager, e.g. metaphlan2
  * data-managers that depend on other data managers and needs to be executed in a workflow like fashion, e.g. hisat2

The single data-managers are easy to run and all you need is a specification on how to run your data manager in a YAML file.

