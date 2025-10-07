FROM python:3.13.5

ADD actin_curator /trial-curator/actin_curator
ADD trialcurator /trial-curator/trialcurator
ADD utils /trial-curator/utils
RUN pip install --no-cache-dir -r /trial-curator/actin_curator/requirements.txt

ENV PYTHONPATH=/trial-curator
ENV GOOGLE_GENAI_USE_VERTEXAI=true
ENV GOOGLE_CLOUD_PROJECT="actin-shared"
ENV GOOGLE_CLOUD_LOCATION="europe-west4"
ENV LLM_PROVIDER="Google"
ENV OPENAI_API_KEY="dummy"

ENTRYPOINT /usr/local/bin/python -m actin_curator.actin_curator --llm_provider $LLM_PROVIDER \
  --actin_filepath /trial-curator/actin_curator/data/ACTIN_rules/ACTIN_rules_w_categories_13062025.csv \
  --output_file_complete /actin_data/output/trial_curator_complete.out.$LLM_PROVIDER \
  --input_text_file /actin_data/input/criteria.txt
