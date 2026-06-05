from __future__ import annotations

import importlib


PIPELINE_IMPORT_CONTRACTS = [
    "aus_trial_universe.ctgov.drug_ontology",
    "aus_trial_universe.ctgov.drug_ontology.preprocessing.atc_tree",
    "aus_trial_universe.ctgov.drug_ontology.shared.schema",
    "aus_trial_universe.ctgov.drug_ontology.shared.paths",
    "aus_trial_universe.ctgov.drug_ontology.shared.run_archive",
    "aus_trial_universe.ctgov.drug_ontology.ctgov.interventions",
    "aus_trial_universe.ctgov.drug_ontology.ctgov.unique_terms",
    "aus_trial_universe.ctgov.drug_ontology.ctgov.load_interventions",
    "aus_trial_universe.ctgov.drug_ontology.identity.rxnorm.matcher",
    "aus_trial_universe.ctgov.drug_ontology.identity.rxnorm.build_input_mapping",
    "aus_trial_universe.ctgov.drug_ontology.identity.rxnorm.load_mapping",
    "aus_trial_universe.ctgov.drug_ontology.sources.atc.ingredient_to_atc",
    "aus_trial_universe.ctgov.drug_ontology.sources.atc.load",
    "aus_trial_universe.ctgov.drug_ontology.sources.fda.product_components",
    "aus_trial_universe.ctgov.drug_ontology.sources.fda.load",
    "aus_trial_universe.ctgov.drug_ontology.sources.pottr.drug_classes",
    "aus_trial_universe.ctgov.drug_ontology.sources.pottr.load",
    "aus_trial_universe.ctgov.drug_ontology.sources.chembl.molecules",
    "aus_trial_universe.ctgov.drug_ontology.sources.chembl.load",
]

ANALYSIS_IMPORT_CONTRACTS = [
    "aus_trial_universe.ctgov.drug_ontology.analysis.pottr.ctgov_crosswalk",
    "aus_trial_universe.ctgov.drug_ontology.analysis.pottr.mapping_helpers",
    "aus_trial_universe.ctgov.drug_ontology.analysis.topograph.extract_unique_drugs",
    "aus_trial_universe.ctgov.drug_ontology.analysis.topograph.annotate_unique_drugs_with_atc",
    "aus_trial_universe.ctgov.drug_ontology.analysis.topograph.ctgov_drug_crosswalk",
]

PIPELINE_CLI_MODULES_WITH_ARG_PARSERS = [
    "aus_trial_universe.ctgov.drug_ontology.ctgov.unique_terms",
    "aus_trial_universe.ctgov.drug_ontology.preprocessing.atc_tree",
    "aus_trial_universe.ctgov.drug_ontology.shared.run_archive",
    "aus_trial_universe.ctgov.drug_ontology.ctgov.load_interventions",
    "aus_trial_universe.ctgov.drug_ontology.identity.rxnorm.build_input_mapping",
    "aus_trial_universe.ctgov.drug_ontology.identity.rxnorm.load_mapping",
    "aus_trial_universe.ctgov.drug_ontology.sources.atc.load",
    "aus_trial_universe.ctgov.drug_ontology.sources.fda.load",
    "aus_trial_universe.ctgov.drug_ontology.sources.pottr.load",
    "aus_trial_universe.ctgov.drug_ontology.sources.chembl.load",
]

ANALYSIS_CLI_MODULES_WITH_ARG_PARSERS = [
    "aus_trial_universe.ctgov.drug_ontology.analysis.pottr.ctgov_crosswalk",
    "aus_trial_universe.ctgov.drug_ontology.analysis.topograph.extract_unique_drugs",
    "aus_trial_universe.ctgov.drug_ontology.analysis.topograph.annotate_unique_drugs_with_atc",
    "aus_trial_universe.ctgov.drug_ontology.analysis.topograph.ctgov_drug_crosswalk",
]


def test_canonical_drug_ontology_import_paths_are_available():
    for module_name in PIPELINE_IMPORT_CONTRACTS:
        importlib.import_module(module_name)


def test_analysis_drug_ontology_import_paths_are_available():
    for module_name in ANALYSIS_IMPORT_CONTRACTS:
        importlib.import_module(module_name)


def test_current_cli_modules_still_expose_arg_parsers():
    for module_name in PIPELINE_CLI_MODULES_WITH_ARG_PARSERS + ANALYSIS_CLI_MODULES_WITH_ARG_PARSERS:
        module = importlib.import_module(module_name)

        parser = module.build_arg_parser()

        assert parser.prog
