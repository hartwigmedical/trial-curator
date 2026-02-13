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

key="$(gcloud secrets versions access latest --secret=actin-trial-curator-key --project=actin-research)"
[[ $? -ne 0 ]] && die "Could not access OpenAI secret key. Exiting."

BASE_CMD="docker run --mount type=bind,src=${local_trial_curator_dir},dst=/actin_data"
info "Running trial-curator for ACTIN using OpenAI"
$BASE_CMD -e LLM_PROVIDER=OpenAI -e OPENAI_API_KEY=$key $docker_image_id

if [ $? -eq 0 ]; then
  info "Successfully ran Docker container!"
else
  die "Docker container run did not succeed. Exiting."
fi

info "Reformat output for inspection and ACTIN use"
title=$(awk -F'Trial Title:' '/Trial Title:/ {gsub(/^[ \t]+/, "", $2); print $2}' $input_file)
trial_id=$(awk -F'Trial ID:' '/Trial ID:/ {gsub(/\r/,""); gsub(/^[ \t]+/, "", $2); print $2}' $input_file)

complete_out_file="${out_dir}/${trial_id}_complete.json"
reformatted_out_file="${out_dir}/${trial_id}_complete.reformatted.tsv"
echo -e "trial_id\t${trial_id}" > ${reformatted_out_file}
echo -e "title\t${title}" >> ${reformatted_out_file}

jq -r '.[] | ["criterion", .original_input_rule_id, .original_input_rule, .curations[].actin_rule_reformat] | @tsv' ${complete_out_file} >> ${reformatted_out_file}

info "Everything done!"