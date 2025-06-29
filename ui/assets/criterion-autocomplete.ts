import { keymap } from "@codemirror/view";
import {
  autocompletion,
  Completion,
  CompletionContext,
  CompletionResult,
  acceptCompletion,
} from "@codemirror/autocomplete";

// Define the shape of each keyword suggestion
const keywords: Completion[] = [
  { label: "Age", type: "function" },
  { label: "Sex", type: "function" },
  { label: "LabValue", type: "function" },
  { label: "PrimaryTumor", type: "function" },
  { label: "Histology", type: "function" },
  { label: "MolecularBiomarker", type: "function" },
  { label: "GeneAlteration", type: "function" },
  { label: "MolecularSignature", type: "function" },
  { label: "DiagnosticFinding", type: "function" },
  { label: "Metastases", type: "function" },
  { label: "Comorbidity", type: "function" },
  { label: "PriorTreatment", type: "function" },
  { label: "CurrentTreatment", type: "function" },
  { label: "TreatmentOption", type: "function" },
  { label: "Contraindication", type: "function" },
  { label: "ClinicalJudgement", type: "function" },
  { label: "ReproductiveStatus", type: "function" },
  { label: "Infection", type: "function" },
  { label: "Symptom", type: "function" },
  { label: "PerformanceStatus", type: "function" },
  { label: "LifeExpectancy", type: "function" },
  { label: "RequiredAction", type: "function" },
  { label: "TissueAvailability", type: "function" },
  { label: "Other", type: "function" },
  { label: "And", type: "function" },
  { label: "Or", type: "function" },
  { label: "Not", type: "function" },
  { label: "If", type: "function" },
  { label: "Timing", type: "function" },
  { label: "description", type: "property" },
  { label: "finding", type: "property" },
  { label: "method", type: "property" },
  { label: "treatment", type: "property" },
];

// Completion source function with proper typing
function myCompletionSource(
  context: CompletionContext
): CompletionResult | null {
  const word = context.matchBefore(/\w*/);
  if (!word || (word.from === word.to && !context.explicit)) {
    return null;
  }

  return {
    from: word.from,
    options: keywords,
    validFor: /^\w*$/,
  };
}

// Export autocomplete extension
export const criterionAutocomplete = autocompletion({
  override: [myCompletionSource],
});

// Export Tab key handler for accepting autocompletion
export const tabAcceptKeymap = keymap.of([
  {
    key: "Tab",
    run: acceptCompletion,
    preventDefault: true,
  },
]);
