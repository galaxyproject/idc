import yaml

full_yaml_path = 'sync/cvmfs_20260701.yml'
output_path = 'sync/test.yml'

# If it is related to dbkey I keep specific dbkeys
dbkeys_to_keep = ['Amel_4.5', 'apiMel4']
# If they are not present or it is not related to dbkeys
# I keep a fixed amount of entries
nb_to_keep = 1

print("Loading the big yaml file.")
with open(full_yaml_path, 'r') as f:
    full_dict = yaml.safe_load(f)
print("Done")

test_dict = {}

for table in full_dict:
    test_dict[table] = []
    if table == '__dbkeys__':
        for entry in full_dict[table]:
            if entry['value'] in dbkeys_to_keep:
                test_dict[table].append(
                    entry
                )
    else:
        for entry in full_dict[table]:
            if entry.get('dbkey') in dbkeys_to_keep:
                test_dict[table].append(
                    entry
                )
        if len(test_dict[table]) == 0 and len(full_dict[table]) > 0:
            test_dict[table] = full_dict[table][:nb_to_keep]

with open(output_path, "w") as fo:
    yaml.dump(test_dict, fo)
