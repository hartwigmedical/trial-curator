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

[[ $# -eq 2 ]] || die "USAGE: $0 [Docker image id] [local directory to contain input/output]"
in_dir="$2/input"
for dir in "$in_dir" "$2/output"; do
  mkdir -p $dir || die "Could not create/verify [$dir]"
done
input="$in_dir/criteria.txt"
[[ -f $input ]] || die "Cannot find input file [$input]"

BASE_CMD="docker run --mount type=bind,src=${2},dst=/actin_data"

echo "Running using OpenAI"
key="$(gcloud secrets versions access latest --secret=actin-trial-curator-key --project=actin-research)"
[[ $? -ne 0 ]] && echo "Could not access OpenAI secret key" && exit 1
$BASE_CMD -e LLM_PROVIDER=OpenAI -e OPENAI_API_KEY="$key" $1
