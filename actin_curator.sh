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

[[ $# -eq 1 ]] || die "USAGE: $0 [Docker image id]"
docker_image_id=$1

local_trial_curator_dir="$HOME/hmf/tmp/actin_data"
in_dir="$local_trial_curator_dir/input"

for dir in "$in_dir" "$local_trial_curator_dir/output"; do
  mkdir -p $dir || die "Could not create or verify [$dir]"
done

trial_input_file="$HOME/Downloads/new_trial_paste_form.txt"
if [[ -f $trial_input_file ]]; then
  echo "Moving [$trial_input_file] to [$in_dir]..."
  mv $trial_input_file $in_dir/criteria.txt
  echo "Done!"
else
  echo "File [$trial_input_file] did not exist. Directly using input file in $in_dir (if any)."
fi

input="$in_dir/criteria.txt"
[[ -f $input ]] || die "Cannot find input file [$input]"

openai_key_file="$HOME/hmf/hartwig_openai_key/key.txt"
echo "Extracting OpenAi key from [$openai_key_file]. Be sure to use an ACTIN ORGANIZATIONAL OpenAi key!"
[[ -f $openai_key_file ]] || die "Cannot find openai key [openai_key_file]. Be sure to add an ACTIN ORGANIZATIONAL OpenAi key!"
openai_key=$(cat $openai_key_file)

BASE_CMD="docker run --mount type=bind,src=${local_trial_curator_dir},dst=/actin_data"
echo "Running trial-curator for ACTIN using OpenAI"
$BASE_CMD -e LLM_PROVIDER=OpenAI -e OPENAI_API_KEY=$openai_key $docker_image_id