# trial-curator

AI magic for curating trial protocols automatically!

## Using Under Docker

The ACTIN project has added a `Dockerfile` and wrapper script for easily running the ACTIN curation module against Vertex either
in GCP somewhere or on a user's machine:

* To obtain a Docker image either:
    * Issue `docker build .` in the root directory of the project and run `docker images`, noting the "hash" of the `docker` image
      just built.
    * OR run `docker pull europe-west4-docker.pkg.dev/actin-build/build-registry-docker/actin-trial-curator:x` where `x` is the tag
      of the version for an existing container you want.
* Run `actin_curator.sh` once with the image hash or tag and the directory to use for input/output to/from the
  container. Let's use `~/hmf/tmp/actin_data` for the rest of the steps. The needed directories under `~/hmf/tmp/actin_data` will be
  created.
* Store your input text in "new_trial_paste_form" Document in Google Drive and download as TXT file, OR store your input text in
  `~/hmf/tmp/actin_data/input/criteria.txt` directly.
* Recall your `actin_curator.sh` command line and run it again (with only the image hash or tag as input), this time it should run for a
  couple of minutes and then write output from the LLM to `~/hmf/tmp/actin_data/output`.

### Troubleshooting

* Auth-related issues could be related to not having application-default credentials. Try issuing `gcloud auth application-default
  login` then re-running the wrapper script.
* The above notwithstanding, auto-detection of your `gcloud` configuration directory could fail, if you think this is happening
  try exporting `CLOUDSDK_CONFIG` in the environment that you're running the wrapper script from.
* Despite having sensible defaults something could need to be adjusted in the Google project or location settings used by the
  Docker container. You could modify the wrapper with `-e` commands after consulting the `Dockerfile`.
* You could also simply be lacking permissions.

This setup has not been thoroughly tested so ask for help if it is not working for you!
