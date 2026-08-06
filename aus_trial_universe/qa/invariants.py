"""SYSTEMATIC INVARIANT SWEEP over the whole live corpus — deterministic only, no LLM, no network.

    python -m aus_trial_universe.qa.invariants        # exit 1 on any violation

Motivation: every defect found on 2026-08-05 was in a DETERMINISTIC HELPER that feeds the LLM stage, not in the
LLM. Those helpers are pure functions, so they can be swept exhaustively rather than stumbled over. The sweep
states the invariants each layer must satisfy and checks all 4,978 cancer_type + 837 gene + 171 signature values.

  C1  unbounded pattern      a normaliser truncates a WORD (regex class / substring replace with no word boundary)
  C2  idempotence            f(f(x)) == f(x) for every deterministic transform
  C3  no invention           canonicalisation must not ADD a positive code / term that was not in the input
  C4  round-trip             parse -> render -> parse is stable
  C5  name/code mirror       the rendered name expression mirrors the code expression term-for-term
  C6  key collision sanity   two values sharing a concept key must map to the same code (else it is a real group)
"""
import collections
import csv
import re
import sys
from pathlib import Path

from aus_trial_universe.tasks.eligibility.mapping import consistency as CT_CONS
from aus_trial_universe.tasks.eligibility.mapping.cancer_type import expr as CE
from aus_trial_universe.tasks.eligibility.mapping.cancer_type import vocab as CV
from aus_trial_universe.tasks.eligibility.mapping.gene_alteration import expr as GE
from aus_trial_universe.tasks.eligibility.mapping.gene_alteration import reconcile as GR
from aus_trial_universe.tasks.eligibility.mapping.reconcile import deterministic_pass

from aus_trial_universe.core.paths import CURRENT_VERSION, ELIGIBILITY_OUTPUT

MAPS = Path(ELIGIBILITY_OUTPUT) / CURRENT_VERSION
csv.field_size_limit(10 ** 9)
V = collections.defaultdict(list)          # check id -> [(detail, ...)]


def rows(name):
    with open(MAPS / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


ct = rows("cancer_type_map_finalised.tsv")
ga = rows("finalised_gene_alteration_map.tsv")
sig = rows("finalised_molecular_signature_map.tsv")
print(f"corpus: cancer_type={len(ct)} gene={len(ga)} signature={len(sig)}")

# --------------------------------------------------------------- C1 unbounded patterns
_WORD = re.compile(r"[a-z0-9]+")


def truncated_words(original: str, normalised: str) -> list[str]:
    """Tokens in `normalised` that are a PROPER FRAGMENT of an original word (the regex-ate-a-letter signature).

    A legitimate normaliser only ever DELETES whole words, so every surviving token must appear as a whole word in
    the input. A token that is only a substring of some input word means the pattern cut through it.
    """
    src_words = set(_WORD.findall(original.lower()))
    bad = []
    for tok in _WORD.findall(normalised):
        if tok in src_words:
            continue
        if any(tok != w and tok in w for w in src_words):      # a fragment of a real word
            bad.append(tok)
    return bad


for r in ct:
    v = r["cancer_type"]
    frags = truncated_words(v, CT_CONS.canonical_key(v))
    if frags:
        V["C1_ct_canonical_key_truncates"].append((v[:88], sorted(set(frags))[:4]))
for r in ga:
    v = r["gene_alteration"]
    frags = truncated_words(v, GR.concept_key(v))
    if frags:
        V["C1_gene_concept_key_truncates"].append((v[:88], sorted(set(frags))[:4]))

# --------------------------------------------------------------- C2 idempotence
def idem(fn, value, label, bucket):
    try:
        once = fn(value)
        twice = fn(once)
    except Exception as exc:                                    # noqa: BLE001
        V[f"{bucket}_raises"].append((value[:80], f"{type(exc).__name__}: {exc}"))
        return
    if once != twice:
        V[bucket].append((value[:70], f"{once!r} -> {twice!r}"))


for r in ct:
    idem(deterministic_pass, r["oncotree_code_finalised"], "ct", "C2_ct_deterministic_pass_not_idempotent")
    idem(CV.canonical_form, r["oncotree_code_finalised"], "ct", "C2_ct_canonical_form_not_idempotent")
    idem(CV.normalise_code_expression, r["oncotree_code_finalised"], "ct", "C2_ct_normalise_not_idempotent")
    idem(CT_CONS.canonical_key, r["cancer_type"], "ct", "C2_ct_canonical_key_not_idempotent")
for r in ga:
    idem(GE.canonicalise, r["finding_model_FINAL"], "ga", "C2_gene_canonicalise_not_idempotent")
    idem(GR.concept_key, r["gene_alteration"], "ga", "C2_gene_concept_key_not_idempotent")
    idem(lambda e: GR.mechanical_fixes(e)[0], r["finding_model_FINAL"], "ga", "C2_gene_mechanical_not_idempotent")
for r in sig:
    idem(GE.canonicalise, r["finding_model_FINAL"], "sig", "C2_sig_canonicalise_not_idempotent")

# --------------------------------------------------------------- C3 no invention
for r in ct:
    src = r["oncotree_code_finalised"]
    if not src.strip():
        continue
    before, after = CE.positive_codes(src), CE.positive_codes(CV.canonical_form(src))
    if before is None or after is None:
        continue
    added = after - before
    if added:
        V["C3_ct_canonical_form_invents_a_code"].append((src[:70], sorted(added)))

_TERM = re.compile(r"([A-Za-z][A-Za-z0-9]*)\[([^\]]*)\]")
for r in ga + sig:
    src = r["finding_model_FINAL"]
    if not src.strip():
        continue
    try:
        out = GE.canonicalise(src)
    except Exception:                                            # noqa: BLE001 - reported by C2
        continue
    added = set(_TERM.findall(out)) - set(_TERM.findall(src))
    if added:
        V["C3_gene_canonicalise_invents_a_term"].append((src[:70], sorted(added)[:3]))

# --------------------------------------------------------------- C4 round-trip
for r in ct:
    src = r["oncotree_code_finalised"]
    if not src.strip():
        continue
    try:
        node, table = CE.parse(src)
        again = CE.render(node)
        node2, _ = CE.parse(again)
        if CE.render(node2) != again:
            V["C4_ct_render_not_stable"].append((src[:70], f"{again!r} -> {CE.render(node2)!r}"))
    except Exception as exc:                                     # noqa: BLE001
        V["C4_ct_parse_render_raises"].append((src[:70], f"{type(exc).__name__}: {exc}"))

# --------------------------------------------------------------- C5 name/code mirror
# Mirrors `oncotree_code_FINAL` — measured 4,934 of 4,935. R8 ("derive + write") renders the name FROM the final
# code, so this is the column to compare against.
for r in ct:
    code, name = r["oncotree_code_finalised"], r.get("oncotree_name_finalised", "")
    if not code.strip():
        continue
    expected = CV.render_name_expression(code)
    if name and name != expected:
        V["C5_ct_name_code_mismatch"].append((code[:60], f"{name[:50]!r} != {expected[:50]!r}"))

# --------------------------------------------------------------- C6 key collisions
for label, data, keyfn, kcol, vcol in (
        ("ct", ct, CT_CONS.canonical_key, "cancer_type", "oncotree_code_finalised"),
        ("gene", ga, GR.concept_key, "gene_alteration", "finding_model_FINAL")):
    by = collections.defaultdict(set)
    for r in data:
        by[keyfn(r[kcol])].add(r[vcol])
    for k, codes in by.items():
        if len(codes) > 1:
            V[f"C6_{label}_key_collision_with_different_codes"].append((k[:70], sorted(codes)[:3]))

# --------------------------------------------------------------- report
print("\n" + "=" * 100)
order = sorted(V, key=lambda k: -len(V[k]))
if not order:
    print("NO INVARIANT VIOLATIONS")
for k in order:
    items = V[k]
    print(f"\n{'#' * 100}\n# {k}  —  {len(items)}\n{'#' * 100}")
    for a, b in items[:6]:
        print(f"  {a!r}\n      {b}")
    if len(items) > 6:
        print(f"  … +{len(items) - 6} more")
print("\n" + "=" * 100)
print("SUMMARY")
for k in order:
    print(f"  {len(V[k]):>5}  {k}")


if __name__ == "__main__":
    raise SystemExit(1 if V else 0)
