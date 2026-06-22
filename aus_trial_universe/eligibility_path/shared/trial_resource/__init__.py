__all__ = [
    "CombinedEligibilityResourceInputs",
    "CombinedEligibilityResourceOutputs",
    "CombinedTrialResourceInputs",
    "CombinedTrialResourceOutputs",
    "combine_cohort_resource_tables",
    "combine_trial_resource_tables",
    "discover_pipeline_inputs",
    "run_combined_trial_resource_export",
]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(name)

    from . import combined_trial_resource_export

    return getattr(combined_trial_resource_export, name)
