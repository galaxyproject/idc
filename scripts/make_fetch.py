#!/bin/env python

import yaml
from collections import defaultdict
import re
import os
import sys
import argparse

def main():

    VERSION = 0.1
    FETCH_DM_TOOL = 'toolshed.g2.bx.psu.edu/repos/devteam/data_manager_fetch_genome_dbkeys_all_fasta/data_manager_fetch_genome_all_fasta_dbkey/0.0.2'
    FETCH_DM_POST = {'data_table_reload': ['all_fasta','__dbkeys__']}

    parser = argparse.ArgumentParser(description="")
    parser.add_argument("-g", "--genome_file", required=True, help="The genome yaml file to read.")
    parser.add_argument("-o", "--outfile", default="fetch.yml", help="The name of the output file to produce.")
    parser.add_argument("--version", action='store_true')
    parser.add_argument("--verbose", action='store_true')

    args = parser.parse_args()

    if args.version:
        print("make_fetch.py version: %.1f" % VERSION)
        return

    #Read in the genome file.
    genomes = yaml.safe_load(open(args.genome_file, 'r'))

    dm = {'data_managers': []}

    for genome in genomes['genomes']:
        #make the start
        out = {'id': FETCH_DM_TOOL}
        out['params'] = []
        out['params'].append({'dbkey_source|dbkey': genome['id']})
        if genome['source'] == 'ucsc':
            out['params'].append({'reference_source|reference_source_selector': 'ucsc'})
            out['params'].append({'reference_source|requested_dbkey': genome['id']})
        elif re.match('^[A-Z_]+[0-9.]+', genome['source']):
            out['params'].append({'dbkey_source|dbkey_source_selector': 'new'})
            out['params'].append({'reference_source|reference_source_selector': 'ncbi'})
            out['params'].append({'reference_source|requested_identifier': genome['source']})
            out['params'].append({'sequence_name': genome['description']})
            out['params'].append({'sequence.id': genome['id']})
        elif re.match('^http', genome['source']):
            out['params'].append({'dbkey_source|dbkey_source_selector': 'new'})
            out['params'].append({'reference_source|reference_source_selector': 'url'})
            out['params'].append({'reference_source|user_url': genome['source']})
            out['params'].append({'sequence_name': genome['description']})
            out['params'].append({'sequence.id': genome['id']})
        out['data_table_reload'] = ['all_fasta','__dbkeys__']

        dm['data_managers'].append(out)

    with open(args.outfile, 'w') as out:
        yaml.dump(dm, out, default_flow_style=False)


if __name__ == "__main__": main()
