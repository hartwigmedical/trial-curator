from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aus_trial_universe.trial_drug_curation.enrichment.pottr import (
    POTTR_DRUG_CLASS_HIERARCHY_URL,
    POTTR_DRUG_DATABASE_URL,
)
from aus_trial_universe.trial_drug_curation.llm.client import (
    DEFAULT_MODEL,
    TrialDrugCurationClient,
)
from aus_trial_universe.trial_drug_curation.prompts.prompt_builder import build_prompt
from aus_trial_universe.trial_drug_curation.utils.constants import (
    DEFAULT_ONCOTREE_YAML,
    DEFAULT_OUTPUT_DIR,
    REGISTRY_LABELS,
    SUPPORTED_REGISTRIES,
)
from aus_trial_universe.trial_drug_curation.utils.local_records import (
    load_trial_records,
)
from aus_trial_universe.trial_drug_curation.utils.outputs import (
    count_tsv_data_rows,
    dated_output_path,
    merge_tsv_tables,
    next_available_output_path,
    unique_trial_ids_in_tsv,
    write_skipped_trials_tsv,
)
from aus_trial_universe.trial_drug_curation.utils.resources import (
    resolve_resource_paths,
)
from aus_trial_universe.trial_drug_curation.utils.trial_ids import (
    SkippedTrial,
    select_trial_ids,
    unique_trial_ids,
)

ProgressLogger = Callable[[str], None]
ClientFactory = Callable[..., TrialDrugCurationClient]


@dataclass(frozen=True)
class CurationRunConfig:
    trial_ids: Sequence[str]
    output_dir: str | Path = DEFAULT_OUTPUT_DIR
    output_path: str | Path | None = None
    model: str = DEFAULT_MODEL
    max_output_tokens: int | None = None
    ctgov_input: str | Path | None = None
    anzctr_input: str | Path | None = None
    oncotree_yaml: str = DEFAULT_ONCOTREE_YAML
    pottr_drug_class_hierarchy_url: str = POTTR_DRUG_CLASS_HIERARCHY_URL
    pottr_drug_database_url: str = POTTR_DRUG_DATABASE_URL
    batch_size: int = 10
    prompt_only: bool = False


@dataclass(frozen=True)
class CurationRunResult:
    output_path: Path | None
    skipped_path: Path | None
    prompt: str
    tsv: str | None
    data_rows: int | None
    unique_trial_ids: tuple[str, ...]
    selected_trial_ids_by_registry: dict[str, tuple[str, ...]]
    skipped_trials: tuple[SkippedTrial, ...]
    resources: dict[str, str]
    records_by_registry: dict[str, list[dict[str, Any]]]


def run_trial_drug_curation(
    config: CurationRunConfig,
    *,
    progress: ProgressLogger | None = None,
    client_factory: ClientFactory = TrialDrugCurationClient,
) -> CurationRunResult:
    unique_ids = unique_trial_ids(config.trial_ids)
    if not unique_ids:
        raise ValueError("At least one trial ID is required.")

    selection = select_trial_ids(unique_ids)
    skipped: tuple[SkippedTrial, ...] = selection.skipped_trials
    _log(
        progress,
        f"Loaded {len(unique_ids)} unique trial ID(s) "
        f"({registry_summary(selection.grouped_trial_ids) or 'no recognised IDs'}).",
    )
    if skipped:
        _log(progress, f"Marked {len(skipped)} unrecognised trial ID(s) as skipped.")

    _log(progress, "Resolving local resource paths and loading selected records...")
    resources = resolve_resource_paths(
        registries=selection.grouped_trial_ids.keys(),
        ctgov_input=config.ctgov_input,
        anzctr_input=config.anzctr_input,
        oncotree_yaml=config.oncotree_yaml,
        pottr_drug_class_hierarchy_url=config.pottr_drug_class_hierarchy_url,
        pottr_drug_database_url=config.pottr_drug_database_url,
    )
    loaded_records = load_trial_records(
        grouped_trial_ids=selection.grouped_trial_ids,
        resources=resources,
    )
    skipped = (*skipped, *loaded_records.skipped_trials)
    if loaded_records.skipped_trials:
        _log(
            progress,
            f"Marked {len(loaded_records.skipped_trials)} trial ID(s) missing "
            "from local inputs as skipped.",
        )

    skipped_path = write_skipped_trials_tsv(
        skipped,
        output_dir=skipped_output_dir(config),
    )
    if skipped_path is not None:
        _log(progress, f"Wrote skipped trial report: {skipped_path}")

    records_by_registry = {
        registry: registry_records
        for registry, registry_records in loaded_records.records_by_registry.items()
        if registry_records
    }
    if not records_by_registry:
        _log(progress, "No local trial records found; skipping OpenAI API call.")
        return CurationRunResult(
            output_path=None,
            skipped_path=skipped_path,
            prompt="",
            tsv=None,
            data_rows=None,
            unique_trial_ids=tuple(unique_ids),
            selected_trial_ids_by_registry=_selected_ids(selection.grouped_trial_ids),
            skipped_trials=skipped,
            resources=resources,
            records_by_registry=records_by_registry,
        )

    _log(progress, "Building prompt from selected local trial records...")
    prompt = build_prompt(
        resources=resources,
        records_by_registry=records_by_registry,
    )
    _log(progress, f"Prompt built with local trial records ({len(prompt):,} characters).")
    _log(progress, f"OpenAI request trial records: {records_summary(records_by_registry)}.")

    if config.prompt_only:
        _log(progress, "Prompt-only mode; skipping OpenAI API call.")
        return CurationRunResult(
            output_path=None,
            skipped_path=skipped_path,
            prompt=prompt,
            tsv=None,
            data_rows=None,
            unique_trial_ids=tuple(unique_ids),
            selected_trial_ids_by_registry=_selected_ids(selection.grouped_trial_ids),
            skipped_trials=skipped,
            resources=resources,
            records_by_registry=records_by_registry,
        )

    output_path = resolve_output_path(config)
    batches = batched_records(records_by_registry, batch_size=config.batch_size)
    record_count = sum(len(records) for records in records_by_registry.values())
    _log(
        progress,
        f"Submitting {record_count} trial record(s) to OpenAI model {config.model} "
        f"in {len(batches)} batch(es) of up to {config.batch_size}.",
    )
    _log(
        progress,
        "The API processes all requested output columns in each batch response; "
        "field-level progress is not available within each batch call.",
    )
    client = client_factory(
        model=config.model,
        max_output_tokens=config.max_output_tokens,
    )

    batch_tsvs: list[str] = []
    for batch_number, batch_records in enumerate(batches, start=1):
        batch_prompt = build_prompt(
            resources=resources,
            records_by_registry=batch_records,
        )
        _log(
            progress,
            f"Submitting batch {batch_number}/{len(batches)} "
            f"({records_summary(batch_records)}; {len(batch_prompt):,} characters)...",
        )
        _log(progress, f"Waiting for OpenAI structured response for batch {batch_number}...")
        batch_tsv = client.curate_tsv(user_prompt=batch_prompt)
        batch_rows = count_tsv_data_rows(batch_tsv)
        _log(progress, f"Batch {batch_number}/{len(batches)} returned {batch_rows} row(s).")
        batch_tsvs.append(batch_tsv)

    tsv = merge_tsv_tables(batch_tsvs)

    _log(progress, "Received structured response; writing TSV...")
    data_rows = count_tsv_data_rows(tsv)
    if data_rows == 0:
        _log(progress, "WARNING: Model returned a header-only TSV with 0 data rows.")
    else:
        _log(progress, f"Model returned {data_rows} TSV data row(s).")
    output_trial_ids = unique_trial_ids_in_tsv(tsv)
    requested_trial_ids = {
        str(record.get("trialId", ""))
        for records in records_by_registry.values()
        for record in records
        if record.get("trialId")
    }
    _log(
        progress,
        f"TSV includes rows for {len(output_trial_ids)} unique trial ID(s) "
        f"from {len(requested_trial_ids)} local trial record(s).",
    )
    missing_output_trial_ids = sorted(requested_trial_ids - output_trial_ids)
    if missing_output_trial_ids:
        _log(
            progress,
            "No output rows returned for trial ID(s): "
            f"{_short_id_list(missing_output_trial_ids, limit=20)}",
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(tsv, encoding="utf-8")
    _log(progress, f"Wrote TSV: {output_path}")
    return CurationRunResult(
        output_path=output_path,
        skipped_path=skipped_path,
        prompt=prompt,
        tsv=tsv,
        data_rows=data_rows,
        unique_trial_ids=tuple(unique_ids),
        selected_trial_ids_by_registry=_selected_ids(selection.grouped_trial_ids),
        skipped_trials=skipped,
        resources=resources,
        records_by_registry=records_by_registry,
    )


def write_tsv_for_trials(
    trial_ids: Sequence[str],
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    output_path: str | Path | None = None,
    model: str = DEFAULT_MODEL,
    max_output_tokens: int | None = None,
    ctgov_input: str | Path | None = None,
    anzctr_input: str | Path | None = None,
    oncotree_yaml: str = DEFAULT_ONCOTREE_YAML,
    pottr_drug_class_hierarchy_url: str = POTTR_DRUG_CLASS_HIERARCHY_URL,
    pottr_drug_database_url: str = POTTR_DRUG_DATABASE_URL,
    batch_size: int = 10,
    progress: ProgressLogger | None = None,
) -> Path:
    result = run_trial_drug_curation(
        CurationRunConfig(
            trial_ids=trial_ids,
            output_dir=output_dir,
            output_path=output_path,
            model=model,
            max_output_tokens=max_output_tokens,
            ctgov_input=ctgov_input,
            anzctr_input=anzctr_input,
            oncotree_yaml=oncotree_yaml,
            pottr_drug_class_hierarchy_url=pottr_drug_class_hierarchy_url,
            pottr_drug_database_url=pottr_drug_database_url,
            batch_size=batch_size,
        ),
        progress=progress,
    )
    if result.output_path is None:
        raise ValueError("No requested trial IDs were found in local inputs.")
    return result.output_path


def registry_summary(grouped_trial_ids: dict[str, list[str]]) -> str:
    return ", ".join(
        f"{REGISTRY_LABELS[registry]}={len(grouped_trial_ids[registry])}"
        for registry in SUPPORTED_REGISTRIES
        if registry in grouped_trial_ids
    )


def records_summary(records_by_registry: dict[str, list[dict[str, Any]]]) -> str:
    sections: list[str] = []
    for registry in SUPPORTED_REGISTRIES:
        records = records_by_registry.get(registry, [])
        if not records:
            continue
        trial_ids = [str(record.get("trialId", "")) for record in records]
        sections.append(
            f"{REGISTRY_LABELS[registry]}={len(records)} "
            f"[{_short_id_list(trial_ids)}]"
        )
    return "; ".join(sections)


def batched_records(
    records_by_registry: dict[str, list[dict[str, Any]]],
    *,
    batch_size: int,
) -> list[dict[str, list[dict[str, Any]]]]:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1.")

    flat_records: list[tuple[str, dict[str, Any]]] = []
    for registry in SUPPORTED_REGISTRIES:
        flat_records.extend(
            (registry, record)
            for record in records_by_registry.get(registry, [])
        )

    batches: list[dict[str, list[dict[str, Any]]]] = []
    for start in range(0, len(flat_records), batch_size):
        batch: dict[str, list[dict[str, Any]]] = {}
        for registry, record in flat_records[start : start + batch_size]:
            batch.setdefault(registry, []).append(record)
        batches.append(batch)
    return batches


def resolve_output_path(config: CurationRunConfig) -> Path:
    if config.output_path is not None:
        return Path(config.output_path)
    return next_available_output_path(dated_output_path(output_dir=config.output_dir))


def skipped_output_dir(config: CurationRunConfig) -> Path:
    if config.output_path is not None:
        return Path(config.output_path).parent
    return Path(config.output_dir)


def _selected_ids(grouped_trial_ids: dict[str, list[str]]) -> dict[str, tuple[str, ...]]:
    return {
        registry: tuple(grouped_trial_ids[registry])
        for registry in SUPPORTED_REGISTRIES
        if registry in grouped_trial_ids
    }


def _short_id_list(trial_ids: Sequence[str], limit: int = 12) -> str:
    visible = [trial_id for trial_id in trial_ids if trial_id][:limit]
    suffix = "" if len(trial_ids) <= limit else f", ... +{len(trial_ids) - limit}"
    return ", ".join(visible) + suffix


def _log(progress: ProgressLogger | None, message: str) -> None:
    if progress is not None:
        progress(message)
