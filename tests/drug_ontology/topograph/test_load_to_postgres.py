from __future__ import annotations

from pathlib import Path

from aus_trial_universe.ctgov.drug_ontology.knowledgebase.topograph import load_to_postgres


def test_parser_uses_sql_dump_design_and_has_no_output_tsv_argument():
    parser = load_to_postgres.build_arg_parser()
    option_strings = {option for action in parser._actions for option in action.option_strings}

    assert "--topograph_master_tsv" in option_strings
    assert "--topograph_source_version" in option_strings
    assert "--output_tsv" not in option_strings
    assert "--output_check_tsv" not in option_strings


def test_build_arg_parser_accepts_required_args(tmp_path: Path):
    input_tsv = tmp_path / "TOPOGRAPH-master.tsv"
    input_tsv.write_text("", encoding="utf-8")

    args = load_to_postgres.build_arg_parser().parse_args(
        [
            "--topograph_master_tsv",
            str(input_tsv),
            "--topograph_source_version",
            "Topograph_test",
        ]
    )

    assert args.topograph_master_tsv == input_tsv
    assert args.topograph_source_version == "Topograph_test"
