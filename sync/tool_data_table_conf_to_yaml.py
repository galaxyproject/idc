"""
Scan the xml tool_data_table_conf and
generate a yaml with the paths ordered
per table
"""

import argparse
import logging
import os.path
import sys
import urllib.request
import xml.etree.ElementTree as ET
import yaml


def update_dictionary(all_entries_per_table, name, comment_char, columns, path, file_type, xml_file):
    logger.info(f"Processing table {name} from {xml_file}")
    if file_type == "path":
        if not os.path.isfile(path):
            logger.warning(
                f"The file {path} supposely containing the {name} table do not exists."
            )
            return(all_entries_per_table)
        f = open(path, 'r')
    elif file_type == "url":
        try:
            f = urllib.request.urlopen(path)
        except:
            logger.warning(
                f"The url {path} supposely containing the {name} table do not exists."
            )
            return(all_entries_per_table)
    else:
        logger.warning(
            f"The file_type {file_type} is not supported."
        )
        return(all_entries_per_table)
    if name not in all_entries_per_table.keys():
        all_entries_per_table[name] = []
    for line in f:
        if file_type == "url":
            line = line.decode('utf-8', errors='replace')
        if comment_char is not None and line.startswith(comment_char):
            continue
        if line.strip() == "":
            continue
        values = line.strip().split("\t")
        dict_to_store = dict(zip(columns, values))
        dict_to_store['loc_file'] = path
        dict_to_store['xml_file'] = xml_file
        all_entries_per_table[name].append(dict_to_store)
    f.close()
    return(all_entries_per_table)


parser = argparse.ArgumentParser(
    description="Scan tool_data_table_conf and generate yamls with paths"
)
parser.add_argument(
    '-t', '--tool_data_table_conf',
    nargs='+',
    default=[
        "/cvmfs/data.galaxyproject.org/byhand/location/tool_data_table_conf.xml",
        "/cvmfs/data.galaxyproject.org/managed/location/tool_data_table_conf.xml",
        "/cvmfs/brc.galaxyproject.org/config/tool_data_table_conf.xml",
        "/cvmfs/vgp.galaxyproject.org/config/tool_data_table_conf.xml"
    ],
    help="Full path to tool_data_table_conf.xml file(s)"
)
parser.add_argument('-o', '--output', default=sys.stdout,
                    type=argparse.FileType('w'),
                    help="Output yaml file with all paths grouped by table name.")
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

all_entries_per_table = {}
for xml_file in args.tool_data_table_conf:
    tree = ET.parse(xml_file)
    root = tree.getroot()
    for table in root:
        name = table.attrib['name']
        comment_char = table.attrib.get('comment_char', None)
        for info in table:
            if info.tag == "columns":
                columns = [c.strip() for c in info.text.split(',')]
            if info.tag == "file":
                path = info.get('path')
                file_type = 'path'
                if path is None:
                    path = info.get('url')
                    file_type = 'url'
        all_entries_per_table = update_dictionary(all_entries_per_table, name, comment_char, columns, path, file_type, xml_file)

yaml.dump(all_entries_per_table, args.output)
