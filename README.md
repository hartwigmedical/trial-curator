# trial-curator

AI magic for curating trial protocols automatically!

## Using Under Docker

The ACTIN project has added a `Dockerfile` and wrapper script for easily creating an image to run the ACTIN curation module
in a container. For this approach the image can either be built locally:

  * Issue `docker build .` in the root directory of the project and run `docker images`, noting the "hash" of the `docker` image
    just built.

OR pulled from the registry:

  * Run `docker pull europe-west4-docker.pkg.dev/actin-build/build-registry-docker/actin-trial-curator:x` where `x` is the tag
    of the version for an existing container you want. If you don't know which version, browse the available images via the
    (console)[https://console.cloud.google.com/artifacts/docker/actin-build/europe-west4/build-registry-docker/actin-trial-curator?project=actin-build].

Once you have the image you can run using the script provided. You need to pass the image hash, to get that run `docker images`
and look at the third column. Then:

* Run `actin_curator.sh` once with the image hash or tag and any directory you would like to use for input/output to/from the
  container.  Let's use `/tmp/actin_data` for the rest of the steps. The needed directory structure under `/tmp/actin_data` will
  be created.
* Copy your inclusion criteria that should be interpreted into `/tmp/actin_data/input/criteria.txt`.
* Recall your `actin_curator.sh` command line and run it again, this time it should run for a couple of minutes and then write
  output from the LLM in `/tmp/actin_data/output`.

### Troubleshooting

* Auth-related issues could be related to not having application-default credentials. Try issuing `gcloud auth application-default
  login` then re-running the wrapper script.
* The above notwithstanding, auto-detection of your `gcloud` configuration directory could fail, if you think this is happening
  try exporting `CLOUDSDK_CONFIG` in the environment that you're running the wrapper script from.
* Despite having sensible defaults something could need to be adjusted in the Google project or location settings used by the
  Docker container. You could modify the wrapper with `-e` commands after consulting the `Dockerfile`.
* You could also simply be lacking permissions.

This setup has not been thoroughly tested so ask for help if it is not working for you!
