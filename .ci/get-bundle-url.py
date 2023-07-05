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
    "-a", "--galaxy-api-key", help="Galaxy API key (or set $EPHEMERIS_API_KEY)"
)
parser.add_argument(
    "-n", "--history-name", default="Data Manager History (automatically created)", help="History name"
)
parser.add_argument(
    "-r", "--record-file", help="Record file"
)
args = parser.parse_args()

api_key = args.galaxy_api_key or os.environ.get("EPHEMERIS_API_KEY")
password = args.galaxy_password or os.environ.get("IDC_USER_PASS")
if api_key:
    auth_kwargs = {"key": api_key}
elif password:
    auth_kwargs = {"email": args.galaxy_user, "password": password}
else:
    raise RuntimeError("No Galaxy credentials supplied")

gi = GalaxyInstance(url=args.galaxy_url, **auth_kwargs)

history = gi.histories.get_histories(name=args.history_name, deleted=False)[0]
history_id = history['id']
datasets = gi.datasets.get_datasets(
    history_id=history_id, extension=EXT, order="create_time-dsc"
)
dataset_id = datasets[0]['id']

bundle_url = f"{args.galaxy_url}/api/datasets/{dataset_id}/display?to_ext={EXT}"

if args.record_file:
    with open(args.record_file, "w") as fh:
        fh.write(f"galaxy_url: {args.galaxy_url}\n")
        fh.write(f"history_id: {history_id}\n")
        fh.write(f"history_url: {args.galaxy_url}/{history['url']}\n")
        fh_write(f"bundle_dataset_id: {dataset_id}\n")
        fh.write(f"bundle_dataset_url: {bundle_url}\n")

print(bundle_url)
