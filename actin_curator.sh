#!/usr/bin/env bash

source message_functions || exit 1

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
out_dir="$local_trial_curator_dir/output"

for dir in "$in_dir" "$out_dir"; do
  mkdir -p $dir || die "Could not create or verify [$dir]"
done

trial_input_file="$HOME/Downloads/new_trial_paste_form.txt"
input_file="${in_dir}/criteria.txt"

if [[ -f $trial_input_file ]]; then
  info "Moving [$trial_input_file] to [$input_file]..."
  mv $trial_input_file $input_file
  info "Done!"
elif [[ -f $input_file ]]; then
  info "File [$trial_input_file] did not exist. Directly using input file [$input_file] to run trial-curator."
else
  die "Cannot find input file: [$trial_input_file] or [$input_file] both do not exist."
fi

openai_key_file="$HOME/hmf/hartwig_openai_key/key.txt"
info "Extracting OpenAi key from [$openai_key_file]. Be sure to use an ACTIN ORGANIZATIONAL OpenAi key!"
[[ -f $openai_key_file ]] || die "Cannot find openai key [$openai_key_file]. Be sure to add an ACTIN ORGANIZATIONAL OpenAi key!"
openai_key=$(cat $openai_key_file)

BASE_CMD="docker run --mount type=bind,src=${local_trial_curator_dir},dst=/actin_data"
info "Running trial-curator for ACTIN using OpenAI"
$BASE_CMD -e LLM_PROVIDER=OpenAI -e OPENAI_API_KEY=$openai_key $docker_image_id

if [ $? -eq 0 ]; then
  info "Successfully ran Docker container!"
else
  die "Docker container run did not succeed. Exiting."
fi

info "Reformat trial-curator output for inspection and ACTIN use"
jq -r '.[] | [.input_rule, .actin_rule_reformat] | @tsv' ${out_dir}/trial_curator_complete.out.OpenAI > ${out_dir}/trial_curator_complete.reformatted.tsv
info "Everything done!"