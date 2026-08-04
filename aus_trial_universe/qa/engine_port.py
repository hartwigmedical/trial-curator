"""Faithful Python transcription of the matching engine's expression parser — the DRY-RUN tool.

Transcribed line-for-line from, and pinned to:
    repo   /Users/junrancao/HMF_repository/oncoact  ·  branch trial_matching  ·  commit a97142938 (2026-05-22)
    files  trial-matching/src/main/kotlin/com/hartwig/oncoact/trialmatching/
             trial/TrialGeneticsParser.kt · genetic/parsers/GeneticAlterationParser.kt
             genetic/parsers/{ProteinAnnotationParser,AminoAcidParser,ChromosomeArm*Parser,ArmParser}.kt
             genetic/{Fusion,Fluctuation,SmallVariantAlteration,Interruption,SpliceRegionVariant}.kt
             GeneticCriteria.kt
    enums  hmftools/finding-datamodel/.../SmallVariant.java (VariantEffect, CodingEffect) · GainDeletion.java
           ChromosomeArmCopyNumber.java

WHY A TRANSCRIPTION AND NOT THEIR JAR. Their module is a submodule of a Maven reactor whose `finding-datamodel`
dependency is not installed locally, and there is no `kotlinc` on this machine — so running the real thing means
building a multi-module chain that may need artefacts from an internal registry. Independently of that, the
transcription is what FOUND the defects: `Fusion(null, null)` does not throw, so executing their code would have
returned it as a correct answer, and their own test cannot catch it because it asserts parser output against
parser output. Rewriting each branch by hand forces the question "what happens when `split('=')` yields 3?".

FIDELITY IS TESTED, NOT ASSERTED. `tests/agentic/qa/test_engine_port.py` runs all 12 cases from their
`TrialGeneticsParserTest.kt` through this port and requires identical output (12/12 at the pinned commit). If the
engine moves, re-run that test FIRST — every number this module produces is only as good as that check.

LIMITS. This is the parse + criteria layer, not end-to-end matching: with no patient data it reports which criteria
object the engine builds, not who matches. That is enough for the verdicts we draw from it ("matches nobody",
"biomarker gate disabled", "any fusion"), because those follow from the criteria object plus `includes()`.
"""
import re

VARIANT_EFFECT = {"STOP_GAINED","STOP_LOST","START_LOST","FRAMESHIFT","SPLICE_ACCEPTOR","SPLICE_DONOR",
 "INFRAME_INSERTION","INFRAME_DELETION","MISSENSE","PHASED_MISSENSE","PHASED_INFRAME_INSERTION",
 "PHASED_INFRAME_DELETION","SYNONYMOUS","PHASED_SYNONYMOUS","INTRONIC","FIVE_PRIME_UTR",
 "THREE_PRIME_UTR","UPSTREAM_GENE","NON_CODING_TRANSCRIPT","OTHER"}
AA1 = set("ARNDCQEGHILKMFPSTWYV")          # AminoAcid one-letter codes
ARM_TYPE = {"GAIN","LOSS","DIPLOID"}
_PROT_RE = re.compile(r"([A-Za-z]+)([0-9]+)([A-Za-z\\*?]+)")

class ParseError(Exception): pass

def parse_protein_annotation(data, gene):
    if data == "": return None
    if data == "p.?": return None
    m = _PROT_RE.search(data[2:])
    if not m: raise ParseError(f"Invalid protein annotation format: {data}")
    pos = int(m.group(2)); ref = m.group(1); alt = m.group(3)
    ref_cod = ref if (len(ref)==1 and ref in AA1) else ("*" if ref=="*" else None)
    if ref == "*": return ("Extension", gene, pos)
    if alt == "fs":       return ("Frameshift", gene, pos, ref_cod)
    if alt.endswith("*"): return ("StopGained", gene, pos, ref_cod)
    if alt.endswith("?"): return ("Unknown", gene, pos, ref_cod)
    alt_cod = alt if (len(alt)==1 and alt in AA1) else None   # AminoAcidParser.get -> null for 'X'
    return ("Substitution", gene, pos, ref_cod, alt_cod)

class GeneticAlterationParser:
    def __init__(self, file_line):
        name_end = file_line.find("[")
        if name_end <= 0: raise ParseError(f"Malformed file line: {file_line}")
        self.type_name = file_line[:name_end]
        self.data_string = file_line[name_end+1:len(file_line)-1]
        if self.data_string == "inSpliceRegion":
            self.data = {}; self.is_splice_region = True
        else:
            pairs = []
            for tok in self.data_string.split("&"):
                parts = tok.split("=")
                if len(parts) == 2: pairs.append((parts[0].strip(), parts[1].strip()))
            self.data = dict(pairs)          # Kotlin associate: last wins
            self.is_splice_region = False

    def parse(self):
        t = self.type_name
        if t == "Disruption":
            return ("Interruption", self.data["gene"])          # KeyError == Kotlin NPE
        if t == "Fusion":
            return ("Fusion", self.data.get("geneStart"), self.data.get("geneEnd"))
        if t == "GainDeletion":
            gene = self.data["gene"]
            ts = self.data.get("type")
            return ("Fluctuation", gene, ts if ts in ("GAIN","HOM_DEL") else None)
        if t == "SmallVariant":
            if self.is_splice_region: return ("SpliceRegionVariant",)
            gene = self.data.get("gene", "")
            pi = (parse_protein_annotation(self.data["transcriptImpact.hgvsProteinImpact"], gene)
                  if "transcriptImpact.hgvsProteinImpact" in self.data else None)
            exon = int(self.data["transcriptImpact.affectedExon"]) if "transcriptImpact.affectedExon" in self.data else None
            effects = set()
            if "transcriptImpact.effects" in self.data:
                v = self.data["transcriptImpact.effects"]
                if v not in VARIANT_EFFECT: raise ParseError(f"No enum constant VariantEffect.{v}")
                effects.add(v)
            return ("SmallVariantAlteration", gene, exon, frozenset(effects), pi)
        if t == "Arm":
            return self._parse_arm()
        raise ParseError(f"Unknown genetic criterion type: {t}")

    def _parse_arm(self):
        raw = self.data_string
        parts = ([p.replace("(","").replace(")","").strip() for p in raw.strip().split(") & (")]
                 if raw.startswith("(") else [raw])
        out = []
        for p in parts:
            dm = {}
            for f in p.split("&"):
                kv = f.strip().split("=")
                dm[kv[0]] = kv[1]                              # IndexError == Kotlin exception
            ty = dm["type"]
            ty = ty[4:] if ty.startswith("ARM_") else ty
            if ty not in ARM_TYPE: raise ParseError(f"No enum constant Type.{ty}")
            arm = dm["arm"]
            if arm not in ("p","P","q","Q"): raise ParseError(f"Unknown arm name: {arm}")
            out.append(("ChromosomeArmChange", dm["chromosome"], arm.upper(), ty))
        return ("ChromosomeArmChanges", frozenset(out))

PHARMACOGENOTYPE, WILDTYPE, VIRUS = "PharmocoGenotype", "Wildtype", "Virus"

class TrialGeneticsParser:
    def __init__(self, s): self.input = s; self.failures = []; self.skipped = []
    def parse(self):
        parsed = []
        for fpt in self._first_pass():
            try: parsed.extend(self._second_pass(fpt))
            except Exception as e: self.failures.append((fpt[0], str(e)))
        return parsed
    def _first_pass(self):
        tokens=[]; lvl=0; cur=""
        def add(cur):
            t = cur.strip()
            if not t: return
            if t.startswith("NOT(") and t.endswith(")"):
                inner = t[4:-1]
                if inner.startswith("(") and inner.endswith(")"): inner = inner[1:-1]
                tokens.append((inner, True))
            else: tokens.append((t, False))
        for c in self.input:
            if c == "(": cur += c; lvl += 1
            elif c == ")": cur += c; lvl -= 1
            elif c == "&" and lvl == 0 and cur.strip().startswith("NOT("): add(cur); cur = ""
            else: cur += c
        add(cur)
        return tokens
    def _second_pass(self, fpt):
        inner_text, is_neg = fpt
        out = []; cur = ""
        def add_section(cur):
            t = cur.strip()
            if not t: return
            if PHARMACOGENOTYPE in t or WILDTYPE in t or VIRUS in t:
                self.skipped.append(t); return
            body = t[1:-1] if (t.startswith("(") and t.endswith(")")) else t
            out.append((GeneticAlterationParser(body).parse(), not is_neg, body))
        in_sq = False
        for c in inner_text:
            if c == "[": cur += c; in_sq = True
            elif c == "]": cur += c; in_sq = False
            elif c == "|" and not in_sq: add_section(cur); cur = ""
            else: cur += c
        add_section(cur)
        return out
