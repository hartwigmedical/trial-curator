from __future__ import annotations

import argparse
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

LOGGER = logging.getLogger(__name__)

CANONICAL_CLASS_ORDER: List[str] = ["SmallVariant", "GainDeletion", "Disruption", "Fusion"]


# =========================
# Errors / helpers
# =========================

class MappingError(RuntimeError):
    pass


def normalize_text(v: object) -> str:
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()


def is_blank(v: object) -> bool:
    s = normalize_text(v)
    return s == "" or s == "_" or s.lower() == "nan"


def clean_bool(v: object) -> bool:
    if v is None:
        return False
    return str(v).strip().upper() == "TRUE"


def split_top_level(s: str, sep: str) -> List[str]:
    """
    Split by `sep` only at parentheses depth 0.
    """
    s = s or ""
    parts: List[str] = []
    buf: List[str] = []
    depth = 0

    for ch in s:
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == sep and depth == 0:
            part = "".join(buf).strip()
            if part:
                parts.append(part)
            buf = []
        else:
            buf.append(ch)

    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def split_commas(s: str) -> List[str]:
    s = normalize_text(s)
    if is_blank(s):
        return []
    return [p.strip() for p in split_top_level(s, ",") if p.strip()]


def split_ands(s: str) -> List[str]:
    return [p.strip() for p in split_top_level(s or "", "&") if p.strip()]


def broadcast_or_zip(components: List[List[str]], n: int) -> List[List[str]]:
    """
    v3 behavior: broadcast/pad/truncate with warning (do not crash).
      - len==0 -> blanks
      - len==1 -> broadcast
      - len==n -> zip
      - else -> pad/truncate (warn)
    """
    out: List[List[str]] = []
    for comp in components:
        if len(comp) == 0:
            out.append([""] * n)
        elif len(comp) == 1:
            out.append([comp[0]] * n)
        elif len(comp) == n:
            out.append(comp)
        else:
            LOGGER.warning(
                "Component length mismatch; padding/truncating. got=%d expected=%d values=%s",
                len(comp), n, comp,
            )
            x = comp[:n]
            while len(x) < n:
                x.append(comp[-1])
            out.append(x)
    return out


# =========================
# Boolean AST
# =========================

@dataclass(frozen=True)
class Expr:
    pass


@dataclass(frozen=True)
class Atom(Expr):
    token: str


@dataclass(frozen=True)
class Not(Expr):
    child: Expr


@dataclass(frozen=True)
class And(Expr):
    left: Expr
    right: Expr


@dataclass(frozen=True)
class Or(Expr):
    left: Expr
    right: Expr


# =========================
# Parser
# =========================

_TOKEN_RE = re.compile(
    r"""
    (?P<LPAREN>\() |
    (?P<RPAREN>\)) |
    (?P<AND>\bAND\b|&) |
    (?P<OR>\bOR\b|\|) |
    (?P<NOT>\bNOT\b|!) |
    (?P<ATOM>[A-Za-z0-9_.]+)
    """,
    flags=re.VERBOSE | re.IGNORECASE,
)


def tokenize(s: str) -> List[str]:
    s = normalize_text(s)
    if not s:
        return []
    toks: List[str] = []
    for m in _TOKEN_RE.finditer(s):
        kind = m.lastgroup
        val = m.group(kind) if kind else ""
        if not kind or not val:
            continue
        toks.append(val)
    return toks


class _Parser:
    def __init__(self, toks: List[str]):
        self.toks = toks
        self.i = 0

    def peek(self) -> Optional[str]:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def pop(self) -> str:
        if self.i >= len(self.toks):
            raise MappingError("Unexpected end of tokens")
        t = self.toks[self.i]
        self.i += 1
        return t

    def parse(self) -> Expr:
        expr = self.parse_or()
        if self.peek() is not None:
            raise MappingError(f"Unexpected token: {self.peek()}")
        return expr

    def parse_or(self) -> Expr:
        left = self.parse_and()
        while True:
            t = self.peek()
            if t is None:
                break
            if t.upper() == "OR" or t == "|":
                self.pop()
                right = self.parse_and()
                left = Or(left, right)
                continue
            break
        return left

    def parse_and(self) -> Expr:
        left = self.parse_not()
        while True:
            t = self.peek()
            if t is None:
                break
            if t.upper() == "AND" or t == "&":
                self.pop()
                right = self.parse_not()
                left = And(left, right)
                continue
            break
        return left

    def parse_not(self) -> Expr:
        t = self.peek()
        if t is not None and (t.upper() == "NOT" or t == "!"):
            self.pop()
            child = self.parse_not()
            return Not(child)
        return self.parse_atom()

    def parse_atom(self) -> Expr:
        t = self.peek()
        if t is None:
            raise MappingError("Unexpected end while parsing atom")
        if t == "(":
            self.pop()
            inner = self.parse_or()
            if self.pop() != ")":
                raise MappingError("Expected ')'")
            return inner
        if t == ")":
            raise MappingError("Unexpected ')'")
        tok = self.pop()
        return Atom(normalize_model_token(tok))


def parse_model_expr(s: str) -> Expr:
    toks = tokenize(s)
    if not toks:
        raise MappingError("Empty FindingsModel_curation expression")
    return _Parser(toks).parse()


# =========================
# Expr utilities
# =========================

def expr_contains_token(expr: Expr, token: str) -> bool:
    token_norm = normalize_model_token(token)
    if isinstance(expr, Atom):
        return normalize_model_token(expr.token) == token_norm
    if isinstance(expr, Not):
        return expr_contains_token(expr.child, token)
    if isinstance(expr, And) or isinstance(expr, Or):
        return expr_contains_token(expr.left, token) or expr_contains_token(expr.right, token)
    raise MappingError(f"Unknown Expr type: {type(expr)}")


def extract_positive_atoms(expr: Expr) -> List[str]:
    out: List[str] = []

    def rec(e: Expr, neg: bool) -> None:
        if isinstance(e, Atom):
            if not neg:
                out.append(normalize_model_token(e.token))
            return
        if isinstance(e, Not):
            rec(e.child, not neg)
            return
        if isinstance(e, And) or isinstance(e, Or):
            rec(e.left, neg)
            rec(e.right, neg)
            return
        raise MappingError(f"Unknown Expr type: {type(e)}")

    rec(expr, False)
    # preserve order, unique
    seen = set()
    uniq: List[str] = []
    for t in out:
        if t in seen:
            continue
        seen.add(t)
        uniq.append(t)
    return uniq


# =========================
# NNF / De Morgan
# =========================

def to_nnf(expr: Expr) -> Expr:
    if isinstance(expr, Atom):
        return expr
    if isinstance(expr, Not):
        child = expr.child
        if isinstance(child, Atom):
            return expr
        if isinstance(child, Not):
            return to_nnf(child.child)
        if isinstance(child, And):
            return Or(to_nnf(Not(child.left)), to_nnf(Not(child.right)))
        if isinstance(child, Or):
            return And(to_nnf(Not(child.left)), to_nnf(Not(child.right)))
        return Not(to_nnf(child))
    if isinstance(expr, And):
        return And(to_nnf(expr.left), to_nnf(expr.right))
    if isinstance(expr, Or):
        return Or(to_nnf(expr.left), to_nnf(expr.right))
    raise MappingError(f"Unknown Expr type: {type(expr)}")


# =========================
# Token normalization
# =========================

# New spec: GermlineSmallVariant == SmallVariant
# Robustness: keyword-ish tokens sometimes appear as standalone atoms.
_CLASS_MAP = {
    "smallvariant": "SmallVariant",
    "gaindeletion": "GainDeletion",
    "disruption": "Disruption",
    "fusion": "Fusion",
    "arm": "Arm",
    "virus": "Virus",
    "pharmocogenotype": "PharmocoGenotype",
    "germlinesmallvariant": "SmallVariant",
    "splice": "SmallVariant",
    "insertion": "SmallVariant",
    "nonsense": "SmallVariant",
}


def normalize_model_token(token: str) -> str:
    t = token.strip()
    parts = t.split(".")
    if not parts:
        return t

    cls = _CLASS_MAP.get(parts[0].lower(), parts[0])

    if len(parts) == 1:
        return cls

    if parts[1].lower() == "type":
        qual = parts[2].upper() if len(parts) >= 3 else ""
        return f"{cls}.type.{qual}".rstrip(".")

    return ".".join([cls] + parts[1:])


# =========================
# Atomic mapping
# =========================

# v3 patterns
HGVS_PROTEIN_RE = re.compile(r"\bp\.[A-Za-z0-9_?*]+")
EXON_RE = re.compile(r"\bexon\s+(\d+)\b", re.IGNORECASE)


def format_args_kv(pairs: List[Tuple[str, str]]) -> str:
    return " & ".join(f"{k}={v}" for k, v in pairs)


def infer_gaindel_type_from_variant_curation(variant_curation: str) -> Optional[str]:
    # v3 behavior
    v = (variant_curation or "").lower()
    if "deletion" in v or "loss" in v:
        return "SOMATIC_DEL"
    if "amplification" in v or "copy number gain" in v or "cn gain" in v:
        return "SOMATIC_GAIN"
    return None


def infer_arm_type(variant_curation: str) -> str:
    v = (variant_curation or "").lower()
    return "ARM_GAIN" if any(x in v for x in ("amplification", "copy number gain", "trisomy")) else "ARM_LOSS"


def parse_arm_locus(locus: str) -> Dict[str, str]:
    s = locus.strip()
    s = re.sub(r"(\d+[pq]\d+)\.\d+$", r"\1", s, flags=re.IGNORECASE)  # ignore sub-band
    m = re.match(r"^(\d+)\s*([pq])?\s*([0-9]+)?$", s, flags=re.IGNORECASE)
    if not m:
        raise MappingError(f"Unparsable Arm locus: {locus!r}")

    chrom = m.group(1)
    arm = m.group(2).lower() if m.group(2) else None
    digits = m.group(3)

    out: Dict[str, str] = {"chromosome": chrom}
    if arm:
        out["arm"] = arm
    if digits and len(digits) >= 2:
        out["region"] = digits[0]
        out["band"] = digits[1]
    return out


def map_fusion_with_flags(gene: str, fusion_both: bool, fusion_5: bool, fusion_3: bool) -> str:
    """
    v3 behavior:
      - pair gene "A_B": if fusion_both -> Fusion[geneStart=A & geneEnd=B], else still ordered-pair mapping
      - single gene:
          both 5' and 3' -> (Fusion[geneStart=G] | Fusion[geneEnd=G])
          only 5' -> Fusion[geneStart=G]
          only 3' -> Fusion[geneEnd=G]
          else -> Fusion[geneStart=G | geneEnd=G]
    """
    gene = gene.strip()

    if "_" in gene:
        left, right = [x.strip() for x in gene.split("_", 1)]
        if fusion_both:
            return f"Fusion[geneStart={left} & geneEnd={right}]"
        return f"Fusion[geneStart={left} & geneEnd={right}]"

    if fusion_5 and fusion_3:
        return f"(Fusion[geneStart={gene}] | Fusion[geneEnd={gene}])"
    if fusion_5 and not fusion_3:
        return f"Fusion[geneStart={gene}]"
    if fusion_3 and not fusion_5:
        return f"Fusion[geneEnd={gene}]"
    return f"Fusion[geneStart={gene} | geneEnd={gene}]"


def map_arm_atom(gene_locus_expr: str, variant_curation: str, arm_type_override: Optional[str] = None) -> str:
    loci = [x.strip() for x in split_ands(gene_locus_expr)]
    arm_type = arm_type_override or infer_arm_type(variant_curation)

    locus_chunks: List[str] = []
    for loc in loci:
        fields = parse_arm_locus(loc)

        # chromosome-only + ARM_GAIN + single-locus -> expand p/q
        if "arm" not in fields and arm_type == "ARM_GAIN" and len(loci) == 1:
            chrom = fields["chromosome"]
            left = format_args_kv([("chromosome", chrom), ("arm", "p"), ("type", arm_type)])
            right = format_args_kv([("chromosome", chrom), ("arm", "q"), ("type", arm_type)])
            return f"Arm[{left}] | Arm[{right}]"

        kv: List[Tuple[str, str]] = [("chromosome", fields["chromosome"])]
        if "arm" in fields:
            kv.append(("arm", fields["arm"]))
        if "region" in fields:
            kv.append(("region", fields["region"]))
        if "band" in fields:
            kv.append(("band", fields["band"]))
        kv.append(("type", arm_type))

        locus_chunks.append(format_args_kv(kv))

    if len(locus_chunks) == 1:
        return f"Arm[{locus_chunks[0]}]"

    inner = " & ".join(f"({chunk})" for chunk in locus_chunks)
    return f"Arm[{inner}]"


def map_smallvariant_from_variant_curation(gene: str, variant_curation: str) -> str:
    """
    v3 behavior (restored):
      - Always includes gene=...
      - Extract HGVS protein -> transcriptImpact.hgvsProteinImpact
      - Extract exon -> transcriptImpact.affectedExon
      - If exon + insertion/deletion -> transcriptImpact.effects INFRAME_INSERTION/INFRAME_DELETION
    """
    pairs: List[Tuple[str, str]] = [("gene", gene)]
    v = variant_curation or ""
    v_lc = v.lower()

    m = HGVS_PROTEIN_RE.search(v)
    if m:
        pairs.append(("transcriptImpact.hgvsProteinImpact", m.group(0)))

    m2 = EXON_RE.search(v)
    exon_num: Optional[str] = m2.group(1) if m2 else None
    if exon_num:
        pairs.append(("transcriptImpact.affectedExon", exon_num))

    if exon_num and "insertion" in v_lc:
        pairs.append(("transcriptImpact.effects", "INFRAME_INSERTION"))
    elif exon_num and "deletion" in v_lc:
        pairs.append(("transcriptImpact.effects", "INFRAME_DELETION"))

    return f"SmallVariant[{format_args_kv(pairs)}]"


def map_atom_to_args(
    atom_token: str,
    gene: str,
    variant_curation: str,
    *,
    fusion_both: bool,
    fusion_5: bool,
    fusion_3: bool,
    allow_fusion_flags: bool,
) -> str:
    token = normalize_model_token(atom_token)

    forced_arm_type: Optional[str] = None
    if token.startswith("Arm.type."):
        forced_arm_type = token.split(".")[-1].upper()
        cls = "Arm"
    else:
        cls = token.split(".")[0]

    forced_gd_type: Optional[str] = None
    if token.startswith("GainDeletion.type."):
        forced_gd_type = token.split(".")[-1].upper()
        cls = "GainDeletion"

    if cls == "Virus":
        return f"Virus[{format_args_kv([('name', gene)])}]"

    # New spec (1): PharmocoGenotype with allele when present
    if cls == "PharmocoGenotype":
        pairs: List[Tuple[str, str]] = [("gene", gene)]
        if not is_blank(variant_curation):
            pairs.append(("allele", normalize_text(variant_curation)))
        return f"PharmocoGenotype[{format_args_kv(pairs)}]"

    if cls == "Disruption":
        return f"Disruption[{format_args_kv([('gene', gene)])}]"

    if cls == "GainDeletion":
        pairs: List[Tuple[str, str]] = [("gene", gene)]
        typ = forced_gd_type or infer_gaindel_type_from_variant_curation(variant_curation)
        if typ:
            pairs.append(("type", typ))
        return f"GainDeletion[{format_args_kv(pairs)}]"

    if cls == "SmallVariant":
        return map_smallvariant_from_variant_curation(gene, variant_curation)

    if cls == "Fusion":
        if not allow_fusion_flags:
            return map_fusion_with_flags(gene, fusion_both=True, fusion_5=False, fusion_3=False)
        return map_fusion_with_flags(gene, fusion_both=fusion_both, fusion_5=fusion_5, fusion_3=fusion_3)

    if cls == "Arm":
        return map_arm_atom(gene, variant_curation, arm_type_override=forced_arm_type)

    raise MappingError(f"Unknown model token: {atom_token} (normalized={token})")


def render_expr_boolean(
    expr: Expr,
    gene: str,
    variant: str,
    *,
    fusion_both: bool,
    fusion_5: bool,
    fusion_3: bool,
    allow_fusion_flags: bool,
) -> str:
    if isinstance(expr, Atom):
        return map_atom_to_args(
            expr.token,
            gene,
            variant,
            fusion_both=fusion_both,
            fusion_5=fusion_5,
            fusion_3=fusion_3,
            allow_fusion_flags=allow_fusion_flags,
        )
    if isinstance(expr, Not):
        child = render_expr_boolean(
            expr.child,
            gene,
            variant,
            fusion_both=fusion_both,
            fusion_5=fusion_5,
            fusion_3=fusion_3,
            allow_fusion_flags=allow_fusion_flags,
        )
        return f"NOT({child})"
    if isinstance(expr, And):
        left = render_expr_boolean(
            expr.left,
            gene,
            variant,
            fusion_both=fusion_both,
            fusion_5=fusion_5,
            fusion_3=fusion_3,
            allow_fusion_flags=allow_fusion_flags,
        )
        right = render_expr_boolean(
            expr.right,
            gene,
            variant,
            fusion_both=fusion_both,
            fusion_5=fusion_5,
            fusion_3=fusion_3,
            allow_fusion_flags=allow_fusion_flags,
        )
        return f"{left} & {right}"
    if isinstance(expr, Or):
        left = render_expr_boolean(
            expr.left,
            gene,
            variant,
            fusion_both=fusion_both,
            fusion_5=fusion_5,
            fusion_3=fusion_3,
            allow_fusion_flags=allow_fusion_flags,
        )
        right = render_expr_boolean(
            expr.right,
            gene,
            variant,
            fusion_both=fusion_both,
            fusion_5=fusion_5,
            fusion_3=fusion_3,
            allow_fusion_flags=allow_fusion_flags,
        )
        return f"{left} | {right}"

    raise MappingError(f"Unknown Expr type: {type(expr)}")


# =========================
# Dedup / ordering for OR-unions (same as v3 intent)
# =========================

def class_of_expr(expr: str) -> str:
    m = re.match(r"^([A-Za-z0-9_]+)\[", expr.strip())
    if not m:
        return ""
    return normalize_model_token(m.group(1))


def sort_by_preferred_order(exprs: List[str]) -> List[str]:
    order: Dict[str, int] = {c: i for i, c in enumerate(CANONICAL_CLASS_ORDER)}

    def key_fn(e: str) -> Tuple[int, str]:
        c = class_of_expr(e)
        return (order.get(c, 999), e)

    return sorted(exprs, key=key_fn)


def is_more_detailed(a: str, b: str) -> bool:
    def score(s: str) -> int:
        s = s.strip()
        if "[" not in s or "]" not in s:
            return 0
        inside = s[s.find("[") + 1 : s.rfind("]")]
        return inside.count("&") + (1 if inside.strip() else 0)

    return score(a) > score(b)


def dedupe_keep_most_detailed(exprs: List[str]) -> List[str]:
    out: List[str] = []
    best: Dict[Tuple[str, str], str] = {}

    gene_re = re.compile(r"\bgene=([^ &\]]+)")
    for e in exprs:
        cls = class_of_expr(e)
        m = gene_re.search(e)
        gene = m.group(1) if m else ""
        k = (cls, gene)
        prev = best.get(k)
        if prev is None:
            best[k] = e
        else:
            if is_more_detailed(e, prev):
                best[k] = e

    seen = set()
    for e in exprs:
        cls = class_of_expr(e)
        m = gene_re.search(e)
        gene = m.group(1) if m else ""
        k = (cls, gene)
        chosen = best.get(k, e)
        if chosen in seen:
            continue
        seen.add(chosen)
        out.append(chosen)

    return out


def token_to_class_expr(
    token_norm: str,
    gene: str,
    variant: str,
    *,
    fusion_both: bool,
    fusion_5: bool,
    fusion_3: bool,
    allow_fusion_flags: bool,
) -> str:
    return map_atom_to_args(
        token_norm,
        gene,
        variant,
        fusion_both=fusion_both,
        fusion_5=fusion_5,
        fusion_3=fusion_3,
        allow_fusion_flags=allow_fusion_flags,
    )


# =========================
# Keyword-triggered augmentations (new spec #3)
# =========================

def keyword_smallvariant_constraints(variant_curation: str, model_expr_str: str) -> List[str]:
    hay = f"{normalize_text(variant_curation)} {normalize_text(model_expr_str)}".lower()
    out: List[str] = []
    if "insertion" in hay:
        out.append("SmallVariant[transcriptImpact.effects=INFRAME_INSERTION]")
    if "splice" in hay:
        out.append("SmallVariant[inSpliceRegion]")
    if "nonsense" in hay:
        out.append("SmallVariant[transcriptImpact.codingEffect=NONSENSE_OR_FRAMESHIFT]")
    return out


def append_or_terms(expr: str, extra_terms: List[str]) -> str:
    extra_terms = [t.strip() for t in extra_terms if t and t.strip()]
    if not extra_terms:
        return expr.strip()

    base = expr.strip()
    if not base:
        return " | ".join(extra_terms)

    needs_wrap = (" & " in base) or base.startswith("NOT(") or (
        " | " in base and not (base.startswith("(") and base.endswith(")"))
    )
    if needs_wrap and not (base.startswith("(") and base.endswith(")")):
        base = f"({base})"

    return " | ".join([base] + extra_terms)


# =========================
# Core row mapping
# =========================

def map_row_to_args(row: pd.Series) -> str:
    gene_raw = normalize_text(row.get("Gene_curation", ""))
    model_raw = normalize_text(row.get("FindingsModel_curation", ""))

    if is_blank(gene_raw) or is_blank(model_raw):
        return ""

    var_raw = normalize_text(row.get("Variant_curation", ""))
    gene_type_raw = normalize_text(row.get("Gene_type", ""))
    fb_raw = normalize_text(row.get("Fusion_both", ""))
    f5_raw = normalize_text(row.get("Fusion_FivePrime", ""))
    f3_raw = normalize_text(row.get("Fusion_ThreePrime", ""))

    genes = split_commas(gene_raw)
    vars_ = split_commas(var_raw)
    models = split_commas(model_raw)
    gene_types = split_commas(gene_type_raw)
    fb = split_commas(fb_raw)
    f5 = split_commas(f5_raw)
    f3 = split_commas(f3_raw)

    n = max(len(genes), len(vars_), len(models), len(gene_types), len(fb), len(f5), len(f3), 1)
    genes, vars_, models, gene_types, fb, f5, f3 = broadcast_or_zip(
        [genes, vars_, models, gene_types, fb, f5, f3],
        n,
    )

    out_components: List[str] = []

    for i in range(n):
        gene = genes[i].strip()
        variant = vars_[i].strip()
        model_expr_str = models[i].strip()
        gene_type = gene_types[i].strip().upper()

        if is_blank(gene) or is_blank(model_expr_str):
            continue

        fusion_both = clean_bool(fb[i])
        fusion_5 = clean_bool(f5[i])
        fusion_3 = clean_bool(f3[i])

        variant_nonblank = not is_blank(variant)

        keyword_terms = keyword_smallvariant_constraints(variant, model_expr_str)

        model_ast = parse_model_expr(model_expr_str)
        top_not_mode = isinstance(model_ast, Not)

        if top_not_mode:
            nnf = to_nnf(model_ast)
            allow_fusion_flags = expr_contains_token(model_ast, "Fusion")
            comp_expr = render_expr_boolean(
                nnf,
                gene,
                variant,
                fusion_both=fusion_both,
                fusion_5=fusion_5,
                fusion_3=fusion_3,
                allow_fusion_flags=allow_fusion_flags,
            )
            comp_expr = append_or_terms(comp_expr, keyword_terms)
        else:
            if variant_nonblank:
                allow_fusion_flags = expr_contains_token(model_ast, "Fusion")
                comp_expr = render_expr_boolean(
                    model_ast,
                    gene,
                    variant,
                    fusion_both=fusion_both,
                    fusion_5=fusion_5,
                    fusion_3=fusion_3,
                    allow_fusion_flags=allow_fusion_flags,
                )
                comp_expr = append_or_terms(comp_expr, keyword_terms)
            else:
                tokens_pos = extract_positive_atoms(model_ast)
                exprs: List[str] = []
                for t in tokens_pos:
                    exprs.append(
                        token_to_class_expr(
                            t,
                            gene,
                            variant,
                            fusion_both=fusion_both,
                            fusion_5=fusion_5,
                            fusion_3=fusion_3,
                            allow_fusion_flags=True,
                        )
                    )

                # new spec #3: always add keyword-triggered constraints
                exprs.extend(keyword_terms)

                # new spec #2 (revised): suppress Gene_type expansion ONLY when exactly "Fusion"
                suppress_gene_type_expansion = normalize_text(model_expr_str) == "Fusion"
                if not suppress_gene_type_expansion:
                    if gene_type == "TSG":
                        exprs.append(f"SmallVariant[gene={gene}]")
                        exprs.append(f"GainDeletion[gene={gene} & type=SOMATIC_DEL]")
                        exprs.append(f"Disruption[gene={gene}]")
                    elif gene_type == "ONCO":
                        exprs.append(f"SmallVariant[gene={gene}]")
                        exprs.append(f"GainDeletion[gene={gene} & type=SOMATIC_GAIN]")
                        if fusion_both or fusion_5 or fusion_3:
                            exprs.append(map_fusion_with_flags(gene, fusion_both, fusion_5, fusion_3))

                exprs = dedupe_keep_most_detailed(exprs)
                exprs = sort_by_preferred_order(exprs)
                comp_expr = " | ".join(exprs)

        comp_expr = comp_expr.strip()
        if not comp_expr:
            continue

        if n > 1 and (" | " in comp_expr or " & " in comp_expr) and not (
            comp_expr.startswith("(") and comp_expr.endswith(")")
        ):
            comp_expr = f"({comp_expr})"

        out_components.append(comp_expr)

    return ", ".join(out_components)


# =========================
# Post-processing cleanup (new spec #5)
# =========================

_OR_TOKEN_RE = re.compile(r"\bOR\b", flags=re.IGNORECASE)


def _normalize_operator_spacing(s: str) -> str:
    s = re.sub(r"\s*\|\s*", " | ", s)
    s = re.sub(r"\s*&\s*", " & ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


import re
from typing import List

_OR_TOKEN_RE = re.compile(r"\bOR\b", flags=re.IGNORECASE)

def _normalize_operator_spacing(s: str) -> str:
    s = re.sub(r"\s*\|\s*", " | ", s)
    s = re.sub(r"\s*&\s*", " & ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _is_wrapped_by_parens(s: str) -> bool:
    s = s.strip()
    if len(s) < 2 or not (s.startswith("(") and s.endswith(")")):
        return False
    depth = 0
    for i, ch in enumerate(s):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0 and i != len(s) - 1:
                # outer parens close before end => not a single wrapping pair
                return False
        if depth < 0:
            return False
    return depth == 0

def _strip_outer_parens(s: str) -> str:
    s = s.strip()
    while _is_wrapped_by_parens(s):
        s = s[1:-1].strip()
    return s

def _split_top_level(s: str, sep: str) -> List[str]:
    """
    Split on `sep` only when not inside parentheses.
    """
    out: List[str] = []
    buf: List[str] = []
    depth = 0
    for ch in s:
        if ch == "(":
            depth += 1
            buf.append(ch)
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            buf.append(ch)
            continue
        if ch == sep and depth == 0:
            part = "".join(buf).strip()
            if part:
                out.append(part)
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out

def _flatten_or_terms(expr: str) -> List[str]:
    """
    Turn something like:
      (A | B), (C | (D | E))
    into flat list:
      [A, B, C, D, E]
    """
    expr = _normalize_operator_spacing(expr)
    expr = _strip_outer_parens(expr)

    # First: treat top-level commas as OR separators
    comma_parts = _split_top_level(expr, ",")
    if len(comma_parts) > 1:
        terms: List[str] = []
        for p in comma_parts:
            terms.extend(_flatten_or_terms(p))
        return terms

    # Then: split on top-level |
    bar_parts = _split_top_level(expr, "|")
    if len(bar_parts) > 1:
        terms = []
        for p in bar_parts:
            terms.extend(_flatten_or_terms(p))
        return terms

    # Base case: single term (might still be wrapped)
    term = _strip_outer_parens(expr)
    term = _normalize_operator_spacing(term)
    return [term] if term else []

def postprocess_args_mapping(args_str: str) -> str:
    """
    Post-process mapping string to:
      - Convert OR -> |
      - Convert top-level commas -> |
      - Flatten redundant parentheses
      - Remove duplicate OR-terms globally (string-equal after normalization)
    Final output contains NO commas.
    """
    s = "" if args_str is None else str(args_str)
    s = s.strip()
    if s == "" or s == "_" or s.lower() == "nan":
        return ""

    # OR -> |
    s = _OR_TOKEN_RE.sub("|", s)
    s = _normalize_operator_spacing(s)

    # Flatten into OR terms
    terms = _flatten_or_terms(s)

    # Global dedupe after normalization
    seen = set()
    deduped: List[str] = []
    for t in terms:
        nt = _normalize_operator_spacing(t)
        if not nt:
            continue
        if nt in seen:
            continue
        seen.add(nt)
        deduped.append(nt)

    return " | ".join(deduped)


def main():
    parser = argparse.ArgumentParser(
        description="Generate Args column for GeneAlteration mapping."
    )

    parser.add_argument("--input_xlsx", required=True)
    parser.add_argument("--output_dir", required=True)
    parser.add_argument("--sheet_name", required=True)
    parser.add_argument(
        "--log_level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    input_path = Path(args.input_xlsx)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    LOGGER.info("Reading workbook: %s (sheet=%s)", input_path, args.sheet_name)
    df = pd.read_excel(input_path, sheet_name=args.sheet_name)

    LOGGER.info("Generating Args (overwriting Args by default)...")
    df["Args"] = df.apply(map_row_to_args, axis=1)

    LOGGER.info("Post-processing Args (OR->|, dedupe)...")
    df["Args_postprocessed"] = df["Args"].apply(postprocess_args_mapping)

    output_path = output_dir / f"{input_path.stem}_mapped.xlsx"

    LOGGER.info("Writing output: %s", output_path)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=args.sheet_name, index=False)

    LOGGER.info("Done. Output written to %s", output_path)


if __name__ == "__main__":
    main()
