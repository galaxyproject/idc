#!/usr/bin/env bash
set -euo pipefail

# Set this variable to 'true' to publish on successful installation
: ${PUBLISH:=false}

BUILD_GALAXY_URL="http://idc-build"
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

EPHEMERIS="git+https://github.com/mvdbeek/ephemeris.git@data_manager_mode#egg_name=ephemeris"
BIOBLEND="git+https://github.com/mvdbeek/bioblend.git@idc_data_manager_runs#egg_name=bioblend"
GALAXY_MAINTENANCE_SCRIPTS="git+https://github.com/mvdbeek/galaxy-maintenance-scripts.git@avoid_galaxy_app#egg_name=galaxy-maintenance-scripts"

# Should be set by Jenkins, so the default here is for development
: ${GIT_COMMIT:=$(git rev-parse HEAD)}

# Set to true to perform everything on the Jenkins worker and copy results to the Stratum 0 for publish, instead of
# performing everything directly on the Stratum 0. Requires preinstallation/preconfiguration of CVMFS and for
# fuse-overlayfs to be installed on Jenkins workers.
USE_LOCAL_OVERLAYFS=true

#
# Development/debug options
#

#
# Ensure that everything is defined for set -u
#

TOOL_YAMLS=()
REPO_USER=
REPO_STRATUM0=
SSH_MASTER_SOCKET=
WORKDIR=
USER_UID="$(id -u)"
USER_GID="$(id -g)"
OVERLAYFS_UPPER=
OVERLAYFS_LOWER=
OVERLAYFS_WORK=
OVERLAYFS_MOUNT=

SSH_MASTER_UP=false
CVMFS_TRANSACTION_UP=false
IMPORT_CONTAINER_UP=false
LOCAL_CVMFS_MOUNTED=false
LOCAL_OVERLAYFS_MOUNTED=false
BUILD_GALAXY_UP=false


function trap_handler() {
    { set +x; } 2>/dev/null
    # return to original dir
    while popd; do :; done || true
    $IMPORT_CONTAINER_UP && stop_import_container
    clean_preconfigured_container
    $LOCAL_CVMFS_MOUNTED && unmount_overlay
    # $LOCAL_OVERLAYFS_MOUNTED does not need to be checked here since if it's true, $LOCAL_CVMFS_MOUNTED must be true
    $CVMFS_TRANSACTION_UP && abort_transaction
    $BUILD_GALAXY_UP && stop_build_galaxy
    clean_workspace
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
        log_exec scp -o "ControlPath=$SSH_MASTER_SOCKET" "$file" "${REPO_USER}@${REPO_STRATUM0}:${WORKDIR}/${file##*/}"
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
    $PUBLISH && log_debug "Changes will be published" || log_debug "Test installation, changes will be discarded"
}


function load_repo_configs() {
    log 'Loading repository configs'
    . ./.ci/repos.conf
}


function detect_changes() {
#    log 'Detecting changes to genome files...'
#    log_exec git remote set-branches --add origin "$MAIN_BRANCH"
#    log_exec git fetch origin
#    COMMIT_RANGE="origin/${MAIN_BRANCH}..."
#
#    log 'Change detection limited to directories:'
#    for d in "${!REPOS[@]}"; do
#        echo "${d}/"
#    done
#
#    REPO= ;
#    while read op path; do
#        if [ -n "$REPO" -a "$REPO" != "${path%%/*}" ]; then
#            log_exit_error "Changes to data in multiple repos found: ${REPO} != ${path%%/*}"
#        elif [ -z "$REPO" ]; then
#            REPO="${path%%/*}"
#        fi
#        case "$op" in
#            A|M)
#                echo "$op $path"
#                ;;
#        esac
#    done < <(git diff --color=never --name-status "$COMMIT_RANGE" -- $(for d in "${!REPOS[@]}"; do echo "${d}/"; done))

    # FIXME:
    REPO=sandbox

    log 'Change detection results:'
    declare -p REPO

    log "Getting repo for: ${REPO}"
    # set -u will force exit here if $TOOLSET is invalid
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
    log "Setting up Ephemeris"
    log_exec python3 -m venv ephemeris
    . ./ephemeris/bin/activate
    log_exec pip install --upgrade pip wheel
    log_exec pip install --index-url https://wheels.galaxyproject.org/simple/ \
        --extra-index-url https://pypi.org/simple/ "${BIOBLEND:=bioblend}" "${EPHEMERIS:=ephemeris}"
}


function verify_cvmfs_revision() {
    log "Verifying that CVMFS Client and Stratum 0 are in sync"
    local cvmfs_io_sock="${WORKSPACE}/${BUILD_NUMBER}/cvmfs-cache/${REPO}/cvmfs_io.${REPO}"
    local stratum0_published_url="http://${REPO_STRATUM0}/cvmfs/${REPO}/.cvmfspublished"
    local client_rev=$(cvmfs_talk -p "$cvmfs_io_sock" revision)
    local stratum0_rev=$(curl "$stratum0_published_url" | awk -F '^--$' '{print $1} NF>1{exit}' | grep '^S' | sed 's/^S//')
    if [ -z "$client_rev" ]; then
        log_exit_error "Failed to detect client revision"
    elif [ -z "$stratum0_rev" ]; then
        log_exit_error "Failed to detect Stratum 0 revision"
    elif [ "$client_rev" -ne "$stratum0_rev" ]; then
        log_exit_error "Client revision '${client_rev}' does not match Stratum 0 revision '${stratum0_rev}'"
    fi

    log "${REPO} is revision ${client_rev}"
}


function mount_overlay() {
    log "Mounting OverlayFS/CVMFS"
    log_debug "\$JOB_NAME: ${JOB_NAME}, \$WORKSPACE: ${WORKSPACE}, \$BUILD_NUMBER: ${BUILD_NUMBER}"
    log_exec mkdir -p "$OVERLAYFS_LOWER" "$OVERLAYFS_UPPER" "$OVERLAYFS_WORK" "$OVERLAYFS_MOUNT" "$CVMFS_CACHE"
    log_exec cvmfs2 -o config=.ci/cvmfs-fuse.conf,allow_root "$REPO" "$OVERLAYFS_LOWER"
    verify_cvmfs_revision
    LOCAL_CVMFS_MOUNTED=true
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
    $USE_LOCAL_OVERLAYFS || port_forward_flag="-L 127.0.0.1:${LOCAL_PORT}:127.0.0.1:${REMOTE_PORT}"
    log_exec ssh -S "$SSH_MASTER_SOCKET" -M ${port_forward_flag:-} -Nfn -l "$REPO_USER" "$REPO_STRATUM0"
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
    exec_on "cvmfs_server publish -a 'idc-${GIT_COMMIT:0:7}' -m 'Automated data installation for commit ${GIT_COMMIT}' ${REPO}"
    CVMFS_TRANSACTION_UP=false
}


function prep_for_galaxy_run() {
    # Sets globals $WORKDIR
    log "Copying configs to Stratum 0"
    WORKDIR=$(exec_on mktemp -d -t idc.work.XXXXXX)
    if $IMPORT_DOCKER_IMAGE_PULL; then
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
    log "Installing Data Managers"
    log_exec shed-tools install -t data_managers_tools.yml -g "$BUILD_GALAXY_URL" -u idc -p "$IDC_USER_PASS"
}


function run_data_managers() {
    # FIXME: this will be generated
    local dm_id='data_manager_fetch_genome_all_fasta_dbkey'
    local build_id='dm6'
    log "Running Data Managers"
    log_exec curl -L -o fetch-dm6.yml https://gist.github.com/natefoo/7c8d27ab2460ea11426c724bbafff011/raw/c99029b65fec45b51b7ffc35afbe842caaef4b3b/fetch-dm6.yml
    log_exec run-data-managers --config fetch-dm6.yml -g "$BUILD_GALAXY_URL" -u idc -p "$IDC_USER_PASS" --data-manager-mode bundle --history-name "${build_id}.${dm_id}"
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
    log "Stopping and committing preconfigured container on Stratum 0"
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


function run_import_container() {
    run_container_for_preconfigure
    log "Installing importer scripts"
    exec_on docker exec "$PRECONFIGURE_CONTAINER_NAME" yum install -y python39 git
    exec_on docker exec "$PRECONFIGURE_CONTAINER_NAME" pip3 install --upgrade pip wheel setuptools
    exec_on docker exec "$PRECONFIGURE_CONTAINER_NAME" /usr/local/bin/pip install "$GALAXY_MAINTENANCE_SCRIPTS"
    commit_preconfigured_container

    # update tool_data_table_conf.xml from repo
    copy_to config/tool_data_table_conf.xml
    exec_on diff -q "${WORKDIR}/tool_data_table_conf.xml" "/cvmfs/${REPO}/config/tool_data_table_conf.xml" || exec_on cp "${WORKDIR}/tool_data_table_conf.xml" "${OVERLAYFS_MOUNT}/config/tool_data_table_conf.xml"

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
    # FIXME
    local dm_id='data_manager_fetch_genome_all_fasta_dbkey'
    local build_id='dm6'
    local bundle_uri="$(python3 ./.ci/get-bundle-url.py --galaxy-url "$BUILD_GALAXY_URL" --history-name "${build_id}.${dm_id}")"
    log_debug "bundle URI is: $bundle_uri"
    log "Importing data bundles"
    exec_on docker exec "$CONTAINER_NAME" mkdir -p "/cvmfs/${REPO}/data"
    exec_on docker exec "$CONTAINER_NAME" /usr/local/bin/galaxy-import-data-bundle --tool-data-path "/cvmfs/${REPO}/data" --data-table-config-path "/cvmfs/${REPO}/config/tool_data_table_conf.xml" "$bundle_uri"
    deactivate
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
    log "Checking for changes to repo"
    show_paths
    # NOTE: this assumes local mode
    for config in ${OVERLAYFS_UPPER}/config/*; do
        lower="${OVERLAYFS_LOWER}/${config##*/}"
        [ -f "$lower" ] || lower=/dev/null
        diff -q "$lower" "$config" || diff -u --color=always "$lower" "$config"
    done
}


function clean_workspace() {
    log_exec rm -rf "${WORKSPACE}/${BUILD_NUMBER}"
}


function post_install() {
    log "Running post-installation tasks"
    exec_on "find '$OVERLAYFS_UPPER' -perm -u+r -not -perm -o+r -not -type l -print0 | xargs -0 --no-run-if-empty chmod go+r"
    exec_on "find '$OVERLAYFS_UPPER' -perm -u+rx -not -perm -o+rx -not -type l -print0 | xargs -0 --no-run-if-empty chmod go+rx"
    [ -n "${WORKDIR:-}" ] && exec_on rm -rf "$WORKDIR"
}


function copy_upper_to_stratum0() {
    log "Copying changes to Stratum 0"
    set -x
    rsync -ah -e "ssh -o ControlPath=${SSH_MASTER_SOCKET}" --stats "${OVERLAYFS_UPPER}/" "${REPO_USER}@${REPO_STRATUM0}:/cvmfs/${REPO}"
    { rc=$?; set +x; } 2>/dev/null
    return $rc
}


function do_install_local() {
    mount_overlay
    run_import_container
    import_tool_data_bundles
    check_for_repo_changes
    stop_import_container
    clean_preconfigured_container
    post_install
    if $PUBLISH; then
        start_ssh_control
        begin_transaction 600
        copy_upper_to_stratum0
        publish_transaction
        stop_ssh_control
    fi
    unmount_overlay
}


function main() {
    check_bot_command
    load_repo_configs
    detect_changes
    set_repo_vars
    prep_for_galaxy_run
    run_build_galaxy
    setup_ephemeris
    install_data_managers
    run_data_managers
    if $USE_LOCAL_OVERLAYFS; then
        do_install_local
    else
        log_exit_error "Remote mode was not ported from usegalaxy-tools"
    fi
    stop_build_galaxy
    clean_workspace
    return 0
}


main
