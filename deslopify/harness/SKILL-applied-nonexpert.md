---
name: deslopify-applied-nonexpert
description: Write and independently ground a non-expert companion to an applied mathematics paper for a mathematically mature reader outside the paper's specialty.
---

# Method: write the applied-mathematics non-expert guide before anyone draws the graph

You are the first independent reading session. Your sole reader-facing
product is `progress/reading/guide.md`, a guide to the finished mathematical
document `{document}`. An independent reviewer checks whether the guide helps
its intended reader understand the paper and whether its claims match the
source. Some pipelines also test clarity without the source first. A reviser
follows when the review finds work worth fixing or required output remains
incomplete.

Do not rewrite or edit the source. Expository text goes in
`progress/reading/guide.md` or `progress/_session_notes.md`.

Follow the shared companion instructions supplied with the routing method
(`SKILL-common.md`) for source fidelity, continuations, presentation, historical
research, citations, and the boxed source-and-scope remark.

## Selected paper profile

This is the applied-mathematics nonexpert method. The entry skill selects this file
after reading the source. Use its instructions for the selected paper; for a
collection, the collection's three-part structure takes precedence.

## Audience and length

Write for a mathematically mature reader new to the paper's subfield. Assume
general mathematics, but not its specialized terminology, standard examples,
folklore, or notation. Give the reader an intuitive foothold before formal
statements: describe the objects, the question, and why it is difficult in
familiar language. Explain a specialized term or symbol at first use and say
what a mathematical quantity represents. Translate notation-heavy statements
into plain language without losing material hypotheses or conclusions.

Build from the conceptual question and motivating example to the precise
result and then the mechanism. Explain background locally where it is needed;
use an analogy only when it clarifies the relevant relation and mark any
important limit. Aim for roughly 3,000--8,000 words for an ordinary paper,
adjusting to its complexity. Give understanding priority over compression.

## Build the internal model before writing

### Applied-mathematics profile

Before drafting, identify whether the paper mainly contributes a theorem,
numerical method, model, experiment, benchmark, software framework, survey,
or ML result. Use this to choose emphasis, not to impose a template.

Identify the concrete question and connect the paper's model, inputs, outputs,
and measures to real objects. Only when it matters to interpreting a result,
explain the distinction between the practical target and the paper's model,
proxy, simulator, dataset, or metric.

For each major finding, determine what was proved, computed, measured, or
observed, and what assumptions, comparisons, and uncertainty define its scope.
Focus on the few ideas and results that explain the contribution. For a central
proof, explain its mechanism; for numerical or ML work, examine the relevant
error, stability, evaluation, and generalization issues. Consider these
dimensions when they are central to the paper; do not mention them merely
because they are standard concerns in the field. Say when material
information is not stated rather than guessing.

Cite principal findings, figures, tables, formulas, and unusual definitions
with compact source locators; routine explanatory prose needs no locator.

## Write `progress/reading/guide.md`

### Opening lead for a beginner

After the source-and-scope remark and before **Main findings**, write one to three
short orientation paragraphs. Start with the real-world or scientific
situation: what needs to be predicted, explained, optimized, designed, or
controlled, and why that is difficult. Connect it in plain language to the
paper's model or method before introducing symbols. A stylized scenario can
help, but label a companion-created example and do not present it as evidence.

### Companion structure

Use these top-level sections in this order: **Main findings**, **Q&A**,
optional **Worked examples**, **Historical context**, **About the method and
experimental validation**, optional **Where the document is thin**. All except
**Worked examples** and **Where the document is thin** are required. A short
unheaded lead may precede **Main findings**, but no substantive **Introduction** or **Overview**
may do so. You may add at most one genuinely paper-specific top-level reader aid
under the rule below.

**About the method and experimental validation** should explain how the method
works, the main evidence, and any proof ideas needed to interpret the findings.
It is not an audit trail of every experimental setting or dependency.

### Main findings

Write **Main findings** for a mathematically mature reader new to the subject.

Begin with the concrete scientific, engineering, or computational problem and
why it matters. Explain specialized terms and connect the important formal
objects and quantities to the real system in plain language. A small example
may help, but should not be presented as evidence.

Then present the **1–3 most important findings**, stating each clearly before
substantial method detail. Explain what was proved, computed, measured, or
observed, with only the evidence and setup needed to understand its credibility
and scope. For a theoretical paper, explain the main objects, assumptions,
notation, and governing relations well enough to connect the model back to the
application.

After the findings, explain the main conceptual or methodological lesson.
Distinguish direct support from interpretation when it matters. Include only
limitations that materially change how a headline finding should be read;
leave minor protocol discrepancies, reconstruction issues, implementation
details, and reproducibility notes to **About the method and experimental
validation**.

Briefly explain what is new relative to close prior work. End with a short
account of the most important later developments or applications; internet
research may help. Leave detailed genealogy to **Historical context**.

Prefer a few well-developed paragraphs to an inventory of experiments or
caveats. Use a formula, figure, or compact table when it genuinely helps.
Aim for roughly **2–4 rendered pages**, longer when more orientation is needed.

### Q&A

Typically include about five substantive questions under descriptive
subsection headings. Choose questions that help a reader understand the
application target, modeling choices, main mechanism, evidence, and limits.
The first two should usually address the central conceptual stakes, as in a
research talk, rather than local implementation details.

Give each answer enough context to stand alone and use up to three paragraphs
when needed. Explain any specialized term before relying on it. If the paper
does not settle the question, say so; distinguish a supported conclusion from
your own interpretation. A cross-reference can guide the reader but should
not replace the explanation.

### Worked examples

This section is optional. Include a source example, small computation, toy
model, or carefully labeled companion scenario only if it makes the model,
method, metric, or a limitation easier to understand. Connect it to the main
finding it illuminates. Do not present an illustrative case as experimental
evidence or a deployment.

#### Optional paper-specific section

Add at most one content-specific top-level reader aid when the paper genuinely
needs it, such as a dictionary between the model and the real system or a
compact account of parameter regimes. Place it where it helps the reader;
omit it if the required sections already cover the material.

### Historical context

Follow the historical-research and citation instructions in `SKILL-common.md`,
with the audience and level of explanation specified above.

### About the method and experimental validation

After presenting the findings, explain the method's central idea and why it
addresses the practical or scientific problem. Connect its inputs, model or
representation, main steps, and outputs to the real objects of interest.
Describe enough of the mechanism to make the results intelligible; use a
formula or example when it helps, and leave routine implementation details
to the source.

Then explain how the paper tested or supported its main claims. Begin with
what the experiments, simulations, computations, or proofs are meant to show.
Describe the key setup, comparison, and result in a clear sequence, grouping
evidence by type when that helps readers see what each kind establishes.

For important empirical claims, identify the data or simulated setting,
baseline, metric, and uncertainty when available. Explain a decisive ablation,
negative result, or sensitivity check if it changes the interpretation. If
these are absent, describe the resulting limit without turning this section
into a missing-items checklist. For theoretical claims, state what the
guarantee says, its essential assumptions, and how it relates to the tested
setting; give the proof idea only when it clarifies the paper's main advance.

Make clear where the evidence ends and the broader application claim begins.
If it affects how a result should be read, discuss whether the model or proxy
captures the practical target and what remains untested. Cite the source's
central experiments, figures, tables, appendices, or theorems where readers can
follow up.

### Where the document is thin

This section is optional. Include it only if your reading identifies serious
gaps in the paper's mathematics, modeling, or evidence. Explain each gap and
where it occurs. Otherwise
omit the section; delicate steps, unanswered questions, and limits on your own
verification do not by themselves warrant it.

Finish with the bibliography required by the shared companion instructions.

## Before stopping

Write `progress/_session_notes.md`. Record what was hardest to explain, where a
later auditor should be skeptical, which supplementary artifacts or computations
were inspected or rerun, the literature cutoff and search limitations, source
location or extraction problems, and which broadly useful illustrations were
included or omitted and why. Record the selected applied profile and its basis. Do
not include hidden reasoning, token counts, costs, or timings.

Leave the source byte-for-byte unchanged. Do not create `graph.json`, other
graph files, maps, summary files, or PDFs in this pass. The later audit copies the guide to
`companion.md`, generates the persistent `companion.tex`, and compiles the PDF.
