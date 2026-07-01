"""POTTR vs Hartwig eligibility comparison (per-trial, per-criterion).

Reconciles the curated eligibility criteria in the Hartwig final trial resource against the POTTR AU
eligibility file and writes a two-header-row TSV: for each criterion group (cancer_type, gene_alteration,
molecular_signature) a set of hartwig columns, a parsed pottr_* column, and _comparison verdict column(s);
plus POTTR-only pottr_prior_therapy / pottr_expression_ihc display columns.

Run from the repo root with a pandas-enabled interpreter (the repo `python` lacks pandas):

    ~/anaconda3/bin/python aus_trial_universe/eligibility_path/analysis/pottr_comparison.py

Inputs are auto-selected by newest mtime (no dates to edit): the newest final trial resource, the newest
pinned POTTR snapshot, and the newest ctgov POTTR-id alias table. Output is
data/eligibility_path/analysis/eligibility_vs_pottr_comparison_<resource-date>.tsv. Re-run after the
pipeline (`make eligibility-path-run-all`) regenerates the final resource, or after editing the conditions
resource. See analysis/README.md for the verdict vocabulary and reconciliation rules.
"""
from __future__ import annotations
import csv, re, os, glob
from collections import defaultdict
import pandas as pd

ONCO = "data/eligibility_path/resources/oncotree/oncotree_expanded/oncotree_expanded.csv"

def _latest(pattern):
    hits = glob.glob(pattern)
    if not hits:
        raise FileNotFoundError(f"no files match {pattern}")
    return max(hits, key=os.path.getmtime)

# Newest dated inputs. MINE (final resource) advances every pipeline run; the POTTR snapshot and the
# ctgov alias table advance only on a download run -- picking each by newest mtime handles that.
MINE = _latest("data/eligibility_path/exports/final/eligibility_trial_resource_*.tsv")
POTTR = _latest("data/eligibility_path/analysis/pottr_trial_eligibility.AU_snapshot_*.tsv")
ALIAS = _latest("data/trial_inputs/ctgov/input_trials/version_*/02b_pottr_id_aliases_ctgov.tsv")
_date = re.search(r"_(\d{8})\.tsv$", MINE).group(1)
OUT = f"data/eligibility_path/analysis/eligibility_vs_pottr_comparison_{_date}.tsv"


ROOT = "Cancer"; SOLID = "Solid tumour"

def load_oncotree(path):
    code_name = {}
    ancestors = defaultdict(set); descendants = defaultdict(set)
    def parse(cell):
        cell = cell.strip()
        if not cell: return None, None
        m = re.search(r'^(.*?)\s*\(([^()]+)\)\s*$', cell)
        return (m.group(1).strip(), m.group(2).strip()) if m else (cell, cell)
    with open(path, encoding="utf-8-sig") as f:
        r = csv.reader(f); h = next(r)
        lc = [i for i, x in enumerate(h) if x.startswith("level_")]
        for row in r:
            lineage = []
            for i in lc:
                if i < len(row):
                    nm, cd = parse(row[i])
                    if cd: lineage.append((nm, cd))
            for depth, (nm, cd) in enumerate(lineage):
                code_name.setdefault(cd, nm)
                anc = {c for _, c in lineage[:depth]}
                ancestors[cd] |= anc
                for a in anc: descendants[a].add(cd)
    return code_name, ancestors, descendants

CODE_NAME, ANC, DESC = load_oncotree(ONCO)

def rel(a, b):
    """Relationship of hartwig-code a to pottr-code b."""
    if a == b: return "EXACT"
    if b in DESC.get(a, ()): return "HARTWIG_BROADER"    # a is ancestor of b -> hartwig includes b
    if a in DESC.get(b, ()): return "HARTWIG_NARROWER"   # a descendant of b -> hartwig is subset
    return "UNRELATED"

# Curated POTTR catype free-text -> OncoTree code (judgement; hierarchy handles the rest).
CATYPE = {
 "anaplastic astrocytoma":"ASTR","anaplastic thyroid cancer":"THAP","b-cell lymphoma":"MBN",
 "basal cell carcinoma":"BCC","biliary tract cancer":"BILIARY_TRACT","biliary tract cancers":"BILIARY_TRACT",
 "bladder cancer":"BLADDER","breast cancer":"BREAST","central nervous system cancer":"BRAIN",
 "cervical cancer":"CERVIX","cervical clear cell carcinoma":"CECC","cholangiocarcinoma":"CHOL",
 "chondrosarcoma":"CHS","chronic lymphocytic leukaemia":"CLLSLL","clear cell renal cell carcinoma":"CCRCC",
 "colorectal adenocarcinoma":"COADREAD","colorectal cancer":"COADREAD","cutaneous melanoma":"SKCM",
 "dedifferentiated chondrosarcoma":"DDCHS","diffuse gastric cancer":"DSTAD",
 "diffuse large b-cell lymphoma":"DLBCLNOS","diffuse large b-cell lymphoma,ritcher's transformation":"DLBCLNOS",
 "endometrial cancer":"UCEC","endometrial clear cell carcinoma":"UCCC","fallopian tube carcinoma":"OVARY",
 "follicular lymphoma":"FL","gastric cancer":"STAD","gastroesophageal adenocarcinoma":"GEJ",
 "gastroesophageal cancer":"EGC","gastrointestinal cancer":None,"gastrointestinal stromal tumour":"GIST",
 "gastrooesophageal adenicarcoma":"GEJ","glioblastoma":"GB","glioma":"GNOS","hpv-related cancer":None,
 "head and neck squamous cell carcinoma":"HNSC","high-grade glioma":"HGGNOS",
 "high-grade serous carcinoma of the ovary, fallopian tube, and peritoneum":"HGSOC",
 "hepatocellular carcinoma":"HCC","cytotoxic_chemotherapy":None,
 "high-grade serous ovarian cancer":"HGSOC","low-grade glioma":"LGGNOS","lung squamous cell carcinoma":"LUSC",
 "mantle cell lymphoma":"MCL","marginal zone lymphoma":"MZL","melanoma":"MEL","mesenchymal chondrosarcoma":"MCHS",
 "mesothelioma":"PLMESO","mucosal melanoma":"MEL","multiple myeloma":"PCM","myxoid chondrosarcoma":"MYCHS",
 "neuroblastoma":"NBL","neuroendocrine carcinoma":"NECNOS","neuroendocrine tumour":"NETNOS",
 "non-melanoma skin cancer":"SKIN","non-small cell lung cancer":"NSCLC","non-small-cell lung cancer":"NSCLC",
 "oesophageal carcinoma":"ESCA","ovarian carcinosarcoma":"OCS","ovarian clear cell carcinoma":"CCOV",
 "ovarian mucinous carcinoma":"MOV","ovarian cancer":"OVARY","pik3ca-related overgrowth spectrum":None,
 "pancreatic adenocarcinoma":"PAAD","pancreatic cancer":"PANCREAS","paraganglioma":"PGNG",
 "peripheral t-cell lymphoma":"PTCL","peritneal serous carcinoma":"PSEC","peritoneal serous carcinoma":"PSEC",
 "pheochromocytoma":"PHC","prostate adenocarcinoma":"PRAD","prostate cancer":"PROSTATE",
 "prostate small cell carcinoma":"PRSCC","renal cell carcinoma":"RCC","sarcoma":"SARCNOS",
 "small lymphocytic lymphoma":"CLLSLL","small-cell lung cancer":"SCLC","solid tumour":SOLID,"solid tumours":SOLID,
 "squamous cell carcinoma of the head and neck":"HNSC","systemic mastocytosis":"SM","thyroid cancer":"THYROID",
 "triple-negative breast cancer":"BREAST","urothelial carcinoma":"BLCA","uterine sarcoma":"USARC",
 "uveal melanoma":"UM","vulval clear cell adenocarcinoma":None,"waldenstrom macroglobulinemia":"WM",
 "yolk sac tumors":None,
}
# My non-OncoTree-code special cancer tokens
MINE_CANCER_SPECIAL = {"Pan-cancer": ROOT, "MDS/MPN": "MDS/MPN"}

def map_catype(text):
    t = text.strip().lower()
    if t in CATYPE: return CATYPE[t]
    return None  # unmapped -> caller notes it

# ----------------------------------------------------------------------------- parse my cancer codes
def mine_cancer_codes(cell):
    """Return set of oncotree codes from one of my cancer_type cells (incl uses |, excl uses NOT()&)."""
    codes = set(); unknown = []
    # pull NOT(...) tokens first (exclusive col) then bare tokens (inclusive col)
    raw = cell
    for m in re.findall(r'NOT\(\s*([^()]+?)\s*\)', raw):
        raw = raw.replace(f"NOT({m})", " ")
        codes_unknown_add(m.strip(), codes, unknown)
    for tok in re.split(r'[|,&]', raw):
        tok = tok.strip()
        if tok: codes_unknown_add(tok, codes, unknown)
    return codes, unknown

def codes_unknown_add(tok, codes, unknown):
    tok = tok.strip()
    if not tok: return
    if tok in MINE_CANCER_SPECIAL: codes.add(MINE_CANCER_SPECIAL[tok]); return
    if tok in CODE_NAME or tok in (ROOT, SOLID): codes.add(tok); return
    unknown.append(tok)

# ----------------------------------------------------------------------------- gene/molecular parse
# buckets
MUT, FUS, AMP, DEL, DISR, EXPR, SIG = "mutation","fusion","amplification","deletion","disruption","expression","signature"
INCLUDE, EXCLUDE = "+", "-"

def split_top(s, seps):
    """Split on any char in `seps` only at bracket depth 0 (ignore separators inside []/())."""
    out, buf, depth = [], [], 0
    for ch in s:
        if ch in "[(": depth += 1
        elif ch in "])": depth = max(0, depth - 1)
        if ch in seps and depth == 0:
            out.append("".join(buf)); buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return out

def mine_features(incl, excl):
    """Parse both columns uniformly. The NOT(...) wrapper carries polarity, so the
    column (inclusive vs exclusive) is redundant: bare atom -> must be present (INCLUDE),
    NOT(atom) -> must be absent (EXCLUDE), Wildtype[X] -> mutation must be absent."""
    toks = set()
    def emit(entity, bucket, sign): toks.add((entity, bucket, sign))
    for col in (incl, excl):
        for atom in split_top(col, "|&"):
            atom = atom.strip()
            if not atom: continue
            sign = INCLUDE
            m = re.match(r'NOT\((.*)\)$', atom, re.S)
            if m: atom = m.group(1).strip(); sign = EXCLUDE
            parse_atom(atom, sign, emit)
    return toks

def parse_atom(atom, sign, emit):
    m = re.match(r'([A-Za-z]+)\[(.*)\]$', atom)
    if not m: return
    kind, body = m.group(1), m.group(2)
    genes = re.findall(r'gene(?:Start|End)?=([A-Za-z0-9_-]+)', body)
    if kind == "SmallVariant":
        for g in genes: emit(g, MUT, sign)
    elif kind == "Fusion":
        for g in genes: emit(g, FUS, sign)
    elif kind == "GainDeletion":
        tm = re.search(r'type=([A-Za-z_]+)', body); typ = tm.group(1) if tm else ""
        bucket = DEL if typ in ("HOM_DEL", "LOSS", "DEL", "LOH", "HET_DEL") else AMP
        for g in genes: emit(g, bucket, sign)
    elif kind == "Wildtype":
        # require wildtype == exclude mutation
        for g in genes: emit(g, MUT, EXCLUDE if sign == INCLUDE else INCLUDE)
    elif kind == "Disruption":
        for g in genes: emit(g, DISR, sign)
    elif kind in ("MicrosatelliteStability","homologousRecombination","tumorMutationLoad"):
        if kind == "MicrosatelliteStability":
            val = "MSI" if "MSI" in body else "MSS" if "MSS" in body else body
            emit(f"MSI:{val}", SIG, sign)
        elif kind == "homologousRecombination":
            emit("HRD", SIG, sign if "DEFICIENT" in body else sign)
        else:
            emit("TMB:high", SIG, sign)
    # Arm / PharmocoGenotype: ignored for now (note separately if present)

# POTTR alteration token -> bucket
def pottr_bucket(alt):
    a = alt.lower()
    if a in ("amplification","amp","copy_number_gain","gain"): return AMP
    if a in ("deletion","loss","homozygous_deletion","copy_number_loss"): return DEL
    if a == "fusion" or a.endswith("_fusion"): return FUS
    if a in ("overexpression","protein_expression","low_protein_expression","loss_of_protein_expression",
             "positive","negative","high_protein_expression"): return EXPR
    # everything mutation-like: oncogenic_mutation(s), alteration, specific variants, exon_/codon_/missense, etc.
    return MUT

SIG_GENES = {"microsatellite_instability":"MSI","mismatch_repair":"MSI","tumour_mutational_burden":"TMB",
             "tumor_mutational_burden":"TMB"}
# POTTR uses gene-family shorthands that my resource splits into specific members.
POTTR_GENE_ALIASES = {"IDH": ["IDH1", "IDH2"], "BRCA": ["BRCA1", "BRCA2"]}

# POTTR therapy predicates -> "prior_therapy" comparison dimension (my schema models none of these).
THERAPY_PREDS = {"prior_therapy","systemic_therapy","line_of_therapy","neoadjuvant_therapy",
                 "adjuvant_therapy","concurrent_therapy"}

def is_gene_symbol(ent):
    """Gene symbols are short upper-case tokens; POTTR predicates use snake_case / underscores
    (prior_therapy, ki67_index, hormone_secretion, sensitive_to, ...)."""
    if "_" in ent: return False
    return any(c.isupper() for c in ent)

def pottr_features(rows):
    """Parse all cohort rows of a trial. Returns a dict of categorised criteria so that every
    POTTR token is accounted for: the reconcilable dimensions (cancer/gene/signature) plus the
    schema-gap dimensions my resource does not model (expression/IHC, prior therapy, other
    clinical predicates) and non-criterion status flags."""
    c_incl, c_excl = set(), set(); unmapped = []
    gene_toks, sig_toks = set(), set()
    expression, prior_therapy, other_clinical, status_flags = [], [], [], []
    def sgn(s, txt): return f"{'NOT ' if s==EXCLUDE else ''}{txt}"
    for row in rows:
        # split on ; (AND). Parentheses group ORs but we treat each atom independently.
        for part in re.split(r';', row):
            part = part.strip().lstrip("*").strip()
            if not part: continue
            for atom in re.split(r'\bOR\b', part):
                atom = atom.strip().lstrip("*").strip().strip("()").strip()
                if not atom: continue
                sign = INCLUDE
                m = re.match(r'NOT\s+(.*)$', atom, re.I)
                if m: atom = m.group(1).strip(); sign = EXCLUDE
                cm = re.match(r'catype:\s*(.+)$', atom, re.I)
                if cm:
                    name = cm.group(1).strip(); code = map_catype(name)
                    if code is None:
                        if name.lower() not in CATYPE: unmapped.append(name)
                    else:
                        (c_excl if sign == EXCLUDE else c_incl).add(code)
                    continue
                gm = re.match(r'([A-Za-z0-9_]+):\s*(.+)$', atom)
                if gm:
                    ent, alt = gm.group(1), gm.group(2).strip()
                    el = ent.lower()
                    if el in SIG_GENES:
                        sig_toks.add((f"{SIG_GENES[el]}:high" if SIG_GENES[el]=="TMB" else "MSI:MSI", SIG, sign)); continue
                    if el in THERAPY_PREDS:
                        prior_therapy.append(sgn(sign, f"{ent}:{alt}")); continue
                    if not is_gene_symbol(ent):     # snake_case clinical predicate (ki67_index, sensitive_to, ...)
                        other_clinical.append(sgn(sign, f"{ent}:{alt}")); continue
                    b = pottr_bucket(alt)
                    if b == EXPR:
                        expression.append(sgn(sign, f"{ent}:{alt}")); continue
                    for g in POTTR_GENE_ALIASES.get(ent, [ent]):
                        gene_toks.add((g, b, sign))
                    continue
                # no colon: a bare cancer name (catype: omitted) or a status flag
                code = map_catype(atom)
                if code is not None:
                    (c_excl if sign == EXCLUDE else c_incl).add(code)
                elif atom.lower() in CATYPE:
                    pass  # known non-cancer term mapped to None
                else:
                    status_flags.append(sgn(sign, atom))
    return {"c_incl":c_incl,"c_excl":c_excl,"unmapped":unmapped,"gene":gene_toks,"sig":sig_toks,
            "expression":expression,"prior_therapy":prior_therapy,"other_clinical":other_clinical,
            "status_flags":status_flags}

INCL, EXCL = "+", "-"

# ---------------------------------------------------------------- gene / variant tokens
NON_MUT_DESCS = {"amplification", "deletion", "fusion", "disruption"}
GENERIC_MUT = {"oncogenic_mutation", "oncogenic_mutations", "ongenic_mutation", "alteration",
               "mutation", "missense_variant", "tyrosine_kinase_domain_mutation",
               "oncogenic_mutation,germline", "g12_missense_variant", "g13_missense_variant",
               "codon_719_mutation", "alpha-c-helix_mutation"}

def hartwig_gene_tokens(incl, excl):
    """-> list of (gene, descriptor, sign). descriptor = specific variant or bucket word.
    Wildtype[X] flips to the EXCLUDE side (require absence of mutation)."""
    out = []
    for col in (incl, excl):
        for atom in split_top(col, "|&"):
            atom = atom.strip()
            if not atom:
                continue
            sign = INCL
            m = re.match(r"NOT\((.*)\)$", atom, re.S)
            if m:
                atom = m.group(1).strip(); sign = EXCL
            k = re.match(r"([A-Za-z]+)\[(.*)\]$", atom)
            if not k:
                continue
            kind, body = k.group(1), k.group(2)
            genes = re.findall(r"gene(?:Start|End)?=([A-Za-z0-9_-]+)", body)
            if kind == "SmallVariant":
                var = re.search(r"hgvsProteinImpact=p\.([A-Za-z0-9_]+)", body)
                exon = re.search(r"affectedExon=(\d+)", body)
                desc = var.group(1) if var else (f"exon{exon.group(1)}" if exon else "mutation")
                for g in genes: out.append((g, desc, sign))
            elif kind == "Fusion":
                for g in genes: out.append((g, "fusion", sign))
            elif kind == "GainDeletion":
                tm = re.search(r"type=([A-Za-z_]+)", body); typ = tm.group(1) if tm else ""
                d = "deletion" if typ in ("HOM_DEL", "LOSS", "DEL", "LOH", "HET_DEL") else "amplification"
                for g in genes: out.append((g, d, sign))
            elif kind == "Wildtype":
                s = EXCL if sign == INCL else INCL          # wildtype = exclude mutation
                for g in genes: out.append((g, "mutation", s))
            elif kind == "Disruption":
                for g in genes: out.append((g, "disruption", sign))
    return out

def pottr_gene_tokens(rows):
    """-> list of (gene, descriptor, sign) from POTTR gene:alteration atoms (excl expression/sig/predicates)."""
    out = []
    for row in rows:
        for part in re.split(r";", row):
            part = part.strip().lstrip("*").strip()
            if not part:
                continue
            for atom in re.split(r"\bOR\b", part):
                atom = atom.strip().lstrip("*").strip().strip("()").strip()
                if not atom:
                    continue
                sign = INCL
                m = re.match(r"NOT\s+(.*)$", atom, re.I)
                if m:
                    atom = m.group(1).strip(); sign = EXCL
                gm = re.match(r"([A-Za-z0-9_]+):\s*(.+)$", atom)
                if not gm:
                    continue
                ent, alt = gm.group(1), gm.group(2).strip()
                if ent.lower() in SIG_GENES or not is_gene_symbol(ent):
                    continue
                if pottr_bucket(alt) == EXPR:
                    continue
                al = alt.lower()
                if al in GENERIC_MUT:
                    desc = "mutation"
                elif al in ("amplification", "amp", "copy_number_gain", "gain"):
                    desc = "amplification"
                elif al in ("deletion", "loss", "homozygous_deletion", "copy_number_loss"):
                    desc = "deletion"
                elif al == "fusion" or al.endswith("_fusion"):
                    desc = "fusion"
                else:
                    desc = alt  # specific variant (G12C, V600E, exon_20_insertion, ...)
                for g in POTTR_GENE_ALIASES.get(ent, [ent]):
                    out.append((g, desc, sign))
    return out

# ---------------------------------------------------------------- coverage relations
def cover_cancer(a, b):   # mine code a covers pottr code b?
    return rel(a, b) in ("EXACT", "HARTWIG_BROADER")

def cover_eq(a, b):       # exact-equality coverage (gene symbols, signatures)
    return a == b

def cover_va(a, b):       # variant_alteration "GENE:desc": a covers b?
    if a == b:
        return True
    ga, da = a.split(":", 1); gb, db = b.split(":", 1)
    if ga != gb:
        return False
    # generic "mutation" covers any specific point-mutation variant (not amp/del/fusion)
    return da == "mutation" and db not in NON_MUT_DESCS and db != "mutation"

# ---------------------------------------------------------------- verdict
def verdict(mine, pottr, covers, fmt=lambda s: ", ".join(sorted(s)),
           identical_label="identical", missing_label="hartwig-missing"):
    """mine, pottr: sets of display strings. Returns the comparison cell text.
    identical_label tags the match provenance (e.g. 'identical (oncotree match)').
    missing_label is emitted when POTTR has content but hartwig has none (e.g. 'no hartwig curation')."""
    if not pottr:
        return ""                                   # POTTR has no curation here -> blank
    if not mine:
        return f"{missing_label}: {fmt(pottr)}" if missing_label == "hartwig-missing" else missing_label
    pottr_extra = {p for p in pottr if not any(covers(m, p) for m in mine)}   # hartwig is missing these
    mine_extra = {m for m in mine if not any(covers(p, m) for p in pottr)}    # pottr is missing these
    if not pottr_extra and not mine_extra:
        v = identical_label
    elif not pottr_extra:
        v = "pottr_subset"
    elif not mine_extra:
        v = "pottr_superset"
    else:
        return f"diff - pottr missing: {fmt(mine_extra)};\ndiff - hartwig missing: {fmt(pottr_extra)}"
    return f"{v} [pottr: {fmt(pottr)}]"

def split_sign(tokens, key):
    """tokens: iterable of (..., sign). key(tok)->display. Returns (incl_set, excl_set)."""
    inc, exc = set(), set()
    for tok in tokens:
        (exc if tok[-1] == EXCL else inc).add(key(tok))
    return inc, exc

SIG_DISPLAY = {"MSI:MSI": "MSI", "MSI:MSS": "MSS", "TMB:high": "TMB-high", "HRD": "HRD"}
def sig_disp(tok): return SIG_DISPLAY.get(tok[0], tok[0])

# MSI and MSS are the two complementary states of one axis: NOT MSI == MSS, NOT MSS == MSI.
# Fold MS exclusions onto the inclusive side so hartwig `MSS` reconciles with POTTR `NOT MSI`.
MS_COMPLEMENT = {"MSI:MSI": "MSI:MSS", "MSI:MSS": "MSI:MSI"}
def canon_ms(sig_tokens):
    out = set()
    for ent, kind, sign in sig_tokens:
        if sign == EXCL and ent in MS_COMPLEMENT:
            out.add((MS_COMPLEMENT[ent], kind, INCL))
        else:
            out.add((ent, kind, sign))
    return out

def sig_verdict(hsig_set, psig_set):
    """Molecular-signature verdict. `POTTR ⊆ hartwig` (every POTTR signature has an exact hartwig match)
    is reported as identical — hartwig listing extra signature options (e.g. accepting both MSI and MSS)
    does not make it a mismatch when POTTR's requirement is one of them. `no hartwig curation` when
    hartwig has no signature of that polarity."""
    v = verdict(hsig_set, psig_set, cover_eq, missing_label="no hartwig curation")
    if v.startswith("pottr_subset"):
        return "identical" + v[len("pottr_subset"):]
    return v

# ---------------------------------------------------------------- free-text reconciliation
def norm_cancer_text(s):
    """Normalise a cancer free-text so British/US and singular/plural forms collapse
    (e.g. 'Neuroendocrine Tumors' == 'Neuroendocrine tumour', 'Mesothelioma' == 'Mesothelioma')."""
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(tumours|tumour|tumors|tumor)\b", "tumor", s)
    s = re.sub(r"\b(cancers|cancer)\b", "cancer", s)
    s = re.sub(r"\b(neoplasms|neoplasm)\b", "neoplasm", s)
    s = re.sub(r"\b(malignancies|malignancy)\b", "malignancy", s)
    s = re.sub(r"\b(carcinomas|carcinoma)\b", "carcinoma", s)
    return re.sub(r"\s+", " ", s).strip()

def hartwig_condition_texts(cell):
    """Split my `original_health_conditions` (pipe-joined) into individual condition strings."""
    return [x.strip() for x in re.split(r"\s*\|\s*", str(cell)) if x.strip()]

def pottr_catype_texts(rows):
    """Raw POTTR cancer free-text by polarity -> (inclusive_set, exclusive_set).
    Captures both `catype:X` and bare cancer names (e.g. `NOT Breast cancer`), mirroring
    pottr_features so the display + the free-text short-circuit see every cancer atom."""
    inc, exc = set(), set()
    for row in rows:
        for part in re.split(r";", row):
            part = part.strip().lstrip("*").strip()
            for atom in re.split(r"\bOR\b", part):
                atom = atom.strip().lstrip("*").strip().strip("()").strip()
                if not atom:
                    continue
                sign = INCL
                m = re.match(r"NOT\s+(.*)$", atom, re.I)
                if m:
                    atom = m.group(1).strip(); sign = EXCL
                cm = re.match(r"catype:\s*(.+)$", atom, re.I)
                if cm:
                    (exc if sign == EXCL else inc).add(cm.group(1).strip())
                elif ":" not in atom and map_catype(atom) is not None:
                    (exc if sign == EXCL else inc).add(atom)   # bare cancer name (maps to a code)
    return inc, exc

def disp_code(c):
    """Display a cancer code. The OncoTree root code is 'Cancer'; hartwig calls it 'Pan-cancer'."""
    return "Pan-cancer" if c == ROOT else c

def cancer_fmt(s):
    return ", ".join(disp_code(x) for x in sorted(s))

def cancer_incl_verdict(mine_codes, pottr_codes, hartwig_texts, pottr_incl_texts):
    """Cancer inclusive verdict with a free-text short-circuit: if the POTTR catype free-text
    set equals my original-condition free-text set (normalised), the two describe the same cancer
    and any code-set difference is only a mapping-granularity artefact -> identical."""
    P = {norm_cancer_text(x) for x in pottr_incl_texts if norm_cancer_text(x)}
    H = {norm_cancer_text(x) for x in hartwig_texts if norm_cancer_text(x)}
    if P and P == H:
        return f"identical (text match) [pottr: {', '.join(sorted(pottr_incl_texts))}]"
    return verdict(mine_codes, pottr_codes, cover_cancer, fmt=cancer_fmt,
                   identical_label="identical (oncotree match)")

def cancer_excl_verdict(hi, he, pe):
    """Cancer *exclusive* verdict, POTTR-centric. Exactly three categories (+ blank), each followed
    by explanatory note(s) joined by ';\\n' (one category prefix, notes left-aligned):
      identical (oncotree match) -- hartwig's exclusions cover POTTR's exactly (no unmatched either side)
      coverage too broad         -- every uncovered POTTR exclusion is a strict descendant of a BROADER
                                    hartwig-included term (umbrella e.g. Pan-cancer), and hartwig has no
                                    competing exclusion -> hartwig just needs to carve it out
      wrong curation             -- any hard mismatch: POTTR excludes a term hartwig includes at the SAME
                                    granularity (no broader ancestor), or an ancestor-of / out-of-scope
                                    term, or hartwig has an unmatched extra exclusion
    hi/he = hartwig inclusive/exclusive codes; pe = POTTR excluded codes."""
    if not pe:
        return ""                                              # POTTR has no cancer exclusion -> blank
    broad_gap, mismatch = [], []                               # broad_gap: (pe_code, broadest ancestor); mismatch: pe_code
    for x in sorted(pe):
        if any(cover_cancer(h, x) for h in he):
            continue                                           # hartwig already excludes x -> matched
        strict = [h for h in hi if rel(h, x) == "HARTWIG_BROADER"]
        if strict:
            broad_gap.append((x, max(strict, key=lambda h: len(DESC.get(h, ())))))   # broadest included ancestor
        else:
            mismatch.append(x)                                 # exact-only conflict / ancestor-of / out-of-scope
    he_extra = sorted({h for h in he if not any(cover_cancer(h, x) for x in pe)})
    if not broad_gap and not mismatch and not he_extra:
        return f"identical (oncotree match) [pottr excl: {cancer_fmt(pe)}]"
    if mismatch or he_extra:                                   # any hard mismatch -> wrong curation
        notes  = [f"hartwig {disp_code(a)} missing NOT({disp_code(x)})" for x, a in broad_gap]
        notes += [f"hartwig missing NOT({disp_code(x)})" for x in mismatch]
        notes += [f"hartwig wrongly excludes NOT({disp_code(h)})" for h in he_extra]
        return "wrong curation - " + ";\n".join(notes)
    notes = [f"hartwig {disp_code(a)} missing NOT({disp_code(x)})" for x, a in broad_gap]
    return "coverage too broad - " + ";\n".join(notes)

# ---------------------------------------------------------------- POTTR per-criterion display
def signed_disp(inc, exc):
    """Render an inclusive set + an exclusive set into one readable cell."""
    parts = []
    if inc:
        parts.append(", ".join(sorted(inc)))
    if exc:
        parts.append("NOT: " + ", ".join(sorted(exc)))
    return " ; ".join(parts)

# ---------------------------------------------------------------- two-level header output
# (group, column) in display order. Level-0 groups the level-1 columns below it.
COLUMN_GROUPS = [
    ("trial_info", "trialId"),
    ("trial_info", "registry"),
    ("trial_info", "pottr_criteria_count"),
    ("trial_info", "pottr_eligibility_criteria"),
    ("cancer_type", "hartwig_original_health_conditions"),
    ("cancer_type", "hartwig_cancer_type_inclusive"),
    ("cancer_type", "hartwig_cancer_type_exclusive"),
    ("cancer_type", "pottr_cancer_type"),
    ("cancer_type", "cancer_type_inclusive_comparison"),
    ("cancer_type", "cancer_type_exclusive_comparison"),
    ("gene_alteration", "hartwig_gene_alteration_inclusive"),
    ("gene_alteration", "hartwig_gene_alteration_exclusive"),
    ("gene_alteration", "pottr_gene_alteration"),
    ("gene_alteration", "gene_inclusive_comparison"),
    ("gene_alteration", "gene_exclusive_comparison"),
    ("gene_alteration", "variant_alteration_inclusive_comparison"),
    ("gene_alteration", "variant_alteration_exclusive_comparison"),
    ("molecular_signature", "hartwig_molecular_signature_inclusive"),
    ("molecular_signature", "hartwig_molecular_signature_exclusive"),
    ("molecular_signature", "pottr_molecular_signature"),
    ("molecular_signature", "molecular_signature_inclusive_comparison"),
    ("molecular_signature", "molecular_signature_exclusive_comparison"),
    ("prior_therapy", "pottr_prior_therapy"),
    ("expression_ihc", "pottr_expression_ihc"),
]

def write_two_level_tsv(rows, path):
    cols = [c for _, c in COLUMN_GROUPS]
    df = pd.DataFrame(rows)[cols]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\t".join(g for g, _ in COLUMN_GROUPS) + "\n")   # level-0 group header
        f.write("\t".join(cols) + "\n")                           # level-1 column header
        df.to_csv(f, sep="\t", index=False, header=False)
    return df

# ---------------------------------------------------------------- main
def main():
    mine = pd.read_csv(MINE, sep="\t", dtype=str, keep_default_na=False)
    pottr = pd.read_csv(POTTR, sep="\t", dtype=str, keep_default_na=False)
    pagg = defaultdict(list)
    for _, r in pottr.iterrows():
        pagg[r["trial_id"]].append(r["eligibility_criteria"])
    mine_by_id = {str(r["trialId"]).strip().upper(): r for _, r in mine.iterrows()}
    alias = {}
    if os.path.exists(ALIAS):
        a = pd.read_csv(ALIAS, sep="\t", dtype=str, keep_default_na=False); c = list(a.columns)
        for _, r in a.iterrows():
            alias[str(r[c[0]]).strip().upper()] = str(r[c[1]]).strip().upper()

    def resolve(t):
        u = str(t).strip().upper()
        if u in mine_by_id: return mine_by_id[u]
        c = alias.get(u)
        return mine_by_id.get(c) if c else None

    rows = []
    for t in sorted(pagg):
        m = resolve(t)
        if m is None:
            continue
        prows = pagg[t]
        # ---- cancer
        c_inc, _ = mine_cancer_codes(m["cancer_type_inclusive"])
        c_exc, _ = mine_cancer_codes(m["cancer_type_exclusive"])
        # ---- gene / variant (hartwig)
        hg = hartwig_gene_tokens(m["gene_alteration_inclusive"], m["gene_alteration_exclusive"])
        hg_gene_i, hg_gene_e = split_sign(hg, lambda x: x[0])
        hg_va_i, hg_va_e = split_sign(hg, lambda x: f"{x[0]}:{x[1]}")
        # ---- molecular signature (hartwig) via v1
        hsig = canon_ms({x for x in mine_features(m["molecular_signature_inclusive"], m["molecular_signature_exclusive"]) if x[1] == SIG})
        hsig_i, hsig_e = split_sign(hsig, sig_disp)
        # ---- POTTR
        P = pottr_features(prows)
        pg = pottr_gene_tokens(prows)
        pg_gene_i, pg_gene_e = split_sign(pg, lambda x: x[0])
        pg_va_i, pg_va_e = split_sign(pg, lambda x: f"{x[0]}:{x[1]}")
        psig_i, psig_e = split_sign(canon_ms(P["sig"]), sig_disp)
        # prior therapy / expression: split POTTR display strings by leading "NOT "
        def split_not(items):
            inc = {s for s in items if not s.startswith("NOT ")}
            exc = {s[4:] for s in items if s.startswith("NOT ")}
            return inc, exc
        pt_i, pt_e = split_not(P["prior_therapy"])
        ex_i, ex_e = split_not(P["expression"])
        # ---- free-text (for the cancer short-circuit + the pottr_cancer_type column)
        p_cat_i, p_cat_e = pottr_catype_texts(prows)
        h_cond_texts = hartwig_condition_texts(m["original_health_conditions"])
        # ---- gene / variant verdicts: 'no hartwig curation' when hartwig has no gene of that
        # polarity (vs a genuine diff when it does); compare variant_alteration ONLY when the gene
        # level is identical, else leave it blank (a gene-level diff makes variant detail moot).
        gene_i = verdict(hg_gene_i, pg_gene_i, cover_eq, missing_label="no hartwig curation")
        gene_e = verdict(hg_gene_e, pg_gene_e, cover_eq, missing_label="no hartwig curation")
        va_i = verdict(hg_va_i, pg_va_i, cover_va, missing_label="no hartwig curation") if gene_i.startswith("identical") else ""
        va_e = verdict(hg_va_e, pg_va_e, cover_va, missing_label="no hartwig curation") if gene_e.startswith("identical") else ""

        rows.append({
            "trialId": t, "registry": m["registry"],
            "pottr_criteria_count": len(prows),
            "pottr_eligibility_criteria": "  ||  ".join(prows),
            # -- cancer_type
            "hartwig_original_health_conditions": m["original_health_conditions"],
            "hartwig_cancer_type_inclusive": m["cancer_type_inclusive"],
            "hartwig_cancer_type_exclusive": m["cancer_type_exclusive"],
            "pottr_cancer_type": signed_disp(p_cat_i, p_cat_e),
            "cancer_type_inclusive_comparison": cancer_incl_verdict(c_inc, P["c_incl"], h_cond_texts, p_cat_i),
            "cancer_type_exclusive_comparison": cancer_excl_verdict(c_inc, c_exc, P["c_excl"]),
            # -- gene_alteration (gene = symbol-level, variant = specific change)
            "hartwig_gene_alteration_inclusive": m["gene_alteration_inclusive"],
            "hartwig_gene_alteration_exclusive": m["gene_alteration_exclusive"],
            "pottr_gene_alteration": signed_disp(pg_va_i, pg_va_e),
            "gene_inclusive_comparison": gene_i,
            "gene_exclusive_comparison": gene_e,
            "variant_alteration_inclusive_comparison": va_i,
            "variant_alteration_exclusive_comparison": va_e,
            # -- molecular_signature
            "hartwig_molecular_signature_inclusive": m["molecular_signature_inclusive"],
            "hartwig_molecular_signature_exclusive": m["molecular_signature_exclusive"],
            "pottr_molecular_signature": signed_disp(psig_i, psig_e),
            "molecular_signature_inclusive_comparison": sig_verdict(hsig_i, psig_i),
            "molecular_signature_exclusive_comparison": sig_verdict(hsig_e, psig_e),
            # -- prior_therapy / expression_ihc: POTTR-only dimensions; hartwig models neither, so the
            # comparison is always trivially hartwig-missing. Per user, drop the _comparison cols and
            # keep only the pottr_* columns (informational: what POTTR requires that hartwig doesn't).
            "pottr_prior_therapy": signed_disp(pt_i, pt_e),
            "pottr_expression_ihc": signed_disp(ex_i, ex_e),
        })
    out = write_two_level_tsv(rows, OUT)
    print(f"trials compared: {len(out)}")
    for col in [c for c in out.columns if c.endswith("_comparison")]:
        vc = out[col].map(lambda s: s.split(" ")[0].split("-")[0] if s else "(blank)").value_counts()
        print(f"\n[{col}]\n{vc.to_string()}")
    print(f"\nwrote {OUT}")

if __name__ == "__main__":
    main()
