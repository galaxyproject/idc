# Using IDC data in Galaxy

This page is for Galaxy administrators who want their server's tools to use the
IDC's reference data. The data is served read-only over
[CVMFS](https://cernvm.cern.ch/fs/) from the `idc.galaxyproject.org`
repository, so there is nothing to download or index on your side.

## Mount the repository

Install the CVMFS client and configure the `galaxyproject.org` domain, which
provides the Stratum 1 servers and public keys. The
[galaxyproject.cvmfs](https://github.com/galaxyproject/ansible-cvmfs) Ansible
role does both, [cvmfs-example](https://github.com/usegalaxy-eu/cvmfs-example)
is a minimal playbook that uses it, and the Galaxy Training Network's
[CVMFS tutorial](https://training.galaxyproject.org/training-material/topics/admin/tutorials/cvmfs/tutorial.html)
walks through the whole setup.

The mounted repository looks like this:

```
/cvmfs/idc.galaxyproject.org/
├── config/     tool_data_table_conf.xml and the .loc files it points at
├── data/       the reference data
└── record/     markers of what has been imported, for the IDC's own tooling
```

CVMFS mounts a repository when it's first accessed, so run
`ls /cvmfs/idc.galaxyproject.org/config/` rather than listing `/cvmfs`.

## Load the data tables

Add the IDC's data table configuration to `tool_data_table_config_path` in
`galaxy.yml`. The option takes a comma-separated list, so keep the files you
already load:

```yaml
galaxy:
  tool_data_table_config_path: /cvmfs/idc.galaxyproject.org/config/tool_data_table_conf.xml,/srv/galaxy/config/tool_data_table_conf.xml
```

Each table in the file reads its `.loc` file from
`/cvmfs/idc.galaxyproject.org/config/`, and the paths in the `.loc` files point
into the repository, so there's nothing else to configure. The
[catalog](catalog.md#data-tables) lists the tables. Restart Galaxy afterwards.

## Make the data visible to jobs

Tools read the data at its `/cvmfs/...` path, so jobs need to see the repository
at that path too. Jobs running on hosts that mount CVMFS already do. For jobs in
Singularity/Apptainer or Docker containers, bind the repository in read-only,
through `singularity_volumes` or `docker_volumes` in the job destination (or the
equivalent TPV `params`):

```yaml
singularity_volumes: $defaults,/cvmfs/idc.galaxyproject.org:ro
```

Compute nodes on remote clusters need the repository mounted before it can be
bound, either natively or with a user-space mount such as
[cvmfsexec](https://github.com/cvmfs/cvmfsexec). A bind of a path the node
doesn't have fails every job on that destination, so only add it where the
repository is mounted.

## Pick up new data

New IDC data reaches CVMFS clients by itself: the Stratum 1s take a snapshot
every hour, and clients see the new revision a few minutes later. Galaxy reads
its data tables at start-up, though, so new entries only appear in tools after a
restart or after an admin reloads the table:

```bash
curl -s -H "x-api-key: $GALAXY_ADMIN_KEY" "https://galaxy.example.org/api/tool_data/<table>/reload"
```

`GET /api/tool_data/<table>`, which needs no key, shows what the table holds.
