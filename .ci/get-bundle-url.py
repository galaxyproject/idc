#!/usr/bin/env python3
import argparse
import os

import requests
from bioblend.galaxy import GalaxyInstance


EXT = 'data_manager_json'

parser = argparse.ArgumentParser(description="")
parser.add_argument(
    "-g", "--galaxy-url", default="http://localhost:8080", help="The Galaxy server URL"
)
parser.add_argument(
    "-u", "--galaxy-user", default="idc@galaxyproject.org", help="Galaxy user email"
)
parser.add_argument(
    "-p", "--galaxy-password", help="Galaxy user password (or set $IDC_USER_PASS)"
)
parser.add_argument(
    "-n", "--history-name", default="Data Manager History (automatically created)", help="History name"
)
args = parser.parse_args()

gi = GalaxyInstance(
    url=args.galaxy_url, email=args.galaxy_user, password=args.galaxy_password or os.environ.get("IDC_USER_PASS")
)

history = gi.histories.get_histories(name=args.history_name, deleted=False)[0]
history_id = history['id']
datasets = gi.datasets.get_datasets(
    history_id=history_id, extension=EXT, order="create_time-dsc"
)
dataset_id = datasets[0]['id']

bundle_url = f"{url}/api/datasets/{dataset_id}/display?to_ext={EXT}"
print(bundle_url)
