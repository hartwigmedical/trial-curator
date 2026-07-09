#!/usr/bin/env bash
#
# Publish docs/v2_workflow_diagram.html to its Claude Artifact, OVERWRITING the existing one.
#
# The artifact URL below is the single canonical target: passing it as `url=` makes the publish
# overwrite in place (keeping the shared link stable) instead of minting a new URL. Run this after
# editing the diagram so the hosted page always matches the repo:
#
#   scripts/publish_diagram_artifact.sh
#
# Requires the `claude` CLI (Claude Code), authenticated as the artifact's owner. It drives a
# non-interactive (`-p`) Claude turn that calls the built-in Artifact tool.
#
# NOTE: if the Artifact tool isn't available in headless mode on your setup, the fallback is to
# ask Claude in a normal session: "re-publish docs/v2_workflow_diagram.html, overwriting <URL>".

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIAGRAM="${REPO_ROOT}/docs/v2_workflow_diagram.html"

# The one artifact this diagram maps to. Overwriting it keeps the shareable link stable.
ARTIFACT_URL="https://claude.ai/code/artifact/671df104-6474-4c32-b78c-45f4b65d063f"

[[ -f "${DIAGRAM}" ]] || { echo "diagram not found: ${DIAGRAM}" >&2; exit 1; }
command -v claude >/dev/null 2>&1 || { echo "the 'claude' CLI is not on PATH" >&2; exit 1; }

echo "Publishing ${DIAGRAM##*/} -> ${ARTIFACT_URL} (overwrite in place)" >&2

claude -p "Publish the file ${DIAGRAM} as a Claude Artifact. Update the EXISTING artifact by passing \
url=${ARTIFACT_URL} so it OVERWRITES in place and does NOT mint a new URL. Use favicon 🧬. \
Then print only the resulting artifact URL." \
  --allowedTools "Artifact Read"
