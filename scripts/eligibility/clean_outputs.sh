#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

DRY_RUN=0
ASSUME_YES=0

usage() {
  cat <<'EOF'
Usage: scripts/eligibility/clean_outputs.sh [--dry-run] [--yes]

Remove generated eligibility-path TSV files from:
  data/eligibility_path/exports/intermediates
  data/eligibility_path/exports/final

Options:
  --dry-run   Print files that would be removed, without deleting them.
  --yes, -y   Delete without prompting.
  --help, -h  Show this help text.
EOF
}

for arg in "$@"; do
  case "$arg" in
    --dry-run)
      DRY_RUN=1
      ;;
    --yes|-y)
      ASSUME_YES=1
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      usage >&2
      exit 2
      ;;
  esac
done

cd "$REPO_ROOT"

TARGET_DIRS=(
  "data/eligibility_path/exports/intermediates"
  "data/eligibility_path/exports/final"
)

FILES=()
while IFS= read -r file; do
  FILES+=("$file")
done < <(
  for dir in "${TARGET_DIRS[@]}"; do
    if [[ -d "$dir" ]]; then
      find "$dir" -type f -name "*.tsv"
    fi
  done | sort
)

if [[ ${#FILES[@]} -eq 0 ]]; then
  echo "No generated eligibility-path TSV files found."
  exit 0
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  printf "Would remove %s generated eligibility-path TSV file(s):\n" "${#FILES[@]}"
  printf "  %s\n" "${FILES[@]}"
  exit 0
fi

if [[ "$ASSUME_YES" -ne 1 ]]; then
  printf "Delete %s generated eligibility-path TSV file(s)? [y/N] " "${#FILES[@]}"
  read -r response
  case "$response" in
    y|Y|yes|YES)
      ;;
    *)
      echo "Cancelled."
      exit 0
      ;;
  esac
fi

rm -- "${FILES[@]}"
printf "Removed %s generated eligibility-path TSV file(s).\n" "${#FILES[@]}"
