# Using IDC data in Galaxy

For Galaxy administrators: how to make the IDC's reference data available to
the tools on your server. Everything is read-only, served over
[CVMFS](https://cernvm.cern.ch/fs/) from the `idc.galaxyproject.org` repository,
and needs no download or indexing on your side.

## 1. Mount the repository

Install the CVMFS client and configure the `galaxyproject.org` domain (its
Stratum 1 servers and public keys). The
[galaxyproject.cvmfs](https://github.com/galaxyproject/ansible-cvmfs) Ansible
role does this, and [cvmfs-example](https://github.com/usegalaxy-eu/cvmfs-example)
is a minimal playbook using it. The
[Galaxy Training Network tutorial on reference data with CVMFS](https://training.galaxyproject.org/training-material/topics/admin/tutorials/cvmfs/tutorial.html)
walks through the whole setup.

Once mounted, the repository looks like this:

```
/cvmfs/idc.galaxyproject.org/
├── config/     tool_data_table_conf.xml and the .loc files it points at
├── data/       the reference data itself
└── record/     markers of what has been imported (used by the IDC's own tooling)
```

Check it with `ls /cvmfs/idc.galaxyproject.org/config/`. CVMFS mounts on
first access (autofs), so the directory may look empty until you open it.

## 2. Load the IDC's data tables

Add the IDC's data table configuration to `tool_data_table_config_path` in
`galaxy.yml`, next to whatever you already load:

```yaml
galaxy:
  tool_data_table_config_path: /cvmfs/idc.galaxyproject.org/config/tool_data_table_conf.xml,/srv/galaxy/config/tool_data_table_conf.xml
```

(`tool_data_table_config_path` is a comma-separated list; keep your own entries,
e.g. the shed-installed tables Galaxy manages itself.) Every table in the file
reads its `.loc` from `/cvmfs/idc.galaxyproject.org/config/`, and the paths in
those `.loc` files point into the repository, so nothing else needs
configuring. The [catalog](catalog.md#data-tables) lists the
tables.

Restart Galaxy to pick up the new configuration.

**Before you add it, check for overlapping entries** (see
[Duplicate values](#duplicate-values-across-sources) below) if you already load
reference data from another source, such as `data.galaxyproject.org`.

## 3. Make the data visible inside jobs

Tools read the files at their `/cvmfs/...` paths, so a job has to see the
repository at the same path:

- **Jobs on the Galaxy host or on nodes that mount CVMFS natively**: nothing to
  do.
- **Containerised jobs** (Singularity/Apptainer, Docker): bind the repository
  into the container, read-only. In a job destination that is
  `singularity_volumes` / `docker_volumes` (or the matching TPV `params`):

  ```yaml
  singularity_volumes: $defaults,/cvmfs/idc.galaxyproject.org:ro
  ```

- **Remote clusters** (Pulsar, other HPC): the compute nodes need the repository
  too, mounted natively or through a user-space mount such as
  [cvmfsexec](https://github.com/cvmfs/cvmfsexec), before it can be bound.

Binding a path the node hasn't mounted fails every job on that destination, so
add the bind only where the repository is available.

## 4. Pick up new data

CVMFS updates reach clients automatically: a new IDC publish is served by the
Stratum 1s after their next snapshot (hourly) and seen by your client within
minutes after that. Galaxy, however, reads data tables into memory at start
up. To make a new entry selectable in tools, either restart Galaxy or reload the
table as an admin:

```bash
curl -s -H "x-api-key: $GALAXY_ADMIN_KEY" "https://galaxy.example.org/api/tool_data/<table>/reload"
```

It's a `GET`. `GET /api/tool_data/<table>` (no key needed) shows what the table
holds afterwards.

## Duplicate values across sources

A data table defined in several of the files in `tool_data_table_config_path`
(say `all_fasta` from both `data.galaxyproject.org` and the IDC) is merged into
one table. Galaxy drops a merged row only if it is identical *in every column*
to one it already has. A row with the same `value` but a different `path` is
kept, so the table then has two rows for one identifier. The code knows this
([`lib/galaxy/tool_util/data/__init__.py`](https://github.com/galaxyproject/galaxy/blob/dev/lib/galaxy/tool_util/data/__init__.py),
`merge_tool_data_table`, with a FIXME saying so).

When a tool then resolves that identifier, it gets the fields of every matching
row: a `${...fields.path}` in the tool's command becomes `pathA,pathB`, and the
job fails.

What that means in practice:

- **The IDC doesn't publish a `value` another widely loaded source already
  serves.** Requests are checked against the data tables of
  test.galaxyproject.org, which loads the IDC together with the other
  galaxyproject.org repositories, and are not built if the data is already
  there.
- **If you load other reference data too**, compare before adding the IDC's
  tables: for each table the IDC defines, the `value` columns of its `.loc`
  file and of your existing sources should not overlap. After adding it,
  `GET /api/tool_data/<table>` listing the same `value` twice is the symptom.
- A row that is the same everywhere (same `value`, same `path`) is harmless;
  it's the same-`value`, different-`path` case that breaks jobs.

## Which servers use it

test.galaxyproject.org loads the IDC's data tables; it is also the server new
IDC builds run on and are checked against. Any other server can mount the
repository the same way.
