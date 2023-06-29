#!/bin/bash

set -e

: ${GALAXY_DOCKER_IMAGE:="quay.io/bgruening/galaxy"}
: ${GALAXY_PORT:="8080"}
: ${GALAXY_DEFAULT_ADMIN_USER:="admin@galaxy.org"}
: ${GALAXY_DEFAULT_ADMIN_PASSWORD:="password"}
: ${EXPORT_DIR:="/mnt/data/export/"}
: ${DATA_MANAGER_DATA_PATH:="${EXPORT_DIR}/data_manager"}

: ${PLANEMO_PROFILE_NAME:="wxflowtest"}
: ${PLANEMO_SERVE_DATABASE_TYPE:="postgres"}

GALAXY_URL="http://localhost:$GALAXY_PORT"

git diff --name-only $TRAVIS_COMMIT_RANGE -- '*.yml' '*.yaml' > changed_files.txt
echo "Following files have changed."
cat changed_files.txt

if [ ! -f .venv ]; then
    virtualenv .venv
    . .venv/bin/activate
    pip install -U pip
    pip install ephemeris
fi

echo 'ephemeris installed'

. .venv/bin/activate

mkdir -p ${DATA_MANAGER_DATA_PATH}

sudo cp scripts/job_conf.xml ${EXPORT_DIR}/job_conf.xml

docker run -d --rm -v ${EXPORT_DIR}:/export/ -e GALAXY_CONFIG_JOB_CONFIG_FILE=/export/job_conf.xml -e GALAXY_CONFIG_GALAXY_DATA_MANAGER_DATA_PATH=/export/data_manager/ -e GALAXY_CONFIG_WATCH_TOOL_DATA_DIR=True -p 8080:80 --name idc_builder ${GALAXY_DOCKER_IMAGE}

echo 'Waitng for Galaxy'

galaxy-wait -g ${GALAXY_URL}

chmod 0777 ${DATA_MANAGER_DATA_PATH}


#if [ -s changed_files.txt ]
#then
#  for FILE in `cat changed_files.txt`;
#    do
#      if [[ $FILE == *"data-managers"* ]]; then
#         #### RUN single data managers
#         shed-tools install -d $FILE -g ${GALAXY_URL} -u $GALAXY_DEFAULT_ADMIN_USER -p $GALAXY_DEFAULT_ADMIN_PASSWORD
#         run-data-managers --config $FILE -g ${GALAXY_URL} -u $GALAXY_DEFAULT_ADMIN_USER -p $GALAXY_DEFAULT_ADMIN_PASSWORD
#      elif [[ $FILE == *"idc-workflows"* ]]; then
#         #### RUN the pipline for new genome
#         shed-tools install -d $FILE -g ${GALAXY_URL} -u $GALAXY_DEFAULT_ADMIN_USER -p $GALAXY_DEFAULT_ADMIN_PASSWORD
#         run-data-managers --config $FILE -g ${GALAXY_URL} -u $GALAXY_DEFAULT_ADMIN_USER -p $GALAXY_DEFAULT_ADMIN_PASSWORD
#     fi
#  done
#fi

echo 'Installing Data Managers'
# Install the data managers
shed-tools install -t data_managers_tools.yml -g ${GALAXY_URL} -u $GALAXY_DEFAULT_ADMIN_USER -p $GALAXY_DEFAULT_ADMIN_PASSWORD
#Let others read the shed_data_manager_conf.xml file
sudo chmod ugo+r ${EXPORT_DIR}/galaxy-central/config/shed_data_manager_conf.xml

echo 'Fetching new genomes'
#Run make_fetch.py to build the fetch manager config file for ephemeris
python scripts/make_fetch.py -g genomes.yml -x ${EXPORT_DIR}/galaxy-central/config/shed_data_manager_conf.xml
#cat data_managers_fetch.yml genomes.yml > fetch.yml
run-data-managers --config fetch.yml -g ${GALAXY_URL} -u $GALAXY_DEFAULT_ADMIN_USER -p $GALAXY_DEFAULT_ADMIN_PASSWORD

echo 'Restarting Galaxy'
#Restart Galaxy to reload the data tables
docker exec idc_builder supervisorctl restart galaxy:
galaxy-wait -g ${GALAXY_URL}
sleep 20

echo 'Building new indices'
#Run the make_dm_genomes.py script to create the list of index builders and genomes and pass it to ephemeris
python scripts/make_dm_genomes.py -d data_managers_tools.yml -x ${EXPORT_DIR}/galaxy-central/config/shed_data_manager_conf.xml -g genomes.yml
run-data-managers --config dm_genomes.yml -g ${GALAXY_URL} -u $GALAXY_DEFAULT_ADMIN_USER -p $GALAXY_DEFAULT_ADMIN_PASSWORD


ls -l ${DATA_MANAGER_DATA_PATH}

rm fetch.yml
rm dm_genomes.yml

docker stop idc_builder
