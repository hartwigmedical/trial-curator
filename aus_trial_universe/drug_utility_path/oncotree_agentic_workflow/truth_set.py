"""Curated truth set of real trial `cancerTypes` rows.

Single source of truth shared by the regression tests and the live correctness
harness (:mod:`evaluation`). Each row records what the previous flat-mapper got
wrong and the human-curated correct OncoTree codes.
"""

from __future__ import annotations

from typing import NamedTuple


class TruthRow(NamedTuple):
    input_text: str
    incorrect_codes: tuple[str, ...]
    correct_codes: tuple[str, ...]


TRUTH_SET: tuple[TruthRow, ...] = (
    TruthRow("Recurrent glioblastoma", ("GBM",), ("GB",)),
    TruthRow(
        "Solid Tumor, Adult | Mesothelioma | NSCLC",
        ("Solid-Tumor", "MESO"),
        ("Solid-Tumor", "PEMESO", "PLMESO", "NSCLC"),
    ),
    TruthRow(
        "Metastatic or unresectable malignant mesothelioma",
        ("MESO",),
        ("PEMESO", "PLMESO"),
    ),
    TruthRow(
        "Ovarian Neoplasms | Fallopian Tube Neoplasms | Peritoneal Neoplasms | "
        "Neoplasm Metastasis",
        ("OV",),
        ("OVARY", "PERITONEUM", "Solid-Tumor"),
    ),
    TruthRow(
        "Platinum-resistant ovarian, primary peritoneal, or fallopian tube cancer",
        ("OV",),
        ("OVARY", "PERITONEUM"),
    ),
    TruthRow(
        "Ovarian Cancer | Lung Adenocarcinoma | Endometrial Cancer",
        ("OV", "LUAD", "UCEC"),
        ("OVARY", "LUAD", "UCEC"),
    ),
    TruthRow("Soft tissue sarcoma", ("STS",), ("SOFT_TISSUE",)),
    TruthRow(
        "Breast Cancer | Biliary Tract Carcinoma | Ovarian Cancer | "
        "Endometrial Cancer | Squamous Non-Small Cell Lung Cancer",
        ("BRCA", "BTC", "OV", "UCEC", "LUSC"),
        ("BREAST", "BILIARY_TRACT", "OVARY", "UCEC", "LUSC"),
    ),
    TruthRow(
        "Recurrent Ovarian Cancer | Recurrent Fallopian Tube Cancer | "
        "Recurrent Primary Peritoneal Cancer | Recurrent Endometrial Cancer | "
        "Endometrial Cancer | Low-grade Serous Ovarian Cancer",
        ("OV", "FTC", "UCEC"),
        ("OVARY", "PERITONEUM", "UCEC"),
    ),
    TruthRow(
        "Advanced Solid Tumor | Advanced Lymphoma | Hodgkin lymphoma | "
        "non-Hodgkin B-cell lymphoma | DLBCL",
        ("Solid-Tumor", "LYM"),
        ("Solid-Tumor", "LYMPH", "HL", "NHL", "DLBCLNOS"),
    ),
    TruthRow(
        "Hormone receptor positive recurrent/metastatic gynaecological cancers; "
        "PIK3CA mutated endometrial cancers and PIK3CA mutated gynaecological "
        "cancers excluding endometrial cancers",
        ("UCEC", "HGSOC", "LGSC", "FTT", "PPT", "ESS", "LMS"),
        ("CERVIX", "OVARY", "UTERUS", "VULVA"),
    ),
    TruthRow(
        "Advanced Solid Tumors | Small Cell Lung Cancer | "
        "Central Nervous System Tumors | Neuroendocrine Carcinomas",
        ("Solid-Tumor", "SCLC", "CNS", "PRAD", "NSCLC"),
        (
            "Solid-Tumor", "SCLC", "BRAIN", "CENE", "GINETES", "HNNE", "RNET",
            "LNET", "NECNOS", "NETNOS", "HGONEC", "PANEC", "PANET", "PRNE",
            "TNET", "UNEC",
        ),
    ),
)
