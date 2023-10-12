#!/usr/bin/env bash
set -euo pipefail

# Set this variable to 'true' to publish on successful installation
: ${PUBLISH:=false}

BUILD_GALAXY_URL="http://idc-build"
PUBLISH_GALAXY_URL="https://usegalaxy.org"
SSH_MASTER_SOCKET_DIR="${HOME}/.cache/idc"
MAIN_BRANCH='main'

# Set to 'centos:...' or 'rockylinux:...' and set GALAXY_GIT_* or GALAXY_SERVER_DIR below to use a clone
IMPORT_DOCKER_IMAGE='rockylinux:8'
# Disable if using a locally built image e.g. for debugging
IMPORT_DOCKER_IMAGE_PULL=true

#GALAXY_TEMPLATE_DB_URL='https://raw.githubusercontent.com/davebx/galaxyproject-sqlite/master/20.01.sqlite'
#GALAXY_TEMPLATE_DB="${GALAXY_TEMPLATE_DB_URL##*/}"
# Unset to use create_db.py, which is fast now that it doesn't migrate new DBs
GALAXY_TEMPLATE_DB_URL=
GALAXY_TEMPLATE_DB='galaxy.sqlite'

EPHEMERIS="git+https://github.com/jmchilton/ephemeris.git@idc_2#egg_name=ephemeris"
BIOBLEND="git+https://github.com/mvdbeek/bioblend.git@idc_data_manager_runs#egg_name=bioblend"
GALAXY_MAINTENANCE_SCRIPTS="git+https://github.com/mvdbeek/galaxy-maintenance-scripts.git@avoid_galaxy_app#egg_name=galaxy-maintenance-scripts"

# Should be set by Jenkins, so the default here is for development
: ${GIT_COMMIT:=$(git rev-parse HEAD)}

# Set to true to perform everything on the Jenkins worker and copy results to the Stratum 0 for publish, instead of
# performing everything directly on the Stratum 0. Requires preinstallation/preconfiguration of CVMFS and for
# fuse-overlayfs to be installed on Jenkins workers.
USE_LOCAL_OVERLAYFS=false

# Set to true to run the importer in a docker container
USE_DOCKER="$USE_LOCAL_OVERLAYFS"

REMOTE_PYTHON=/opt/rh/rh-python38/root/usr/bin/python3
REMOTE_WORKDIR_PARENT=/srv/idc

# $EPHEMERIS_API_KEY and $IDC_VAULT_PASS should be set in the environment

#
# Development/debug options
#

#
# Ensure that everything is defined for set -u
#

DM_STAGE=0
TOOL_YAMLS=()
REPO_USER=
REPO_STRATUM0=
SSH_MASTER_SOCKET=
WORKDIR=
REMOTE_WORKDIR=
USER_UID="$(id -u)"
USER_GID="$(id -g)"
OVERLAYFS_UPPER=
OVERLAYFS_LOWER=
OVERLAYFS_WORK=
OVERLAYFS_MOUNT=
EPHEMERIS_BIN=
GALAXY_MAINTENANCE_SCRIPTS_BIN=

SSH_MASTER_UP=false
CVMFS_TRANSACTION_UP=false
IMPORT_CONTAINER_UP=false
LOCAL_CVMFS_MOUNTED=false
LOCAL_OVERLAYFS_MOUNTED=false
BUILD_GALAXY_UP=false


function trap_handler() {
    { set +x; } 2>/dev/null
    # return to original dir
    while popd 2>/dev/null; do :; done || true
    $IMPORT_CONTAINER_UP && stop_import_container
    clean_preconfigured_container
    $LOCAL_CVMFS_MOUNTED && unmount_overlay
    # $LOCAL_OVERLAYFS_MOUNTED does not need to be checked here since if it's true, $LOCAL_CVMFS_MOUNTED must be true
    $CVMFS_TRANSACTION_UP && abort_transaction
    $BUILD_GALAXY_UP && stop_build_galaxy
    clean_workspace
    [ -n "$WORKSPACE" ] && log_exec rm -rf "$WORKSPACE"
    $SSH_MASTER_UP && [ -n "$REMOTE_WORKDIR" ] && exec_on rm -rf "$REMOTE_WORKDIR"
    $SSH_MASTER_UP && stop_ssh_control
    return 0
}
trap "trap_handler" SIGTERM SIGINT ERR EXIT


function log() {
    [ -t 0 ] && echo -e '\033[1;32m#' "$@" '\033[0m' || echo '#' "$@"
}


function log_error() {
    [ -t 0 ] && echo -e '\033[0;31mERROR:' "$@" '\033[0m' || echo 'ERROR:' "$@"
}


function log_debug() {
    echo "####" "$@"
}


function log_exec() {
    local rc
    if $USE_LOCAL_OVERLAYFS && ! $SSH_MASTER_UP; then
        set -x
        eval "$@"
    else
        set -x
        "$@"
    fi
    { rc=$?; set +x; } 2>/dev/null
    return $rc
}


function log_exit_error() {
    log_error "$@"
    exit 1
}


function log_exit() {
    echo "$@"
    exit 0
}


function exec_on() {
    if $USE_LOCAL_OVERLAYFS && ! $SSH_MASTER_UP; then
        log_exec "$@"
    else
        log_exec ssh -S "$SSH_MASTER_SOCKET" -l "$REPO_USER" "$REPO_STRATUM0" -- "$@"
    fi
}


function copy_to() {
    local file="$1"
    if $USE_LOCAL_OVERLAYFS && ! $SSH_MASTER_UP; then
        log_exec cp "$file" "${WORKDIR}/${file##*}"
    else
        log_exec scp -o "ControlPath=$SSH_MASTER_SOCKET" "$file" "${REPO_USER}@${REPO_STRATUM0}:${REMOTE_WORKDIR}/${file##*/}"
    fi
}


function check_bot_command() {
    log 'Checking for Github PR Bot commands'
    log_debug "Value of \$ghprbCommentBody is: ${ghprbCommentBody:-UNSET}"
    case "${ghprbCommentBody:-UNSET}" in
        "@galaxybot deploy"*)
            PUBLISH=true
            ;;
    esac
    if $PUBLISH; then
        log "Publish requested; running build and import"
    else
        log "Publish not requested, exiting"
        exit 0
    fi
}


function load_repo_configs() {
    log 'Loading repository configs'
    . ./.ci/repos.conf
}


function detect_changes() {
    REPO=idc

    log "Getting repo for: ${REPO}"
    REPO="${REPOS[$REPO]}"
    declare -p REPO
}


function set_repo_vars() {
    REPO_USER="${REPO_USERS[$REPO]}"
    REPO_STRATUM0="${REPO_STRATUM0S[$REPO]}"
    CONTAINER_NAME="idc-${REPO_USER}-${BUILD_NUMBER}"
    if $USE_LOCAL_OVERLAYFS; then
        OVERLAYFS_LOWER="${WORKSPACE}/${BUILD_NUMBER}/lower"
        OVERLAYFS_UPPER="${WORKSPACE}/${BUILD_NUMBER}/upper"
        OVERLAYFS_WORK="${WORKSPACE}/${BUILD_NUMBER}/work"
        OVERLAYFS_MOUNT="${WORKSPACE}/${BUILD_NUMBER}/mount"
        CVMFS_CACHE="${WORKSPACE}/${BUILD_NUMBER}/cvmfs-cache"
    else
        OVERLAYFS_UPPER="/var/spool/cvmfs/${REPO}/scratch/current"
        OVERLAYFS_LOWER="/var/spool/cvmfs/${REPO}/rdonly"
        OVERLAYFS_MOUNT="/cvmfs/${REPO}"
    fi
}


function setup_ansible() {
    log "Setting up Ansible"
    log_exec python3 -m venv ansible-venv
    . ./ansible-venv/bin/activate
    log_exec pip install --upgrade pip wheel
    pushd ansible
    log_exec pip install -r requirements.txt
    log_exec ansible-galaxy role install -p roles -r requirements.yaml
    log_exec ansible-galaxy collection install -p collections -r requirements.yaml
    popd
    deactivate
}


function setup_ephemeris() {
    # Sets global $EPHEMERIS_BIN
    EPHEMERIS_BIN="$(pwd)/ephemeris/bin"
    log "Setting up Ephemeris"
    log_exec python3 -m venv ephemeris
    log_exec "${EPHEMERIS_BIN}/pip" install --upgrade pip wheel
    log_exec "${EPHEMERIS_BIN}/pip" install --index-url https://wheels.galaxyproject.org/simple/ \
        --extra-index-url https://pypi.org/simple/ "${BIOBLEND:=bioblend}" "${EPHEMERIS:=ephemeris}"
}


function setup_remote_ephemeris() {
    # Sets global $EPHEMERIS_BIN
    EPHEMERIS_BIN="${REMOTE_WORKDIR}/ephemeris/bin"
    log "Setting up remote Ephemeris"
    exec_on "$REMOTE_PYTHON" -m venv "${REMOTE_WORKDIR}/ephemeris"
    exec_on "${EPHEMERIS_BIN}/pip" install --upgrade pip wheel
    # urllib3 v2.0 only supports OpenSSL 1.1.1+, currently the 'ssl' module is compiled with 'OpenSSL 1.0.2k-fips  26 Jan 2017'. See: https://github.com/urllib3/urllib3/issues/2168
    exec_on "${EPHEMERIS_BIN}/pip" install --index-url https://wheels.galaxyproject.org/simple/ \
        --extra-index-url https://pypi.org/simple/ "${BIOBLEND:=bioblend}" "${EPHEMERIS:=ephemeris}" "'urllib3<2'"
}


function setup_galaxy_maintenance_scripts() {
    # Sets global $GALAXY_MAINTENANCE_SCRIPTS
    local venv="${1:-.}/galaxy-maintenance-scripts"
    local python="${2:-python3}"
    GALAXY_MAINTENANCE_SCRIPTS_BIN="${venv}/bin"
    log "Setting up Galaxy Maintenance Scripts"
    exec_on "$python" -m venv "$venv"
    exec_on "${venv}/bin/pip" install --upgrade pip wheel
    exec_on "${venv}/bin/pip" install --index-url https://wheels.galaxyproject.org/simple/ \
        --extra-index-url https://pypi.org/simple/ "$GALAXY_MAINTENANCE_SCRIPTS" "'urllib3<2'"
}


function verify_cvmfs_revision() {
    log "Verifying that CVMFS Client and Stratum 0 are in sync"
    local cvmfs_io_sock="${WORKSPACE}/${BUILD_NUMBER}/cvmfs-cache/${REPO}/cvmfs_io.${REPO}"
    local stratum0_published_url="http://${REPO_STRATUM0}/cvmfs/${REPO}/.cvmfspublished"
    local client_rev=$(cvmfs_talk -p "$cvmfs_io_sock" revision)
    local stratum0_rev=$(curl -s "$stratum0_published_url" | awk -F '^--$' '{print $1} NF>1{exit}' | grep '^S' | sed 's/^S//')
    if [ -z "$client_rev" ]; then
        log_exit_error "Failed to detect client revision"
    elif [ -z "$stratum0_rev" ]; then
        log_exit_error "Failed to detect Stratum 0 revision"
    elif [ "$client_rev" -ne "$stratum0_rev" ]; then
        log_exit_error "Importer client revision '${client_rev}' does not match Stratum 0 revision '${stratum0_rev}'"
    fi

    log "${REPO} is revision ${client_rev}"
}


function mount_overlay() {
    log "Mounting OverlayFS/CVMFS"
    log_debug "\$JOB_NAME: ${JOB_NAME}, \$WORKSPACE: ${WORKSPACE}, \$BUILD_NUMBER: ${BUILD_NUMBER}"
    log_exec mkdir -p "$OVERLAYFS_LOWER" "$OVERLAYFS_UPPER" "$OVERLAYFS_WORK" "$OVERLAYFS_MOUNT" "$CVMFS_CACHE"
    log_exec cvmfs2 -o config=.ci/cvmfs-fuse.conf,allow_root "$REPO" "$OVERLAYFS_LOWER"
    LOCAL_CVMFS_MOUNTED=true
    verify_cvmfs_revision
    log_exec fuse-overlayfs \
        -o "lowerdir=${OVERLAYFS_LOWER},upperdir=${OVERLAYFS_UPPER},workdir=${OVERLAYFS_WORK},allow_root" \
        "$OVERLAYFS_MOUNT"
    LOCAL_OVERLAYFS_MOUNTED=true
}


function unmount_overlay() {
    log "Unmounting OverlayFS/CVMFS"
    if $LOCAL_OVERLAYFS_MOUNTED; then
        log_exec fusermount -u "$OVERLAYFS_MOUNT"
        LOCAL_OVERLAYFS_MOUNTED=false
    fi
    # DEBUG: what is holding this?
    log_exec fuser -v "$OVERLAYFS_LOWER" || true
    # Attempt to kill anything still accessing lower so unmount doesn't fail
    log_exec fuser -v -k "$OVERLAYFS_LOWER" || true
    log_exec fusermount -u "$OVERLAYFS_LOWER"
    LOCAL_CVMFS_MOUNTED=false
}


function start_ssh_control() {
    log "Starting SSH control connection to Stratum 0"
    SSH_MASTER_SOCKET="${SSH_MASTER_SOCKET_DIR}/ssh-tunnel-${REPO_USER}-${REPO_STRATUM0}.sock"
    log_exec mkdir -p "$SSH_MASTER_SOCKET_DIR"
    log_exec ssh -M -S "$SSH_MASTER_SOCKET" -Nfn -l "$REPO_USER" "$REPO_STRATUM0"
    USER_UID=$(exec_on id -u)
    USER_GID=$(exec_on id -g)
    SSH_MASTER_UP=true
}


function stop_ssh_control() {
    log "Stopping SSH control connection to Stratum 0"
    log_exec ssh -S "$SSH_MASTER_SOCKET" -O exit -l "$REPO_USER" "$REPO_STRATUM0"
    rm -f "$SSH_MASTER_SOCKET"
    SSH_MASTER_UP=false
}


function begin_transaction() {
    # $1 >= 0 number of seconds to retry opening transaction for
    local max_wait="${1:--1}"
    local start=$(date +%s)
    local elapsed='-1'
    local sleep='4'
    local max_sleep='60'
    log "Opening transaction on $REPO"
    while ! exec_on cvmfs_server transaction "$REPO"; do
        log "Failed to open CVMFS transaction on ${REPO}"
        if [ "$max_wait" -eq -1 ]; then
            log_exit_error 'Transaction open retry disabled, giving up!'
        elif [ "$elapsed" -ge "$max_wait" ]; then
            log_exit_error "Time waited (${elapsed}s) exceeds limit (${max_wait}s), giving up!"
        fi
        log "Will retry in ${sleep}s"
        sleep $sleep
        [ $sleep -ne $max_sleep ] && let sleep="${sleep}*2"
        [ $sleep -gt $max_sleep ] && sleep="$max_sleep"
        let elapsed="$(date +%s)-${start}"
    done
    CVMFS_TRANSACTION_UP=true
}


function abort_transaction() {
    log "Aborting transaction on $REPO"
    exec_on cvmfs_server abort -f "$REPO"
    CVMFS_TRANSACTION_UP=false
}


function publish_transaction() {
    log "Publishing transaction on $REPO"
    exec_on "cvmfs_server publish -a 'idc-${GIT_COMMIT:0:7}.${DM_STAGE}' -m 'Automated data installation for commit ${GIT_COMMIT}' ${REPO}"
    CVMFS_TRANSACTION_UP=false
}


function create_workdir() {
    # Sets global $WORKDIR
    log "Creating local workdir"
    WORKDIR=$(log_exec mktemp -d -t idc.work.XXXXXX)
}


function create_remote_workdir() {
    # Sets global $REMOTE_WORKDIR
    log "Creating remote workdir"
    REMOTE_WORKDIR=$(exec_on mktemp -d -p "$REMOTE_WORKDIR_PARENT" -t idc.work.XXXXXX)
}


function prep_docker_image() {
    if $USE_DOCKER && $IMPORT_DOCKER_IMAGE_PULL; then
        log "Fetching latest Galaxy image"
        exec_on docker pull "$IMPORT_DOCKER_IMAGE"
    fi
}


function run_build_galaxy() {
    setup_ansible
    log "Starting Build Galaxy"
    # This is set beforehand so that the teardown playbook will destroy the instance if launch fails partway through
    BUILD_GALAXY_UP=true
    . ./ansible-venv/bin/activate
    pushd ansible
    log_exec ansible-playbook playbook-launch.yaml
    popd
    deactivate
    wait_for_cvmfs_sync
}


function wait_for_cvmfs_sync() {
    # TODO merge with verify_cvmfs_revision() used by build side
    # TODO: could avoid the hardcoding by using ansible but the output is harder to process
    local stratum0_published_url="http://${REPO_STRATUM0}/cvmfs/${REPO}/.cvmfspublished"
    while true; do
        # ensure it's mounted
        ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -l rocky -i ~/.ssh/id_rsa_idc_jetstream2_cvmfs idc-build ls /cvmfs/${REPO} >/dev/null
        local client_rev=$(ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -l rocky -i ~/.ssh/id_rsa_idc_jetstream2_cvmfs idc-build sudo cvmfs_talk -i ${REPO} revision)
        local stratum0_rev=$(curl -s "$stratum0_published_url" | awk -F '^--$' '{print $1} NF>1{exit}' | grep '^S' | sed 's/^S//')
        if [ "$client_rev" -eq "$stratum0_rev" ]; then
            log "${REPO} is revision ${client_rev}"
            break
        else
            log_debug "Builder client revision '${client_rev}' does not match Stratum 0 revision '${stratum0_rev}'"
            sleep 60
        fi
    done
}


function wait_for_build_galaxy() {
    log "Waiting for Galaxy"
    log_exec "${EPHEMERIS_BIN}/galaxy-wait" -v -g "$BUILD_GALAXY_URL" --timeout 180 || {
        log_error "Timed out waiting for Galaxy"
        #exec_on journalctl -u galaxy-gunicorn
        #log_debug "response from ${IMPORT_GALAXY_URL}";
        curl -s "$BUILD_GALAXY_URL";
        log_exit_error "Terminating build due to previous errors"
    }
}


function stop_build_galaxy() {
    . ./ansible-venv/bin/activate
    log "Stopping Build Galaxy"
    pushd ansible
    log_exec ansible-playbook playbook-teardown.yaml
    BUILD_GALAXY_UP=false
    popd
    deactivate
}


function install_data_managers() {
    log "Generating Data Manager tool list"
    log_exec _idc-data-managers-to-tools
    log "Installing Data Managers"
    log_exec shed-tools install -t tools.yml -g "$BUILD_GALAXY_URL"
}


function generate_data_manager_tasks() {
    # returns false if there are no data managers to run
    log "Generating Data Manager tasks"
    log_exec "${EPHEMERIS_BIN}/_idc-split-data-manager-genomes" -g "$PUBLISH_GALAXY_URL" --tool-id-mode short
    compgen -G "data_manager_tasks/*/data_manager_*/run_data_managers.yaml" >/dev/null
}


function run_data_managers() {
    # TODO: eventually these will specify their stage somehow
    compgen -G "data_manager_tasks/*/data_manager_fetch_genome_dbkeys_all_fasta/run_data_managers.yaml" >/dev/null && {
        run_stage0_data_managers
    } || {
        compgen -G "data_manager_tasks/*/data_manager_*/run_data_managers.yaml" >/dev/null && {
            run_stage1_data_managers
        }
    }
}


function run_stage0_data_managers() {
    local dm_config a
    log "Running Stage 0 Data Managers"
    DM_STAGE=0
    pushd data_manager_tasks
    for dm_config in */data_manager_fetch_genome_dbkeys_all_fasta/run_data_managers.yaml; do
        readarray -td/ a <<<"$dm_config"
        run_data_manager "${a[0]}" "${a[1]}" "$dm_config"
    done
    popd
}


function run_stage1_data_managers() {
    local dm_config a record
    log "Running Stage 1 Data Managers"
    DM_STAGE=1
    pushd data_manager_tasks
    for dm_config in */*/run_data_managers.yaml; do
        readarray -td/ a <<<"$dm_config"
        # this should never be false since we run either/or stage 0 or stage 1 in the caller
        [ "${a[1]}" != 'data_manager_fetch_genome_dbkeys_all_fasta' ] || continue
        run_data_manager "${a[0]}" "${a[1]}" "$dm_config"
    done
    popd
}


function run_data_manager() {
    local build_id="$1"
    local dm_repo_id="$2"
    local dm_config="$3"
    log "Running Data Manager '$dm_repo_id' for build '$build_id'"
    log_exec "${EPHEMERIS_BIN}/run-data-managers" --config "$dm_config" -g "$BUILD_GALAXY_URL" --data-manager-mode bundle --history-name "idc-${build_id}-${dm_repo_id}"
}


function run_container_for_preconfigure() {
    # Sets globals $PRECONFIGURE_CONTAINER_NAME $PRECONFIGURED_IMAGE_NAME
    PRECONFIGURE_CONTAINER_NAME="${CONTAINER_NAME}-preconfigure"
    PRECONFIGURED_IMAGE_NAME="${PRECONFIGURE_CONTAINER_NAME}d"
    ORIGINAL_IMAGE_NAME="$IMPORT_DOCKER_IMAGE"
    log "Starting import container for preconfiguration"
    exec_on docker run -d --name="$PRECONFIGURE_CONTAINER_NAME" \
        -v "${WORKDIR}/:/work/" \
        "$IMPORT_DOCKER_IMAGE" sleep infinity
    IMPORT_CONTAINER_UP=true
}


function commit_preconfigured_container() {
    log "Stopping and committing preconfigured container"
    exec_on docker kill "$PRECONFIGURE_CONTAINER_NAME"
    IMPORT_CONTAINER_UP=false
    exec_on docker commit "$PRECONFIGURE_CONTAINER_NAME" "$PRECONFIGURED_IMAGE_NAME"
    IMPORT_DOCKER_IMAGE="$PRECONFIGURED_IMAGE_NAME"
}


function clean_preconfigured_container() {
    [ -n "${PRECONFIGURED_IMAGE_NAME:-}" ] || return 0
    exec_on docker kill "$PRECONFIGURE_CONTAINER_NAME" || true
    exec_on docker rm -v "$PRECONFIGURE_CONTAINER_NAME" || true
    exec_on docker rmi -f "$PRECONFIGURED_IMAGE_NAME" || true
}


function generate_import_tasks() {
    # returns false if there is no data manager to import
    log "Generating import tasks"
    copy_to genomes.yml
    copy_to data_managers.yml
    exec_on "${EPHEMERIS_BIN}/_idc-split-data-manager-genomes" --complete-check-cvmfs "--cvmfs-root=${OVERLAYFS_LOWER}" "--merged-genomes-path=${REMOTE_WORKDIR}/genomes.yml" "--data-managers-path=${REMOTE_WORKDIR}/data_managers.yml" "--split-genomes-path=${REMOTE_WORKDIR}/import_tasks"
    exec_on "compgen -G '${REMOTE_WORKDIR}/import_tasks/*/data_manager_*/run_data_managers.yaml'" >/dev/null
}


function run_import_container() {
    run_container_for_preconfigure
    log "Installing importer scripts"
    exec_on docker exec "$PRECONFIGURE_CONTAINER_NAME" yum install -y python39 git
    exec_on docker exec "$PRECONFIGURE_CONTAINER_NAME" pip3 install --upgrade pip wheel setuptools
    exec_on docker exec "$PRECONFIGURE_CONTAINER_NAME" /usr/local/bin/pip install "$GALAXY_MAINTENANCE_SCRIPTS"
    commit_preconfigured_container

    # update tool_data_table_conf.xml from repo
    copy_to config/tool_data_table_conf.xml
    exec_on diff -q "${REMOTE_WORKDIR}/tool_data_table_conf.xml" "/cvmfs/${REPO}/config/tool_data_table_conf.xml" || { exec_on mkdir -p "${OVERLAYFS_MOUNT}/config" && exec_on cp "${REMOTE_WORKDIR}/tool_data_table_conf.xml" "${OVERLAYFS_MOUNT}/config/tool_data_table_conf.xml"; }

    log "Starting importer container"
    exec_on docker run -d --user "${USER_UID}:${USER_GID}" --name="${CONTAINER_NAME}" \
        -v "${OVERLAYFS_MOUNT}:/cvmfs/${REPO}" \
        "$IMPORT_DOCKER_IMAGE" sleep infinity
    IMPORT_CONTAINER_UP=true
}


function stop_import_container() {
    log "Stopping importer container"
    # NOTE: docker rm -f exits 1 if the container does not exist
    exec_on docker stop "$CONTAINER_NAME" || true  # try graceful shutdown first
    exec_on docker kill "$CONTAINER_NAME" || true  # probably failed to start, don't prevent the rest of cleanup
    exec_on docker rm -v "$CONTAINER_NAME" || true
    IMPORT_CONTAINER_UP=false
}


function import_tool_data_bundles() {
    local dm_config j build_id dm_repo_id bundle_uri record_file
    copy_to .ci/get-bundle-url.py
    for dm_config in $(exec_on "compgen -G '${REMOTE_WORKDIR}/import_tasks/*/data_manager_*/run_data_managers.yaml'"); do
        IFS='/' read build_id dm_repo_id j <<< "${dm_config##${REMOTE_WORKDIR}/import_tasks/}"
        record_file="${REMOTE_WORKDIR}/import_tasks/${build_id}/${dm_repo_id}/bundle.txt"
        log "Importing bundle for Data Manager '$dm_repo_id' of '$build_id'"
        # API key is filtered from output by Jenkins
        local bundle_uri="$(exec_on ${EPHEMERIS_BIN}/python3 ${REMOTE_WORKDIR}/get-bundle-url.py --galaxy-url "$PUBLISH_GALAXY_URL" --history-name "idc-${build_id}-${dm_repo_id}" --record-file="$record_file" --galaxy-api-key="$EPHEMERIS_API_KEY")"
        [ -n "$bundle_uri" ] || log_exit_error "Could not determine bundle URI!"
        log_debug "bundle URI is: $bundle_uri"
        if $USE_DOCKER; then
            exec_on docker exec "$CONTAINER_NAME" mkdir -p "/cvmfs/${REPO}/data" "/cvmfs/${REPO}/record/${build_id}"
            exec_on docker exec "$CONTAINER_NAME" /usr/local/bin/galaxy-import-data-bundle --tool-data-path "/cvmfs/${REPO}/data" --data-table-config-path "/cvmfs/${REPO}/config/tool_data_table_conf.xml" "$bundle_uri"
            exec_on rsync -av "import_tasks/${build_id}/${dm_repo_id}" "${OVERLAYFS_MOUNT}/record/${build_id}"
        else
            exec_on mkdir -p "/cvmfs/${REPO}/data" "/cvmfs/${REPO}/record/${build_id}"
            exec_on "TMPDIR=${REMOTE_WORKDIR}" "${GALAXY_MAINTENANCE_SCRIPTS_BIN}/galaxy-import-data-bundle" --tool-data-path "/cvmfs/${REPO}/data" --data-table-config-path "/cvmfs/${REPO}/config/tool_data_table_conf.xml" "$bundle_uri"
            exec_on rsync -av "${REMOTE_WORKDIR}/import_tasks/${build_id}/${dm_repo_id}" "${OVERLAYFS_MOUNT}/record/${build_id}"
        fi
    done
}


function show_logs() {
    local lines=
    if [ -n "${1:-}" ]; then
        lines="--tail ${1:-}"
        log_debug "tail ${lines} of server log";
    else
        log_debug "contents of server log";
    fi
    exec_on docker logs $lines "$CONTAINER_NAME"
}


function show_paths() {
    log "contents of OverlayFS upper mount (will be published)"
    exec_on tree "$OVERLAYFS_UPPER"
}


function check_for_repo_changes() {
    local lower=
    local changes=false
    log "Checking for changes to repo"
    show_paths
    for config in $(exec_on "compgen -G '${OVERLAYFS_UPPER}/config/*'"); do
        exec_on test -f "$config" || continue
        lower="${OVERLAYFS_LOWER}/config/${config##*/}"
        exec_on test -f "$lower" || lower=/dev/null
        exec_on diff -q "$lower" "$config" || { changes=true; exec_on diff -u "$lower" "$config" || true; }
    done
    if ! $changes; then
        log_exit_error "Terminating build: expected changes to ${OVERLAYFS_UPPER}/config/* not found!"
    fi
}


function clean_workspace() {
    log_exec rm -rf "${WORKSPACE}/${BUILD_NUMBER}"
}


function post_install() {
    log "Running post-installation tasks"
    exec_on "find '$OVERLAYFS_UPPER' -perm -u+r -not -perm -o+r -not -type l -print0 | xargs -0 --no-run-if-empty chmod go+r"
    exec_on "find '$OVERLAYFS_UPPER' -perm -u+rx -not -perm -o+rx -not -type l -print0 | xargs -0 --no-run-if-empty chmod go+rx"
}


function copy_upper_to_stratum0() {
    log "Copying changes to Stratum 0"
    set -x
    rsync -ah -e "ssh -o ControlPath=${SSH_MASTER_SOCKET}" --stats "${OVERLAYFS_UPPER}/" "${REPO_USER}@${REPO_STRATUM0}:/cvmfs/${REPO}"
    { rc=$?; set +x; } 2>/dev/null
    return $rc
}


function do_import_local() {
    mount_overlay
    # TODO: we could probably replace the import container with whatever cvmfsexec does to fake a mount
    if generate_import_tasks; then
        create_workdir
        prep_for_galaxy_run
        run_import_container
        import_tool_data_bundles
        check_for_repo_changes
        stop_import_container
        clean_preconfigured_container
        post_install
    else
        log "Nothing to import"
        PUBLISH=false
    fi
    if $PUBLISH; then
        start_ssh_control
        begin_transaction 600
        copy_upper_to_stratum0
        publish_transaction
        stop_ssh_control
    fi
    unmount_overlay
}


function do_import_remote() {
    start_ssh_control
    create_remote_workdir
    setup_remote_ephemeris
    # from this point forward $EPHEMERIS_BIN refers to remote
    if generate_import_tasks; then
        setup_galaxy_maintenance_scripts "$WORKDIR" "$REMOTE_PYTHON"
        begin_transaction
        import_tool_data_bundles
        check_for_repo_changes
        post_install
    else
        log "Nothing to import"
        PUBLISH=false
    fi
    $PUBLISH && publish_transaction || abort_transaction
    stop_ssh_control
}


function main() {
    check_bot_command
    load_repo_configs
    detect_changes
    set_repo_vars
    setup_ephemeris
    if generate_data_manager_tasks; then
        run_build_galaxy
        wait_for_build_galaxy
        #install_data_managers
        run_data_managers
    else
        log "Nothing to build, will check for unimported data"
    fi
    if $USE_LOCAL_OVERLAYFS; then
        do_import_local
    else
        do_import_remote
    fi
    stop_build_galaxy
    clean_workspace
    return 0
}


main
