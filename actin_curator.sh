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

[[ $# -eq 2 || $# -eq 3 ]] || die "USAGE: $0 [Docker image id] [local directory to contain input/output] [OPTIONAL OpenAI key]"
in_dir="$2/input"
for dir in "$in_dir" "$2/output"; do
  mkdir -p $dir || die "Could not create/verify [$dir]"
done
input="$in_dir/criteria.txt"
[[ -f $input ]] || die "Cannot find input file [$input]"

BASE_CMD="docker run --mount type=bind,src=${2},dst=/actin_data"

#gc_home="${CLOUDSDK_CONFIG:-$(gcloud info --format='value(config.paths.global_config_dir)')}"
#[[ -n $gc_home ]] || die "Could not determine gcloud config directory! Export CLOUDSDK_CONFIG maybe."
#echo "Using gcloud configured in [$gc_home]"
#
#echo "Running using Vertex"
#$BASE_CMD --mount "type=bind,src=${gc_home},dst=/root/.config/gcloud,ro" $1

if [[ $# -eq 3 ]]; then
  echo "Running using OpenAI"
  $BASE_CMD -e LLM_PROVIDER=OpenAI -e OPENAI_API_KEY="$3" $1
else
  echo "Not running with OpenAI as no API key was provided"
fi
