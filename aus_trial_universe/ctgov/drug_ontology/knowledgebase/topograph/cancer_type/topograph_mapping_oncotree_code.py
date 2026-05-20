from __future__ import annotations

import argparse
import csv
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

CODE_RE = re.compile(r"[A-Z0-9_/-]+")
CODED_TERM_RE = re.compile(
    r"(?P<sep>^|[;,]\s*)"
    r"(?P<description>[^;()]*?)"
    r"\s*\((?P<code>[A-Z0-9_/-]+)\)"
)


def _normalize(value: str | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _replace_coded_terms_without_except(value: str) -> str:
    """
    Replace OncoTree terms of the form 'description (CODE)' with 'CODE'.

    This preserves list delimiters such as ', ' and '; '. The description may
    itself contain commas, for example 'Neuroendocrine Carcinoma, NOS (NECNOS)'.
    """
    value = _normalize(value)
    if not value:
        return value

    def replacement(match: re.Match[str]) -> str:
        sep = match.group("sep") or ""
        code = match.group("code")
        return f"{sep}{code}"

    return CODED_TERM_RE.sub(replacement, value).strip()


def extract_oncotree_code_text(value: str | None) -> str:
    """
    Convert OncoTree labels to code-only text wherever an OncoTree code exists.

    Examples:
        'Acral Melanoma (ACRM)' -> 'ACRM'
        'Solid tumour' -> 'Solid tumour'
        'Solid tumour except Breast (BREAST), Esophagus/Stomach (STOMACH)'
            -> 'Solid tumour except BREAST, STOMACH'
        'Skin (SKIN) except Melanoma (MEL)' -> 'SKIN except MEL'
    """
    value = _normalize(value)
    if not value:
        return value

    # Preserve the free-text qualifier 'except' while still code-normalising both sides.
    # This is important for values like 'Solid tumour except Breast (BREAST)'.
    parts = re.split(r"(\bexcept\b)", value, flags=re.IGNORECASE)
    if len(parts) > 1:
        cleaned_parts = []
        for part in parts:
            if re.fullmatch(r"\bexcept\b", part, flags=re.IGNORECASE):
                cleaned_parts.append(part)
            else:
                cleaned_parts.append(_replace_coded_terms_without_except(part))

        # Normalise spacing around 'except' without changing the term casing.
        cleaned = " ".join(p.strip() for p in cleaned_parts if p.strip())
        cleaned = re.sub(r"\s+([,;])", r"\1", cleaned)
        cleaned = re.sub(r"([,;])\s*", r"\1 ", cleaned)
        return cleaned.strip()

    return _replace_coded_terms_without_except(value)


def process(
    input_csv: Path,
    output_csv: Path,
    source_column: str = "oncotree_curation",
    output_column: str = "oncotree_code",
) -> None:
    with input_csv.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("Input CSV does not contain a header row")

        fieldnames = list(reader.fieldnames)
        source_col_lookup = {name.lower(): name for name in fieldnames}
        source_col = source_col_lookup.get(source_column.lower())
        if source_col is None:
            raise ValueError(f"Input CSV must contain column: {source_column}")

        rows = []
        for row in reader:
            row[output_column] = extract_oncotree_code_text(row.get(source_col, ""))
            rows.append(row)

    output_fieldnames = fieldnames + ([] if output_column in fieldnames else [output_column])

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=output_fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    logger.info("Wrote %s rows to %s", len(rows), output_csv)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add a code-only OncoTree column to a Topograph cancer-type mapping CSV."
    )
    parser.add_argument("--input_csv", required=True, type=Path)
    parser.add_argument("--output_csv", required=True, type=Path)
    parser.add_argument("--source_column", default="oncotree_curation")
    parser.add_argument("--output_column", default="oncotree_code")
    parser.add_argument("--log_level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    process(
        input_csv=args.input_csv,
        output_csv=args.output_csv,
        source_column=args.source_column,
        output_column=args.output_column,
    )


if __name__ == "__main__":
    main()
