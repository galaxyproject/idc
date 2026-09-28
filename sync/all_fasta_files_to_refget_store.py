import argparse
import gc
import json
import logging
import os
from dataclasses import dataclass, asdict

import yaml
from pathlib import Path
from typing import Any

from gtars.refget import RefgetStore, SequenceCollectionMetadata


@dataclass
class FastaAllRecord:
    dbkey: str
    name: str
    path: str
    value: str
    loc_file: str
    xml_file: str


@dataclass
class AliasRecord:
    galaxy_unique_build_id: str
    galaxy_dbkey: str
    galaxy_name: str
    galaxy_loc_file: str
    galaxy_tool_data_table_conf: str


def main(
    cvmfs_yaml_path: Path, output_path: Path, cvmfs_mount_prefix: Path, no_store: bool,
    logger: Any
):
    refget_store_path = output_path.joinpath("store")

    if no_store:
        store = None
    else:
        logger.info(f"Created/opened refgetstore at {refget_store_path}")
        store = RefgetStore.on_disk(refget_store_path)

    fasta_all = read_fasta_all_yaml(cvmfs_yaml_path, logger)

    check_for_duplicate_genomes(fasta_all, logger)

    import_fasta_all(
        fasta_all,
        output_path,
        cvmfs_mount_prefix,
        store,
        logger,
    )


def read_fasta_all_yaml(cvmfs_yaml_path: Path, logger: Any) -> list[FastaAllRecord]:
    logger.info(f"Loading the big yaml file: {cvmfs_yaml_path}")

    with open(cvmfs_yaml_path, "r") as cvmfs_yaml_file:
        cvmfs_yaml = yaml.safe_load(cvmfs_yaml_file)

    out = []
    for el in cvmfs_yaml["all_fasta"]:
        out.append(FastaAllRecord(**el))
    return out


def check_for_duplicate_genomes(fasta_all: list[FastaAllRecord], logger: Any):
    unique_vals = set()
    for fasta_record in fasta_all:
        if fasta_record.value not in unique_vals:
            unique_vals.add(fasta_record.value)
        else:
            logger.warning(
                f"Duplicate unique_build_id value found: {fasta_record.value}"
            )


def import_fasta_all(
    fasta_all: list[FastaAllRecord],
    output_path: Path,
    cvmfs_mount_prefix: Path,
    store: RefgetStore | None,
    logger: Any,
):
    rgsi_output_path = output_path.joinpath("rgsi")
    json_output_path = output_path.joinpath("json")
    yaml_output_path = output_path.joinpath("yaml")
    os.makedirs(rgsi_output_path, exist_ok=True)
    os.makedirs(json_output_path, exist_ok=True)
    os.makedirs(yaml_output_path, exist_ok=True)

    os.chdir(rgsi_output_path)

    all_fasta_yaml = load_all_fasta_yaml(yaml_output_path)

    for i, fasta_record in enumerate(fasta_all):
        unique_build_id = fasta_record.value

        if fasta_record.path.startswith("${__HERE__}"):
            loc_file_dir = os.path.dirname(fasta_record.loc_file)
            fasta_record.path = fasta_record.path.replace("${__HERE__}", loc_file_dir)

        cvmfs_fasta_path = cvmfs_mount_prefix / Path(fasta_record.path).relative_to(
            cvmfs_mount_prefix.anchor
        )
        local_fasta_path = rgsi_output_path.joinpath(unique_build_id + ".fa")

        json_summary_path = json_output_path.joinpath(unique_build_id + ".json")

        if os.path.exists(cvmfs_fasta_path):
            if os.path.islink(local_fasta_path):
                os.unlink(local_fasta_path)
            logger.info(f"Symlinking {local_fasta_path} to {cvmfs_fasta_path}...")
            local_fasta_path.symlink_to(cvmfs_fasta_path)
        else:
            logger.warning(
                f"Fasta file {cvmfs_fasta_path} does not exist, skipping import..."
            )
            continue


        if not os.access(cvmfs_fasta_path, os.R_OK):
            logger.warning(
                f"Fasta file {cvmfs_fasta_path} is not readable, skipping import..."
            )
            continue

        if os.path.exists(json_summary_path):
            logger.info(
                f"JSON summary file {json_summary_path} already exists, skipping import..."
            )
            continue

        # Use the persistent store if available; otherwise create a fresh
        # in-memory store per genome so sequences are freed after each genome
        # instead of accumulating in RAM across the whole run.
        genome_store = store
        if genome_store is None:
            genome_store = RefgetStore.in_memory()

        logger.info(
            f"Processing fasta {i + 1}/{len(fasta_all)}."
        )

        try:
            collection, new = genome_store.add_sequence_collection_from_fasta(local_fasta_path)
        except Exception as e:
            logger.info(f"Could not load {local_fasta_path}: {e}")
            continue

        add_galaxy_aliases_to_store(genome_store, collection, fasta_record)

        refget_metadata_blob = {
            "level_0": collection.digest,
            "level_1": {
                "names": collection.names_digest,
                "lengths": collection.lengths_digest,
                "sequences": collection.sequences_digest,
                "name_length_pairs": collection.name_length_pairs_digest,
                "sorted_name_length_pairs": collection.sorted_name_length_pairs_digest,
                "sorted_sequences": collection.sorted_sequences_digest,
            },
            "level_2": genome_store.get_collection_level2(collection.digest),
            "aliases": asdict(
                AliasRecord(**dict(genome_store.get_aliases_for_collection(collection.digest)))
            ),
        }

        all_fasta_yaml[unique_build_id] = build_all_fasta_metadata_blob(refget_metadata_blob)

        append_to_all_fasta_yaml_file(yaml_output_path, all_fasta_yaml)
        write_single_genome_json_file(json_summary_path, refget_metadata_blob)

        del collection, refget_metadata_blob, genome_store
        gc.collect()


def add_galaxy_aliases_to_store(
    store: RefgetStore,
    collection: SequenceCollectionMetadata,
    fasta_record: FastaAllRecord,
):
    def _add_safe_alias_to_store(
        store: RefgetStore,
        collection: SequenceCollectionMetadata,
        alias: str,
        value: str,
    ):
        # Exchange slashes with '!' to support values in URL for the Seqcol API implementation
        store.add_collection_alias(alias, value.replace("/", "!"), collection.digest)

    _add_safe_alias_to_store(
        store, collection, "galaxy_unique_build_id", fasta_record.value
    )
    _add_safe_alias_to_store(store, collection, "galaxy_dbkey", fasta_record.dbkey)
    _add_safe_alias_to_store(store, collection, "galaxy_name", fasta_record.name)
    _add_safe_alias_to_store(
        store, collection, "galaxy_loc_file", fasta_record.loc_file
    )
    _add_safe_alias_to_store(
        store, collection, "galaxy_tool_data_table_conf", fasta_record.xml_file
    )


def write_single_genome_json_file(
    json_summary_path: Path, refget_metadata_blob: dict[str, Any]
):
    print(f"Writing JSON summary file: {json_summary_path}")
    with open(json_summary_path, "w") as refget_file:
        json.dump(refget_metadata_blob, refget_file, indent=2)
        print(file=refget_file)


def load_all_fasta_yaml(yaml_output_path: Path) -> dict[str, Any]:
    all_fasta_yaml_path = yaml_output_path.joinpath("all_fasta.yml")

    if os.path.exists(all_fasta_yaml_path):
        with open(all_fasta_yaml_path, "r") as all_fasta_yaml_file:
            return yaml.safe_load(all_fasta_yaml_file) or {}
    return {}


def build_all_fasta_metadata_blob(refget_metadata_blob: dict[str, Any]) -> dict[str, Any]:
    return {
        "level_0": refget_metadata_blob["level_0"],
        "level_1": refget_metadata_blob["level_1"],
        "level_2_peek": {
            "sequence_count": len(refget_metadata_blob["level_2"]["sequences"]),
            "first_name": refget_metadata_blob["level_2"]["names"][0],
            "first_length": refget_metadata_blob["level_2"]["lengths"][0],
            "first_sequence": refget_metadata_blob["level_2"]["sequences"][0],
        },
        "aliases": refget_metadata_blob["aliases"],
    }


def append_to_all_fasta_yaml_file(
    yaml_output_path: Path, all_fasta_yaml: dict[str, Any]
):
    all_fasta_yaml_path = yaml_output_path.joinpath("all_fasta.yml")

    with open(all_fasta_yaml_path, "w") as all_fasta_file:
        yaml.dump(
            all_fasta_yaml, all_fasta_file, sort_keys=False, default_flow_style=False
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generates Refgetstore instance from all FASTA tables - "
        "including 'GA4GH refget: sequence collections'-compatible digests "
        "and chromLen info."
    )

    parser.add_argument(
        "tool_data_table_yaml_path",
        type=Path,
        help="Path to the output yaml file created by `tool_data_table_conf_to_yaml.py`",
    )

    parser.add_argument(
        "output_path",
        type=Path,
        help="Path to the output directory where the refget store and digest summaries will be "
        "created.",
    )

    parser.add_argument(
        "-c",
        "--cvmfs-mount-prefix",
        type=Path,
        default=Path("/"),
        help="Path prefix to where CVMFS is mounted, useful for testing on e.g. a Mac if (default: /)",
    )

    parser.add_argument(
        "-n", "--no-store", action="store_true", help="Do not create the Refgetstore."
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

    main(
        args.tool_data_table_yaml_path,
        Path.absolute(args.output_path),
        args.cvmfs_mount_prefix,
        args.no_store,
        logger
    )
