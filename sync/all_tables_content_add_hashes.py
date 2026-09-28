import argparse
import hashlib
import logging
import os
import os.path
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

parser = argparse.ArgumentParser(
    description="Take all_tables_content yaml file and add hash and manifest (contents + hashes). Output yaml per data table."
)
parser.add_argument(
    "-i", "--input", help="Full path to the output of tool_data_table_conf_to_yaml.py"
)
parser.add_argument(
    "--threads",
    type=int,
    default=8,
    help="Number of threads to use for hash computation",
)
parser.add_argument(
    "-o", "--output", default="./", type=str, help="Prefix for the yaml ouput files"
)
parser.add_argument(
    "-log",
    "--loglevel",
    choices=["debug", "info", "warning", "error"],
    default="warning",
    help="Provide logging level. Example --loglevel debug, default=warning",
)

args = parser.parse_args()

logging.getLogger().setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
# Set the log level for your logger to the desired level (e.g., INFO)
logger.setLevel(args.loglevel.upper())

# Create a handler for logging output (e.g., console handler)
handler = logging.StreamHandler()
logger.addHandler(handler)

# Add a formatter to the handler (optional)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)

# First load the yaml
logger.info("Loading the big yaml file.")
with open(args.input, "r") as f:
    all_tables_content = yaml.safe_load(f)
logger.info("Done")

# Create the output directory
os.makedirs(str(args.output), exist_ok=True)

def file_hash(path):
    """
    compute hash for a file
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_matching_files(pathspec):
    """
    iterate over a path given in a data table. cases:

    - file: just iterate the file
    - directory: iterate (recursive) over the directory contents
    - else (prefix): iterate over the parents contents (recursively)
    """

    path_list = pathspec.split(",")
    if len(path_list) > 1:
        for p in path_list:
            yield from iter_matching_files(p)
        return

    p = Path(pathspec)
    if p.is_file():
        parent = p.parent
        prefix = p.name
        yield from sorted(
            (str(f.relative_to(parent)), f) for f in parent.glob(f"{prefix}*") if not f.is_dir()
        )

    elif p.is_dir():
        yield from sorted(
            (str(f.relative_to(p)), f) for f in p.rglob("*") if not f.is_dir()
        )
    else:
        # this is in most cases a prefix. we still take all files contained
        # in the parent directory. this is because for instance the blast databases
        # also contain files, taxdb.btd, taxdb.bti and taxonomy4blast.sqlite3
        # which do not match the prefix
        parent = p.parent
        prefix = p.name

        yield from sorted(
            (str(f.relative_to(parent)), f) for f in parent.rglob("*") if not f.is_dir()
        )


def hash_one(args):
    """
    get the info stored in the manifest (path, size, hash, symlink)
    """
    rel, path = args
    size = path.stat().st_size
    digest = file_hash(path)
    return {"path": rel, "size": size, "digest": digest, "symlink": path.is_symlink()}


def manifest_and_hash(root, max_workers=8):
    """
    comput manifest (all contents + metainfo) and a global hash
    """
    files = iter_matching_files(root)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        manifest = list(executor.map(hash_one, [f for f in files]))

    # Compute the master hash in deterministic order
    master = hashlib.sha256()

    for m in manifest:
        master.update(str(m["path"]).encode())
        master.update(b"\0")
        master.update(str(m["symlink"]).encode())
        master.update(b"\0")
        master.update(str(m["size"]).encode())
        master.update(b"\0")
        master.update(m["digest"].encode())
        master.update(b"\0")

    return manifest, master.hexdigest()

# for table_name in sorted_all_tables_names:
for table_name in all_tables_content:

    yaml_path = str(args.output) + "/" + table_name + ".yaml"
    if os.path.exists(yaml_path):
        # Read the existing file
        with open(yaml_path, 'r') as file:
            old_content_with_hashes = yaml.safe_load(file)
            assert table_name in old_content_with_hashes
    else:
        old_content_with_hashes = {table_name: []}
    content_with_hashes = {}
    content_with_hashes[table_name] = []

    # if the yaml contained data
    # the old data is stored in a dictionary where
    # keys are the original entry
    # (without manifest and digest keys)
    # and values are the full entries
    entries = dict()
    for hashed_old_entry in old_content_with_hashes[table_name]:
        old_entry = {}
        for key in hashed_old_entry:
            if key not in ["manifest", "digest"]:
                old_entry[key] = hashed_old_entry[key]
        entries[str(sorted(old_entry.items()))] = hashed_old_entry

    logger.info(
        f"Checking table {table_name}: {len(all_tables_content[table_name])} entries"
    )
    percent_reported = -1
    for i, entry in enumerate(all_tables_content[table_name]):
        # First we try to get the old manifest
        old_manifest = []
        entry_string = str(sorted(entry.items()))
        if entry_string in entries:
            # Try to get the old_manifest
            old_manifest = entries[entry_string]['manifest']
        # If the old_manifest is not empty we just copy it from the old data
        if len(old_manifest) > 0:
            logger.info(
                f"Skipping table {table_name} entry {i}: {entry}"
            )
            content_with_hashes[table_name].append(entries[entry_string])
            continue
        # Else we process the path
        round_percent = ((i * 100) // len(all_tables_content[table_name]))
        if round_percent % 10 == 0 and round_percent != percent_reported:
            logger.info(f"Submitted {round_percent}%")
            percent_reported = round_percent
        path = None
        for c in entry:
            if entry[c].startswith("/") and c not in ["xml_file", "loc_file"]:
                path = entry[c]
        if path is None:
            continue
        try:
            entry["manifest"], entry["digest"] = manifest_and_hash(path, args.threads)
        except Exception as e:
            logger.error(f"Could not compute digest of {path}: {e}")
            continue

        content_with_hashes[table_name].append(entry)

    if len(content_with_hashes) == 0:
        logger.warning(f"No data in {table_name}")
        continue

    with open(yaml_path, "w") as f:
        yaml.dump(content_with_hashes, f)
