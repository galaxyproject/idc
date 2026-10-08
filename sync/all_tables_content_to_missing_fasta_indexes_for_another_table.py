import argparse
import logging
import sys
import yaml

parser = argparse.ArgumentParser(
    description="Find values which are present in another table but not in fasta_indexes"
)
parser.add_argument(
    '-i', '--input',
    help="Full path to the output of tool_data_table_conf_to_yaml.py"
)
parser.add_argument('-t', '--table',
                    help="Name of the table for which entries should be checked.")
parser.add_argument('-o', '--output', default=sys.stdout,
                    type=argparse.FileType('w'),
                    help="Output file with all fasta values for which fasta_indexes should be run.")
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
with open(args.input, 'r') as f:
    all_tables_content = yaml.safe_load(f)
logger.info("Done")

# Get all fasta
fasta_value_to_dict = {}
for entry in all_tables_content['all_fasta']:
    dbkey = entry.get('dbkey')
    value = entry.get('value')
    fasta_value_to_dict[value] = {}
    fasta_value_to_dict[value]['dbkey'] = dbkey

fasta_indexes_without_fasta = []
# Get fasta_indexes entries
for entry in all_tables_content['fasta_indexes']:
    dbkey = entry.get('dbkey')
    value = entry.get('value')
    if value not in fasta_value_to_dict:
        logger.warning(f"{value} is in fasta_indexes but not in all_fasta.")
        fasta_indexes_without_fasta.append(value)
        continue
    if fasta_value_to_dict[value]['dbkey'] != dbkey:
        logger.warning(f"{value} is in fasta_indexes and in all_fasta but not with the same dbkey!")
        fasta_indexes_without_fasta.append(value)
        continue
    fasta_value_to_dict[value]['fasta_indexes'] = True

table_name = args.table
missing_values = []
logger.info(f"Checking table {table_name} entries")
for entry in all_tables_content[table_name]:
    dbkey = entry.get('dbkey')
    value = entry.get('value')
    if value not in fasta_value_to_dict:
        logger.warning(
            f"{value} is in {table_name} from {entry.get('loc_file')} but not in all_fasta."
        )
        if value in fasta_indexes_without_fasta:
            logger.warning(
                f"However, there is a entry {value} in fasta_indexes."
            )
    else:
        if 'fasta_indexes' in fasta_value_to_dict[value]:
            # Everything is fine
            continue
        else:
            # We need to compute the index
            missing_values.append(value)

args.output.write('\n'.join(sorted(set(missing_values))))
