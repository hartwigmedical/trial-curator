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


class MappingError(ValueError):
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
    return split_top_level(s, ",")


def split_ands(s: str) -> List[str]:
    return split_top_level(s, "&")


def broadcast_or_zip(components: List[List[str]], n: int) -> List[List[str]]:
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
                len(comp),
                n,
                comp,
            )
            x = comp[:n]
            while len(x) < n:
                x.append(comp[-1])
            out.append(x)
    return out


# =========================
# FindingsModel AST
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


_TOKEN_RE = re.compile(
    r"""
    \s*
    (
        NOT\b
      | \(
      | \)
      | \|
      | &
      | [A-Za-z][A-Za-z0-9_.]*
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)


def tokenize_model_expr(s: str) -> List[str]:
    if not s:
        raise MappingError("Empty FindingsModel_curation expression cannot be tokenized.")
    tokens: List[str] = []
    pos = 0
    while pos < len(s):
        m = _TOKEN_RE.match(s, pos)
        if not m:
            raise MappingError(f"Cannot tokenize FindingsModel_curation near: {s[pos:pos+40]!r}")
        tok = m.group(1)
        pos = m.end()
        tokens.append("NOT" if tok.upper() == "NOT" else tok)
    return tokens


def parse_model_expr(s: str) -> Expr:
    toks = tokenize_model_expr(s)
    i = 0

    def parse_primary() -> Expr:
        nonlocal i
        if i >= len(toks):
            raise MappingError("Unexpected end of FindingsModel_curation expression.")
        t = toks[i]

        if t == "NOT":
            i += 1
            return Not(parse_primary())

        if t == "(":
            i += 1
            node = parse_or()
            if i >= len(toks) or toks[i] != ")":
                raise MappingError("Unmatched '(' in FindingsModel_curation.")
            i += 1
            return node

        if t in {")", "&", "|"}:
            raise MappingError(f"Unexpected token {t!r} in FindingsModel_curation.")

        i += 1
        return Atom(t)

    def parse_and() -> Expr:
        nonlocal i
        node = parse_primary()
        while i < len(toks) and toks[i] == "&":
            i += 1
            rhs = parse_primary()
            node = And(node, rhs)
        return node

    def parse_or() -> Expr:
        nonlocal i
        node = parse_and()
        while i < len(toks) and toks[i] == "|":
            i += 1
            rhs = parse_and()
            node = Or(node, rhs)
        return node

    root = parse_or()
    if i != len(toks):
        raise MappingError(f"Unexpected trailing tokens in FindingsModel_curation: {toks[i:]}")
    return root


def expr_contains_token(expr: Expr, normalized_token: str) -> bool:
    if isinstance(expr, Atom):
        return normalize_model_token(expr.token) == normalized_token
    if isinstance(expr, Not):
        return expr_contains_token(expr.child, normalized_token)
    if isinstance(expr, And) or isinstance(expr, Or):
        return expr_contains_token(expr.left, normalized_token) or expr_contains_token(expr.right, normalized_token)
    return False


# =========================
# Token normalization
# =========================

_CLASS_MAP = {
    "smallvariant": "SmallVariant",
    "gaindeletion": "GainDeletion",
    "disruption": "Disruption",
    "fusion": "Fusion",
    "arm": "Arm",
    "virus": "Virus",
    "pharmocogenotype": "PharmocoGenotype",
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
# Mapping primitives
# =========================

HGVS_PROTEIN_RE = re.compile(r"\bp\.[A-Za-z0-9_?*]+")
EXON_RE = re.compile(r"\bexon\s+(\d+)\b", re.IGNORECASE)


def format_args_kv(pairs: List[Tuple[str, str]]) -> str:
    return " & ".join(f"{k}={v}" for k, v in pairs)


def infer_gaindel_type_from_variant_curation(variant_curation: str) -> Optional[str]:
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
    gene = gene.strip()
    if "_" in gene:
        g1, g2 = [x.strip() for x in gene.split("_", 1)]
        a = f"(geneStart={g1} & geneEnd={g2})"
        b = f"(geneStart={g2} & geneEnd={g1})"
        return f"Fusion[{a} | {b}]"

    if fusion_both or (not fusion_5 and not fusion_3):
        return f"Fusion[geneStart={gene} | geneEnd={gene}]"
    if fusion_5:
        return f"Fusion[geneStart={gene}]"
    if fusion_3:
        return f"Fusion[geneEnd={gene}]"
    return f"Fusion[geneStart={gene} | geneEnd={gene}]"


def map_arm_atom(gene_locus_expr: str, variant_curation: str, arm_type_override: Optional[str] = None) -> str:
    """
    FIX: if arm_type_override is provided (e.g. Arm.type.ARM_GAIN), it MUST override inference.
    Chromosome-only + ARM_GAIN expands to p/q arms.
    """
    loci = [x.strip() for x in split_ands(gene_locus_expr)]
    arm_type = arm_type_override or infer_arm_type(variant_curation)

    locus_chunks: List[str] = []
    for loc in loci:
        fields = parse_arm_locus(loc)

        # chromosome-only single locus + ARM_GAIN -> expand to p|q
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

    # Force types when explicitly specified
    forced_arm_type: Optional[str] = None
    if token.startswith("Arm.type."):
        forced_arm_type = token.split(".")[-1].upper()  # ARM_GAIN / ARM_LOSS
        cls = "Arm"
    else:
        cls = token.split(".")[0]

    forced_gd_type: Optional[str] = None
    if token.startswith("GainDeletion.type."):
        forced_gd_type = token.split(".")[-1].upper()
        cls = "GainDeletion"

    if cls == "Virus":
        return f"Virus[{format_args_kv([('name', gene)])}]"

    if cls == "PharmocoGenotype":
        return f"PharmocoGenotype[{format_args_kv([('gene', gene)])}]"

    if cls == "Disruption":
        return f"Disruption[{format_args_kv([('gene', gene)])}]"

    if cls == "GainDeletion":
        pairs: List[Tuple[str, str]] = [("gene", gene)]
        typ = forced_gd_type or infer_gaindel_type_from_variant_curation(variant_curation)
        if typ:
            pairs.append(("type", typ))
        return f"GainDeletion[{format_args_kv(pairs)}]"

    if cls == "SmallVariant":
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

    if cls == "Fusion":
        if allow_fusion_flags:
            return map_fusion_with_flags(gene, fusion_both, fusion_5, fusion_3)
        return map_fusion_with_flags(gene, fusion_both=True, fusion_5=False, fusion_3=False)

    if cls == "Arm":
        # FIX: respect forced_arm_type from Arm.type.*
        return map_arm_atom(gene, variant_curation, arm_type_override=forced_arm_type)

    raise MappingError(f"Unknown findings class token: {atom_token!r} (normalized: {token!r})")


# =========================
# Render AST -> string
# =========================

def _needs_parens_under_and(expr: Expr) -> bool:
    return isinstance(expr, Or)


def _needs_parens_under_or(expr: Expr) -> bool:
    return isinstance(expr, And)


def expr_to_string(
    expr: Expr,
    gene: str,
    variant_curation: str,
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
            variant_curation,
            fusion_both=fusion_both,
            fusion_5=fusion_5,
            fusion_3=fusion_3,
            allow_fusion_flags=allow_fusion_flags,
        )

    if isinstance(expr, Not):
        child = expr.child
        if isinstance(child, Atom):
            cls = normalize_model_token(child.token).split(".")[0]
            atom_str = map_atom_to_args(
                child.token,
                gene,
                variant_curation,
                fusion_both=fusion_both,
                fusion_5=fusion_5,
                fusion_3=fusion_3,
                allow_fusion_flags=allow_fusion_flags,
            )
            if cls == "Virus":
                return atom_str
            return f"!{atom_str}"

        inner = expr_to_string(
            child,
            gene,
            variant_curation,
            fusion_both=fusion_both,
            fusion_5=fusion_5,
            fusion_3=fusion_3,
            allow_fusion_flags=allow_fusion_flags,
        )
        return f"!({inner})"

    if isinstance(expr, And):
        left = expr_to_string(
            expr.left,
            gene,
            variant_curation,
            fusion_both=fusion_both,
            fusion_5=fusion_5,
            fusion_3=fusion_3,
            allow_fusion_flags=allow_fusion_flags,
        )
        right = expr_to_string(
            expr.right,
            gene,
            variant_curation,
            fusion_both=fusion_both,
            fusion_5=fusion_5,
            fusion_3=fusion_3,
            allow_fusion_flags=allow_fusion_flags,
        )
        if _needs_parens_under_and(expr.left):
            left = f"({left})"
        if _needs_parens_under_and(expr.right):
            right = f"({right})"
        return f"{left} & {right}"

    if isinstance(expr, Or):
        left = expr_to_string(
            expr.left,
            gene,
            variant_curation,
            fusion_both=fusion_both,
            fusion_5=fusion_5,
            fusion_3=fusion_3,
            allow_fusion_flags=allow_fusion_flags,
        )
        right = expr_to_string(
            expr.right,
            gene,
            variant_curation,
            fusion_both=fusion_both,
            fusion_5=fusion_5,
            fusion_3=fusion_3,
            allow_fusion_flags=allow_fusion_flags,
        )
        if _needs_parens_under_or(expr.left):
            left = f"({left})"
        if _needs_parens_under_or(expr.right):
            right = f"({right})"
        return f"{left} | {right}"

    raise MappingError(f"Unknown Expr node type: {type(expr)}")


# =========================
# Case B union/dedup
# =========================

def base_class_key(expr: str) -> str:
    s = expr.strip()
    if s.startswith("!"):
        s = s[1:].lstrip()
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1].strip()
    if "[" in s:
        return s.split("[", 1)[0].strip()
    return s.strip()


def detail_score(expr: str) -> int:
    s = expr.strip()
    if s.startswith("!"):
        s = s[1:].lstrip()
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1].strip()
    if "[" not in s:
        return 0
    inside = s.split("[", 1)[1].rstrip("]")
    return len(re.findall(r"[A-Za-z0-9_.]+\s*=\s*[^&|\]]+", inside))


def dedupe_keep_most_detailed(exprs: List[str]) -> List[str]:
    best: Dict[str, str] = {}
    for e in exprs:
        if is_blank(e):
            continue
        k = base_class_key(e)
        if k not in best or detail_score(e) > detail_score(best[k]):
            best[k] = e
    return list(best.values())


def sort_by_preferred_order(exprs: List[str]) -> List[str]:
    def rank(e: str) -> Tuple[int, str]:
        k = base_class_key(e)
        try:
            return (CANONICAL_CLASS_ORDER.index(k), k)
        except ValueError:
            return (len(CANONICAL_CLASS_ORDER) + 1, k)
    return sorted(exprs, key=rank)


def flatten_simple_or_list(s: str) -> Optional[List[str]]:
    if "(" in s or ")" in s:
        return None
    if " & " in s:
        return None
    parts = [p.strip() for p in s.split("|")]
    parts = [p for p in parts if p]
    return parts


def apply_case_b_union_and_normalize(existing_expr: str, additions: List[str]) -> str:
    existing_expr = existing_expr.strip()
    additions = [a.strip() for a in additions if a.strip()]
    additions = dedupe_keep_most_detailed(additions)
    additions = sort_by_preferred_order(additions)

    flat = flatten_simple_or_list(existing_expr)
    if flat is None:
        if not additions:
            return existing_expr
        return f"{existing_expr} | " + " | ".join(additions)

    merged = flat + additions
    merged = dedupe_keep_most_detailed(merged)
    merged = sort_by_preferred_order(merged)
    return " | ".join(merged)


# =========================
# Core row mapping
# =========================

def map_row_to_args(row: pd.Series) -> str:
    gene_raw = normalize_text(row.get("Gene_curation", ""))
    var_raw = normalize_text(row.get("Variant_curation", ""))
    model_raw = normalize_text(row.get("FindingsModel_curation", ""))

    gene_type_raw = normalize_text(row.get("Gene_type", ""))
    fb_raw = normalize_text(row.get("Fusion_both", ""))
    f5_raw = normalize_text(row.get("Fusion_FivePrime", ""))
    f3_raw = normalize_text(row.get("Fusion_ThreePrime", ""))

    if is_blank(model_raw) and is_blank(gene_raw):
        return ""

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

        fusion_both = clean_bool(fb[i])
        fusion_5 = clean_bool(f5[i])
        fusion_3 = clean_bool(f3[i])

        variant_nonblank = not is_blank(variant)

        model_ast: Optional[Expr] = None
        if not is_blank(model_expr_str):
            model_ast = parse_model_expr(model_expr_str)

        # CASE A
        if variant_nonblank:
            allow_fusion_flags = False
            if model_ast is not None:
                allow_fusion_flags = expr_contains_token(model_ast, "Fusion")

            comp_expr = ""
            if model_ast is not None:
                comp_expr = expr_to_string(
                    model_ast,
                    gene,
                    variant,
                    fusion_both=fusion_both,
                    fusion_5=fusion_5,
                    fusion_3=fusion_3,
                    allow_fusion_flags=allow_fusion_flags,
                )

        # CASE B
        else:
            comp_expr = ""
            if model_ast is not None:
                comp_expr = expr_to_string(
                    model_ast,
                    gene,
                    variant,
                    fusion_both=fusion_both,
                    fusion_5=fusion_5,
                    fusion_3=fusion_3,
                    allow_fusion_flags=True,
                )

            additions: List[str] = []
            if gene_type == "TSG":
                additions.append(f"SmallVariant[gene={gene}]")
                additions.append(f"GainDeletion[gene={gene} & type=SOMATIC_DEL]")
                additions.append(f"Disruption[gene={gene}]")
            elif gene_type == "ONCO":
                additions.append(f"SmallVariant[gene={gene}]")
                additions.append(f"GainDeletion[gene={gene} & type=SOMATIC_GAIN]")
                if fusion_both or fusion_5 or fusion_3:
                    additions.append(map_fusion_with_flags(gene, fusion_both, fusion_5, fusion_3))

            if comp_expr and additions:
                comp_expr = apply_case_b_union_and_normalize(comp_expr, additions)
            elif (not comp_expr) and additions:
                additions = dedupe_keep_most_detailed(additions)
                additions = sort_by_preferred_order(additions)
                comp_expr = " | ".join(additions)

        comp_expr = comp_expr.strip()

        # Multi-component formatting (comma separated)
        if n > 1 and (" | " in comp_expr or " & " in comp_expr) and not (comp_expr.startswith("(") and comp_expr.endswith(")")):
            comp_expr = f"({comp_expr})"

        if comp_expr:
            out_components.append(comp_expr)

    return ", ".join(out_components)


# ----------------------------
# MAIN (keep args unchanged)
# ----------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate Args column for GeneAlteration mapping."
    )

    parser.add_argument("--input_xlsx", required=True)
    parser.add_argument("--output_dir", required=True)
    parser.add_argument("--sheet_name", required=True)
    parser.add_argument("--overwrite_col_i", action="store_true")
    parser.add_argument("--no_diff_sheet", action="store_true")
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

    LOGGER.info("Generating Args...")
    df["Args_generated"] = df.apply(map_row_to_args, axis=1)

    if args.overwrite_col_i:
        LOGGER.info("Overwriting Args column...")
        df["Args"] = df["Args_generated"]

    output_path = output_dir / f"{input_path.stem}_mapped.xlsx"

    LOGGER.info("Writing output: %s", output_path)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=args.sheet_name, index=False)

    LOGGER.info("Done. Output written to %s", output_path)


if __name__ == "__main__":
    main()