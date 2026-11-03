---
name: deslopify-applied-expert
description: Write and independently ground a technically auditable expert companion to an applied mathematics paper.
---

# Method: write the applied-mathematics expert guide before anyone draws the graph

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

This is the applied-mathematics expert method. The entry skill selects this file
after reading the source. Use its instructions for the selected paper; for a
collection, the collection's three-part structure takes precedence.

## Audience and length

Write for a technically sophisticated reader, possibly outside the paper's
specific application or method. Assume general graduate-level mathematics and
science, but explain the paper's particular model, terminology, measurements,
and evaluation setting. Begin with the real problem, then connect the practical
target to the model and to what was actually measured or proved.

State assumptions, system boundaries, parameter regimes, and uncertainty when
they materially change the interpretation of a main finding. Give enough
technical detail to understand the result, without turning the companion into
an inventory of the experimental log or proof appendix. Aim for roughly
2,500--7,000 words for an ordinary paper, adjusting to its complexity.

## Build the internal model before writing

### Applied-mathematics profile

Before drafting, classify the paper as one or more of: theorem/proof,
numerical method or numerical analysis, modeling and validation,
computational experiment or benchmark, software or computational framework,
survey, ML theory, or ML empirical. Use this classification only to choose
what deserves emphasis; do not force the paper into a fixed template or print
the classification unless it helps the reader.

Identify the real scientific, engineering, or computational question, the
objects or systems the paper studies, and the main route from those objects to
the paper's conclusions. Only when it matters to interpreting a result, explain
the distinction between the practical target and the model, proxy, benchmark,
simulator, learned objective, or evaluation metric used to study it.

For each major finding, determine what the paper actually proves, computes,
measures, or observes; what evidence supports it; and the assumptions, regime,
data, uncertainty, or comparison needed to understand its scope. Distinguish
the result from its interpretation and from broader application claims.

Prefer a small number of load-bearing findings, ideas, and mechanisms over an
inventory of experiments, hyperparameters, lemmas, or caveats. Include enough
setup for the reader to understand why the conclusions matter and why the
evidence supports them, but not every reported setting or parameter.

If a mathematical guarantee or proof is central, explain its conceptual
mechanism, assumptions, and practical scope. For numerical analysis, attend to
the distinctions among consistency, stability, convergence, error,
conditioning, and complexity. For ML, attend to issues such as
training versus evaluation distribution, proxy objectives, benchmark validity,
ablations, run variation, distribution shift, or contamination. Consider these
dimensions when they are central to the paper; do not mention them merely
because they are standard concerns in the field. If material information is
unavailable, say “not stated” or “not assessable from the paper” rather than
filling the gap by inference.

Cite principal findings, figures, tables, formulas, and unusual definitions with
compact source locators. Do not attach a locator to routine explanatory prose.

## Write `progress/reading/guide.md`

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

Write a section titled **Main findings** for a technically sophisticated reader
who has not read the paper.

Begin with the concrete scientific, engineering, computational, or application
problem. Explain what the paper is trying to understand, predict, compute, or
control, and why the problem matters. When the paper studies a mathematical or
statistical representation of a real system, explain in plain language how the
formal objects, assumptions, and quantities relate to the application.

Then present the **1–3 most important findings**. State each finding clearly
before giving substantial method detail. Explain what was actually proved,
computed, measured, or observed, and give only the evidence or mathematical
setup needed for the reader to understand why the finding is credible and what
its scope is.

For more theoretical applied-mathematics papers, make sure the mathematical
model is explained in enough detail somewhere in this section for the reader to
understand its main objects, assumptions, notation, and governing relations.
Connect the formal model back to the concrete application in plain language.

After the findings, explain the main conceptual or methodological lesson of the
paper. Identify the structural idea, mechanism, reduction, modeling insight, or
empirical pattern that makes the results important beyond the local details.
Distinguish direct support from interpretation when that distinction matters.

Include only the limitations that materially change how a headline finding
should be interpreted. Leave minor protocol inconsistencies, reconstruction
issues, implementation details, and reproducibility notes to **About the method
and experimental validation**.

Briefly explain what is genuinely new relative to close prior work. End with a
short account of the most important later developments, applications,
criticisms, or replacements; internet research is allowed for this part.
Detailed genealogy belongs in **Historical context**.

Prefer a small number of well-developed paragraphs over an inventory of
experiments, parameters, caveats, or implementation choices. Use formulas,
figures, or a compact table whenever they genuinely make the findings easier to
understand.

For an ordinary paper, aim for roughly **2–4 rendered pages**, longer only when
the paper genuinely requires it.


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
