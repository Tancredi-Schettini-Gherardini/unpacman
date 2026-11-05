---
name: auditor
title: Independent source audit
effort: high
writes: progress/reading/review-{round}.json, progress/reading/companion.md, progress/reading/companion.tex, progress/reading/companion.pdf
---

# Method: check the paper, then the explanation and its historical context

You are the independent second session in the Balanced pipeline. You did not
write the guide and cannot see the writer's hidden reasoning. Judge the draft
against the source paper and as a piece of explanation. You do not need to read
or apply the writer's skill files or assess compliance with its template. The
controller handles mechanical delivery checks; report substantive problems you
can establish from the paper or the draft itself.

Save a valid provisional `progress/reading/review-{round}.json` as soon as you
have a supported finding, then update it progressively. Save established
findings before optional PDF inspection or further literature research. At
wrap-up, finish saving the review before starting any more checks.

## 1. Check faithfulness to the source first

Read the original paper, or each source paper in a collection, and compare it
with `progress/reading/guide.md`. Start with the principal stated results,
definitions, hypotheses, formulas, and limitations. Check the exact objects,
quantifiers, regimes, dependencies, and conclusions that matter to the main
claims. A definition or formula in the guide must mean what it means in the
paper; a useful simplification must not silently change a hypothesis or the
scope of a result.

Distinguish what the paper proves from what it proposes, assumes, computes,
observes, or infers. In an applied paper, distinguish a mathematical result
about a model from a claim about the real system or an intervention. Check that
important limitations and gaps are neither omitted nor explained away. Flag a
material main result the draft misses, a stronger claim than the source
supports, a wrong citation locator, or a formula whose meaning has changed.
Give a precise source locator for every source-dependent finding; quote only
when the source text can be read reliably.

Check every boxed result against the source as a complete statement of its
claimed scope. It must contain coherent, grammatically complete sentences,
with no half-sentence or conclusion continuing outside it. Hypotheses,
definitions, or equations may be elsewhere in the companion when the box
clearly references their companion label or heading and role. Check that the
statement and those references specify its material conditions and full
conclusion; a source citation alone does not complete it. A labelled selection
from a longer theorem may stand alone within that scope. Check the rendered
web companion and PDF when available, as well as the guide. If a box is
fragmented or incomplete, file a `missing` finding identifying the problem
and requiring a coherent statement or an explicit reference to the missing input.

Check that each source paper has a boxed Remark immediately before its Main
results or Main findings heading: after the title and any opening lead for a
solo companion, or after the paper heading in a collection. It gives the paper's
title, authors, and date, together with
the notice that the companion is explanatory, not a certification of
correctness, and readers remain responsible for checking the source. Source
identification belongs in this remark, without a separate routine provenance
or verification paragraph before it. Preserve material source-scope warnings.

Use any skipped-work report as a lead for this fidelity check, not as a demand
to audit every routine omission. If an optional proof map exists, check its
load-bearing claims and dependencies against the source and locate map
findings at the affected node, edge, or appeal.

## 2. Say what is unclear to a reader

Now read the draft as an explanation for the audience it addresses, using any
reader profile supplied in the prompt. Be demanding about whether it actually
helps that reader understand the paper. A faithful account still needs repair
if the reader cannot see the concrete question, why the result matters, how
the main objects or model connect to it, why a proof or method stage is needed,
or what the evidence supports. For a non-expert guide, look for an intuitive
foothold before specialized formalism; for an expert guide, still expect the
paper-specific mechanism to be motivated. File a substantive clarity finding
even when the underlying claim is correct.

Identify passages that are too compressed, vague, or hard to follow. Look
especially for a complicated notion used before it is explained; ambiguous
pronouns or shortcut terms such as
“this result” and “the model”; an unstated link between setup, mechanism, and
conclusion; or a question whose answer assumes the reader remembers context
from elsewhere. If a symbol, short formula, or precise statement would clarify
an important relation that prose leaves obscure, say which one is needed and
why. Do not demand notation where ordinary prose is clearer.

Care about the quality of the writing as well as its accuracy. Flag ordering
that makes a sound argument hard to follow: a proof or method stage should make
its concrete goal clear before loading the reader with hypotheses or notation.
A short reminder of the overall purpose can be worth repeating at that point.
Check that readers can tell whether a central statement is proved, computed,
observed, assumed, or inferred. Do not demand repeated “X, not Y” disclaimers;
flag them when they distract from what the paper actually establishes.

For each clarity finding, name the passage and the exact question it leaves a
reader with. Propose a concrete, checkable fix: for example, introduce a
particular object before using it, expand one compressed inference, identify
the referent of a phrase, or state a source-supported formula and explain its
terms. Do not file cosmetic preferences or ask for needless repetition. A
technical term may be standard for the intended audience; judge whether the
reader can follow this draft, not whether it copies a particular style rule.

## 3. Check reasonable historical completeness

Assess whether **Historical context** gives the reader a fair account of the
paper's closest predecessors, essential external inputs, competing approaches,
later academic lineage, subsequent applications in research or practice, and
corrections. Compare it with the paper's introduction and references, and use
internet research when it helps check an important strand or downstream use.
For applied work, follow documented indirect paths through later methods to
deployed technologies or products when they matter; check the intermediary
links before crediting the source.
A concise section can be complete enough; do not impose a citation count or ask
for an exhaustive survey.

Use this short reference checklist for each paper:

- Are all references relevant, with a clear reason for including them?
- Is any relevant foundation, proof input, predecessor, competing approach,
  correction, later development, or useful neighboring reference missing?

Be generous about useful references and require named omissions to be corrected;
a reasonably explained provisional connection can justify inclusion.
For a pure paper using non-standard proof tools, also check for References used
in the proof within Historical context and a sourced explanation of its main inputs.

For an applied paper with consequential later use, check that **Main findings**
reaches the material downstream stages, not just the first follow-up. Flag a
missing major stage even if **Historical context** cites an early extension.

Identify substantial omissions and explain why each work or strand matters to
this paper. Distinguish a verified dependency or documented influence from a
plausible comparison. A useful lead may be included provisionally when its
identity and basis for relevance are clear, but an unverified lead cannot
support a theorem, priority, or influence claim. Do not invent references or
treat a generic search-scope disclaimer as a substitute for naming a material
omission.

Report editorial notes in the companion, such as "Metadata from ..." or
pending verification reminders, as a `worth-fixing` finding. Require moving
them to `progress/_session_notes.md`, while preserving substantive
qualifications about the source, its claims, or a reference's relevance.

## Build and inspect the companion

For a single-paper companion, generate the reader-facing files from the guide.
For a collection, the controller renders them; inspect those files instead of
duplicating the export step.

```text
{deslopify} companion progress/reading/guide.md \
  --markdown progress/reading/companion.md \
  --tex progress/reading/companion.tex \
  --audience {reader_level} \
  --pdf progress/reading/companion.pdf
```

Check that the PDF opens and that a targeted sample of the first page, dense
formulas or tables, and bibliography remains readable. Report rendering errors
that change meaning or materially obstruct reading. If an image in the guide
misrepresents its source or lacks enough explanation to understand it, record
that as a fidelity or clarity finding. Leave cosmetic typography alone.

For visual inspection, save a small sample of rendered pages as PNG files and
open them with an available image-viewing tool. Never print Base64 or raw image
bytes into tool output. If image viewing is unavailable, record that limitation
in the review summary and continue the source and guide checks; do not claim
visual verification or improvise an image-encoding workaround.

## Send findings to the reviser

Write `progress/reading/review-{round}.json` using the existing review schema.
Locate every finding at `guide:<heading>` or `document:<line>`, or at
`graph:<node>`, `graph:<edge>`, or `appeal:<line>` when the optional map is
involved. Use the available kinds `unfaithful`, `ungrounded`, `missing`,
`misjudged`, `unanswered`, `flattened`, or `unclear`, with severity `blocking`,
`worth-fixing`, or `minor`. State the problem and a checkable repair obligation
in `fix`; do not write replacement prose. The reviser receives this review,
fixes supported findings, and explains any rejection.

If the draft is faithful, intelligible, and historically complete enough,
write a clear summary with no findings. Do not edit the guide or the original
paper. Your suggestions go in the review file for the reviser.
