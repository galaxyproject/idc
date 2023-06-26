#!/usr/bin/env python3
import os
import requests
from bioblend.galaxy import GalaxyInstance

url = 'http://idc-build'

gi = GalaxyInstance(url=url, email='idc@galaxyproject.org', password=os.environ['IDC_USER_PASS'])

history = gi.histories.get_histories(name='Data Manager History (automatically created)', deleted=False)[0]
history_id = history['id']
datasets = gi.datasets.get_datasets(history_id=history_id, order='create_time-asc')
dataset_id = datasets[0]['id']
#print(f"Bundle dataset: {datasets[0]}")

bundle_url = f"{url}/api/datasets/{dataset_id}/display?to_ext=data_manager_json"
print(bundle_url)
#print(f"Importing bundle from URL: {bundle_url}")

#params = {
#    "key": "deadbeef",
#}
#
#data = {
#    "source": {
#        "src": "uri",
#        "uri": bundle_url
#    }
#}
#
#r = requests.post(f"http://localhost:8080/api/tool_data", params=params, json=data)
#print(r, r.text)
