#!/usr/bin/env bash

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

REFGET_STORE_PATH="$1"
SEQCOLAPI_PORT="${2:-8100}"

if [[ -z "$REFGET_STORE_PATH" ]]; then
  echo "Error: No refgetstore directory specified." >&2
  echo "Usage: $0 <path_to_refgetstore> [port]" >&2
  exit 1
fi

if [ ! -d "$script_dir/refget_clone" ]; then
  git clone https://github.com/refgenie/refget.git $script_dir/refget_clone
fi

pushd .
cd $script_dir/refget_clone
REFGET_STORE_PATH=$REFGET_STORE_PATH python -m uvicorn seqcolapi.main:store_app --reload --port $SEQCOLAPI_PORT
popd