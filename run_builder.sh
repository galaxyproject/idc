#!/bin/bash

: ${GALAXY_DOCKER_IMAGE:="quay.io/bgruening/galaxy:18.01"}
: ${GALAXY_PORT:="8080"}
: ${EPHEMERIS_VERSION:="0.8.0"}
: ${GALAXY_DEFAULT_ADMIN_USER:="admin@galaxy.org"}
: ${GALAXY_DEFAULT_ADMIN_PASSWORD:="admin"}
: ${EXPORT_DIR:="$HOME/export/"}
: ${DATA_MANAGER_DATA_PATH:="${EXPORT_DIR}/data_manager"}

: ${PLANEMO_PROFILE_NAME:="wxflowtest"}
: ${PLANEMO_SERVE_DATABASE_TYPE:="postgres"}

GALAXY_URL="http://localhost:$GALAXY_PORT"

if [ ! -f .venv ]; then
    virtualenv .venv
    . .venv/bin/activate
    pip install -U pip
    pip install ephemeris=="${EPHEMERIS_VERSION}"
fi

echo 'ephemeris installed'

. .venv/bin/activate

mkdir -p ${DATA_MANAGER_DATA_PATH}

docker run -d -v ${EXPORT_DIR}:/export/ -e GALAXY_CONFIG_GALAXY_DATA_MANAGER_DATA_PATH=/export/data_manager/ -p 8080:80 ${GALAXY_DOCKER_IMAGE}
galaxy-wait -g ${GALAXY_URL}

#TODO: make the yml file dynamic

#CHANGED_YAML_FILES=${git diff --name-only $TRAVIS_COMMIT_RANGE -- '*.yml' '*.yaml'}

shed-tools -t data-managers/metaphlan2_download/tools.yaml -g ${GALAXY_URL} -u $GALAXY_DEFAULT_ADMIN_USER -p $GALAXY_DEFAULT_ADMIN_PASSWORD

run-data-managers --config data-managers/metaphlan2_download/db_v20.yaml -g ${GALAXY_URL} -u $GALAXY_DEFAULT_ADMIN_USER -p $GALAXY_DEFAULT_ADMIN_PASSWORD

ls -l ${DATA_MANAGER_DATA_PATH}

