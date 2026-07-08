"""Eligibility-extraction task (spec §6): cohort free text -> DNF eligibility table.

Slice 1: two specialists — an extractor that captures the cohort's eligibility as
DNF rows (cancer type + gene alteration, so combination logic lands as rows), and
a cancer_type->OncoTree normalizer fanned out over the distinct cancer mentions.
More columns become more specialists as the table grows.
"""
