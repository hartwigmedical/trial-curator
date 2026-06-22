"""Tree-navigation mapper.

Instead of retrieving a flat candidate list and asking an LLM to pick from it,
the navigator walks the OncoTree top-down. At each node it shows the model only
that node's real children and asks, per child, whether to descend, emit, or
skip. Because the model only ever chooses among codes that exist, it cannot
hallucinate a code. Walking stops on each branch as soon as the input no longer
supports a more specific subtype, which is exactly the over/under-specificity
judgement the old reviewer tried to reconstruct after the fact.
"""

from __future__ import annotations

from collections.abc import Sequence

from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.models import (
    SCOPE_TO_FALLBACK_CODE,
    MappingCandidate,
    MappingFeedback,
    MappingOutcome,
    NavDecision,
    NavigationStep,
    NavigationTrace,
    OncoTreeNode,
)
from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.oncotree import (
    OncoTree,
    remove_ancestor_codes,
)
from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.reasoning import ReasoningClient
from aus_trial_universe.drug_utility_path.oncotree_agentic_workflow.schemas import (
    coerce_fallback,
    coerce_scope,
    node_decision_format,
    parse_decisions,
    root_decision_format,
)

DEFAULT_MAX_NODES = 60


class NavigatorMapper:
    def __init__(
        self,
        client: ReasoningClient,
        tree: OncoTree,
        *,
        max_nodes: int = DEFAULT_MAX_NODES,
    ) -> None:
        self.client = client
        self.tree = tree
        self.max_nodes = max_nodes

    def map(
        self,
        *,
        input_text: str,
        feedback: Sequence[MappingFeedback] = (),
    ) -> MappingOutcome:
        steps: list[NavigationStep] = []
        # code -> the candidate we will return for every node we decide to emit
        emitted: dict[str, MappingCandidate] = {}

        roots = self.tree.root_nodes()
        root_payload = self.client.complete_json(
            system_prompt=NAVIGATOR_SYSTEM_PROMPT,
            user_prompt=_root_user_prompt(input_text, roots, feedback),
            response_format=root_decision_format(roots),
        )
        scope = coerce_scope(root_payload.get("scope"))
        include_fallback = coerce_fallback(root_payload.get("include_fallback"))
        root_decisions = parse_decisions(root_payload.get("roots"), roots)
        steps.append(
            NavigationStep(
                parent_code=None,
                parent_name="OncoTree roots",
                stop_here=False,
                decisions=root_decisions,
            )
        )

        if scope == "non_cancer":
            return MappingOutcome((), NavigationTrace(scope, tuple(steps)))
        if scope in SCOPE_TO_FALLBACK_CODE:
            code = SCOPE_TO_FALLBACK_CODE[scope]
            rationale = f"Input describes a {scope.replace('_', ' ')} scope rather than a specific cancer type."
            return MappingOutcome(
                (MappingCandidate(code, "high", rationale),),
                NavigationTrace(scope, tuple(steps)),
            )

        frontier: list[OncoTreeNode] = []
        queued: set[str] = set()
        # A broad term alongside specific ones (e.g. "Solid Tumor | Mesothelioma")
        # emits the fallback as well as the codes found by walking the tree.
        if include_fallback in SCOPE_TO_FALLBACK_CODE:
            fallback_code = SCOPE_TO_FALLBACK_CODE[include_fallback]
            emitted[fallback_code] = MappingCandidate(
                fallback_code, "high", "Input also describes a broad fallback scope."
            )
        self._apply_decisions(root_decisions, emitted, frontier, queued)

        truncated = False
        visited = 0
        while frontier:
            if visited >= self.max_nodes:
                truncated = True
                break
            node = frontier.pop(0)
            visited += 1
            children = self.tree.children(node.code)
            if not children:
                # Reached a leaf via descend: the most specific code on this path.
                emitted.setdefault(
                    node.code,
                    MappingCandidate(node.code, "medium", "Leaf node reached during descent."),
                )
                continue

            payload = self.client.complete_json(
                system_prompt=NAVIGATOR_SYSTEM_PROMPT,
                user_prompt=_node_user_prompt(input_text, node, children, self.tree, feedback),
                response_format=node_decision_format(children),
            )
            stop_here = bool(payload.get("stop_here"))
            decisions = parse_decisions(payload.get("children"), children)
            steps.append(
                NavigationStep(
                    parent_code=node.code,
                    parent_name=node.name,
                    stop_here=stop_here,
                    decisions=decisions,
                )
            )
            if stop_here:
                emitted.setdefault(
                    node.code,
                    MappingCandidate(
                        node.code,
                        "medium",
                        "Input entailed by this node; no child adds supported specificity.",
                    ),
                )
            self._apply_decisions(decisions, emitted, frontier, queued)

        codes = remove_ancestor_codes(tuple(emitted.keys()), self.tree)
        proposed = tuple(emitted[code] for code in codes)
        return MappingOutcome(proposed, NavigationTrace(scope, tuple(steps), truncated))

    def _apply_decisions(
        self,
        decisions: Sequence[NavDecision],
        emitted: dict[str, MappingCandidate],
        frontier: list[OncoTreeNode],
        queued: set[str],
    ) -> None:
        for decision in decisions:
            if decision.action == "emit":
                emitted.setdefault(
                    decision.code,
                    MappingCandidate(decision.code, decision.confidence, decision.rationale),
                )
            elif decision.action == "descend":
                if decision.code in queued:
                    continue
                node = self.tree.get(decision.code)
                if node is not None:
                    frontier.append(node)
                    queued.add(decision.code)
            # "skip" prunes the branch.


NAVIGATOR_SYSTEM_PROMPT = """
You are an expert clinical oncology ontology mapper navigating the OncoTree
hierarchy top-down to map free-text cancer terms to OncoTree codes.

Reason clinically. At each step you are shown a parent context and a set of
nodes. For each node choose exactly one action:
- "descend": the input belongs in this branch and may support an even more
  specific subtype inside it. Keep walking.
- "emit": the input is entailed precisely by this node and nothing deeper is
  supported. Return it as a final code.
- "skip": the input does not relate to this node. Prune it.

Rules:
- Do not over-specialise. Only descend or emit a subtype the input actually
  states or strongly implies. When the input names a cancer at the general
  organ level without a specific histology (e.g. "breast cancer", "ovarian
  cancer", "soft tissue sarcoma"), EMIT the organ root code itself; do not
  descend to a particular subtype. Emit a specific subtype only when the input
  names it (e.g. "lung adenocarcinoma" -> the adenocarcinoma code).
- A generic histology word at the organ level ("cancer", "carcinoma",
  "sarcoma", "neoplasm", "tumour") is the organ-level term: emit the organ ROOT
  code, never a "Not Otherwise Specified (NOS)" descendant whose name merely
  echoes the input. Choose a "...NOS" code only when the input itself says NOS.
  Example: "soft tissue sarcoma" -> SOFT_TISSUE (the root), NOT a "Soft Tissue
  Sarcoma, NOS" subtype.
- A single input may map to several codes. Two situations both produce multiple
  codes: (a) several distinct cancers are listed, and (b) a category or synonym
  term spans multiple branches (e.g. "mesothelioma" -> peritoneal AND pleural
  mesothelioma; "neuroendocrine carcinomas" -> the neuroendocrine codes across
  the relevant organs). Descend into every relevant organ and emit all codes
  the term entails.
- Distinguish a primary cancer type from a metastatic site: map the primary.
  An unspecified "metastasis"/"metastatic disease" with no stated primary is a
  broad solid-tumour scope, not a site.
- At the root step also classify scope:
    "specific"     -> a mappable cancer type; act on the root organs.
    "solid_tumour" -> broad solid tumours / unspecified metastasis with no
                      specific primary site.
    "pan_cancer"   -> cancer-type-agnostic / tumour-agnostic scope.
    "non_cancer"   -> benign or non-cancer; nothing should be mapped.
  When a row mixes a broad term with specific types (e.g. "Solid Tumor, Adult |
  Mesothelioma"), use scope "specific", set "include_fallback" to "solid_tumour"
  (or "pan_cancer") so the broad code is emitted too, and act on the organs for
  the specific types. Otherwise set "include_fallback" to "none".
- For a non-root step, set "stop_here" true when the parent node itself is the
  right answer and none of its children add supported specificity.

Return only the requested JSON.
""".strip()


def _root_user_prompt(
    input_text: str,
    roots: Sequence[OncoTreeNode],
    feedback: Sequence[MappingFeedback],
) -> str:
    lines = [
        f"Input text: {input_text}",
        "",
        "Classify scope, then for each OncoTree root organ choose descend/emit/skip.",
        "Root organs:",
        *[f"- {node.code}: {node.name}" for node in roots],
    ]
    lines.extend(_feedback_lines(feedback))
    return "\n".join(lines)


def _node_user_prompt(
    input_text: str,
    node: OncoTreeNode,
    children: Sequence[OncoTreeNode],
    tree: OncoTree,
    feedback: Sequence[MappingFeedback],
) -> str:
    path = " > ".join(tree.path_names(node.code))
    lines = [
        f"Input text: {input_text}",
        "",
        f"Current node: {node.code} ({node.name})",
        f"Path so far: {path}",
        "",
        "Set stop_here if this node is the best answer. For each child below,",
        "choose descend/emit/skip:",
        *[f"- {child.code}: {child.name}" for child in children],
    ]
    lines.extend(_feedback_lines(feedback))
    return "\n".join(lines)


def _feedback_lines(feedback: Sequence[MappingFeedback]) -> list[str]:
    if not feedback:
        return []
    lines = ["", "Reviewer feedback from previous attempt(s) — adjust accordingly:"]
    lines.extend(f"- Rejected {item.code}: {item.note}" for item in feedback)
    return lines
