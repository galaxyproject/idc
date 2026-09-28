import argparse
import logging
import sys
import yaml

tables_to_include = [
    'alignseq_seq',
    'bmtagger',
    'bowtie2_indexes',
    'bowtie_indexes',
    'bwa_indexes',
    'bwa_indexes_color',
    'bwa_mem2_indexes',
    'bwa_mem_indexes',
    'bwameth_indexes',
    'data_manager_fetch_refseq',
    'fasta_indexes',
    'gatk_picard_indexes',
    'hisat2_indexes',
    'homer_preparse',
    # 'indexed_maf_files',
    'kallisto_indexes',
    # 'liftOver',
    'malt_indices',
    'mash_sketches',
    'mosaik_indexes',
    'ngs_sim_fasta',
    'picard_indexes',
    'rnastar_index2',
    'rnastar_index2x_versioned',
    'sam_fa_indexes',
    'salmon_indexes_versioned',
    'srma_indexes',
    'tophat_indexes',
    'tophat_indexes_color',
    'tophat2_indexes',
    'twobit',
    'vsnp_dnaprints',
    'vsnp_excel',
    'vsnp_genbank'
]


parser = argparse.ArgumentParser(
    description="Rearrange the all_tables_content yaml file to have a dbkey centric view"
)
parser.add_argument(
    '-i', '--input',
    help="Full path to the output of tool_data_table_conf_to_yaml.py"
)
parser.add_argument('-o', '--output', default=sys.stdout,
                    type=argparse.FileType('w'),
                    help="Output yaml file with all dbkey related paths grouped by dbkey.")
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

# Get all dbkeys
all_entries_per_dbkeys = {}
for entry in all_tables_content['__dbkeys__']:
    all_entries_per_dbkeys[entry.get('value')] = {
        'name': entry.get('name'),
        'len_path': entry.get('len_path')
    }

# Get all fasta
# And get a value to dbkey table
fasta_value_to_dbkey = {}
for entry in all_tables_content['all_fasta']:
    dbkey = entry.get('dbkey')
    value = entry.get('value')
    del entry['value']
    del entry['dbkey']
    if dbkey not in all_entries_per_dbkeys:
        logger.warning(
            f"{dbkey} dbkey is in the all_fasta but not in '__dbkeys__'"
        )
        all_entries_per_dbkeys[dbkey] = {
            # 'name': 'Not set in __dbkeys__',
            # 'len_path': 'Not set in __dbkeys__'
        }
    if value in all_entries_per_dbkeys[dbkey]:
        logger.warning(
            f"{value} is present twice in all_fasta. Please check."
        )
    else:
        fasta_value_to_dbkey[value] = dbkey
        all_entries_per_dbkeys[dbkey][value] = {'all_fasta': []}
    all_entries_per_dbkeys[dbkey][value]['all_fasta'].append(
        entry
    )

for table_name in all_tables_content:
    if table_name not in tables_to_include:
        continue
    logger.info(f"Checking table {table_name} entries")
    for entry in all_tables_content[table_name]:
        dbkey = entry.get('dbkey')
        value = entry.get('value')
        if not value in fasta_value_to_dbkey:
            logger.warning(
                f"{value} is in {table_name} from {entry.get('loc_file')} but not in all_fasta"
            )
            if dbkey is None:
                dbkey = 'No_dbkey'
        if dbkey is None:
            dbkey = fasta_value_to_dbkey[value]
        elif dbkey != 'No_dbkey':
            del entry['dbkey']
        if dbkey not in all_entries_per_dbkeys:
            all_entries_per_dbkeys[dbkey] = {
                # 'name': 'Not set in __dbkeys__',
                # 'len_path': 'Not set in __dbkeys__'
            }
        if value not in all_entries_per_dbkeys[dbkey]:
            all_entries_per_dbkeys[dbkey][value] = {}
        if table_name not in all_entries_per_dbkeys[dbkey][value]:
            all_entries_per_dbkeys[dbkey][value][table_name] = []
        del entry['value']
        all_entries_per_dbkeys[dbkey][value][table_name].append(
            entry
        )
        
yaml.dump(all_entries_per_dbkeys, args.output)
