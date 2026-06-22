from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from aus_trial_universe.drug_utility_path.trial_drug_curation.enrichment.pottr import (
    POTTR_DRUG_CLASS_HIERARCHY_URL,
    POTTR_DRUG_DATABASE_URL,
)
from aus_trial_universe.drug_utility_path.trial_drug_curation.llm.client import (
    DEFAULT_MODEL,
)
from aus_trial_universe.drug_utility_path.trial_drug_curation.pipeline import (
    CurationRunConfig,
    run_trial_drug_curation,
)
from aus_trial_universe.drug_utility_path.trial_drug_curation.utils.constants import (
    DEFAULT_ONCOTREE_YAML,
    DEFAULT_OUTPUT_DIR,
)
from aus_trial_universe.drug_utility_path.trial_drug_curation.utils.trial_ids import (
    read_trial_ids_file,
)


def log_progress(message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", file=sys.stderr, flush=True)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run trial drug curation.")
    parser.add_argument("trial_ids", nargs="*", help="Trial ID(s) to curate.")
    parser.add_argument(
        "--trial-ids-file",
        default=None,
        help=(
            "Text file containing trial IDs separated by whitespace, commas, or "
            "newlines. Use '-' to read IDs from stdin."
        ),
    )
    parser.add_argument(
        "--prompt-only",
        action="store_true",
        help="Print the composed prompt instead of calling the LLM.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for dated TSV outputs. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=None,
        help="Explicit TSV output path. Overrides --output-dir.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"OpenAI model to use. Default: {DEFAULT_MODEL}",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=None,
        help="Optional OpenAI max output token value.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Number of local trial records to send per OpenAI request. Default: 10.",
    )
    parser.add_argument(
        "--ctgov-input",
        default=None,
        help=(
            "Local CTGOV input JSON file to use as trial context. Defaults to "
            "the latest data/ctgov/trials/version_*/ctgov_input.json."
        ),
    )
    parser.add_argument(
        "--anzctr-input",
        default=None,
        help=(
            "Local ANZCTR input workbook to use as trial context. Defaults to "
            "the latest data/anzctr/trials/version_*/anzctr_input.xlsx."
        ),
    )
    parser.add_argument(
        "--oncotree-yaml",
        default=DEFAULT_ONCOTREE_YAML,
        help="Local OncoTree YAML file to use for cancer type mapping.",
    )
    parser.add_argument(
        "--no-oncotree-agentic",
        dest="enrich_oncotree",
        action="store_false",
        help=(
            "Skip the OncoTree agentic workflow and keep the curation LLM's "
            "inline OncoTree values instead."
        ),
    )
    parser.add_argument(
        "--pottr-drug-class-hierarchy-url",
        default=POTTR_DRUG_CLASS_HIERARCHY_URL,
        help="POTTR drug class hierarchy source URL.",
    )
    parser.add_argument(
        "--pottr-drug-database-url",
        default=POTTR_DRUG_DATABASE_URL,
        help="POTTR drug database source URL.",
    )
    return parser


def collect_trial_ids(args: argparse.Namespace) -> list[str]:
    trial_ids = list(args.trial_ids)
    if args.trial_ids_file is not None:
        source = "stdin" if args.trial_ids_file == "-" else args.trial_ids_file
        log_progress(f"Reading trial IDs from {source}...")
        trial_ids.extend(read_trial_ids_file(args.trial_ids_file))
    return trial_ids


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        result = run_trial_drug_curation(
            CurationRunConfig(
                trial_ids=collect_trial_ids(args),
                output_dir=args.output_dir,
                output_path=args.output_path,
                model=args.model,
                max_output_tokens=args.max_output_tokens,
                batch_size=args.batch_size,
                ctgov_input=args.ctgov_input,
                anzctr_input=args.anzctr_input,
                oncotree_yaml=args.oncotree_yaml,
                pottr_drug_class_hierarchy_url=args.pottr_drug_class_hierarchy_url,
                pottr_drug_database_url=args.pottr_drug_database_url,
                prompt_only=args.prompt_only,
                enrich_oncotree=args.enrich_oncotree,
            ),
            progress=log_progress,
        )
    except (FileNotFoundError, ValueError) as error:
        parser.error(str(error))

    if args.prompt_only:
        print(result.prompt)
        return 0

    if result.output_path is not None:
        print(result.output_path)
    elif result.skipped_path is not None:
        print(result.skipped_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
