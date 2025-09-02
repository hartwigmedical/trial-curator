#!/usr/bin/env bash

set -o pipefail

exit_handler() {
  exit_code=$?
  [[ $exit_code -ne 0 ]] && echo "ERROR: Non-zero exit code [$exit_code] from previous command"
  exit $exit_code  
}

die() {
  echo "$@"
  exit 1
}

trap exit_handler EXIT

[[ $# -eq 2 ]] || die "Provide the Docker image id and the local directory which will contain input/output"
in_dir="$2/input"
for dir in "$in_dir" "$2/output"; do
  mkdir -p $dir || die "Could not create/verify [$dir]"
done
input="$in_dir/criteria.txt"
[[ -f $input ]] || die "Cannot find input file [$input]"

gc_home="${CLOUDSDK_CONFIG:-$(gcloud info --format='value(config.paths.global_config_dir)')}"
[[ -n $gc_home ]] || die "Could not determine gcloud config directory! Export CLOUDSDK_CONFIG maybe."
echo "Using gcloud configured in [$gc_home]"
docker run --mount "type=bind,src=${2},dst=/actin_data" --mount "type=bind,src=${gc_home},dst=/root/.config/gcloud,ro" $1 
