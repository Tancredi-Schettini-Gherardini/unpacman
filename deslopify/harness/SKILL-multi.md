---
name: deslopify-multi
description: Write a companion to several mathematical papers, with an optional opening overview of how they are related and one three-part mini-companion per paper.
---

# Method: a collection companion

This file defines the document shape and shared presentation rules for Expert
and Non-expert collection companions. The selected audience file and its
single-paper method supply the mathematical standards for each paper; this
collection method overrides their full single-paper output shape.

The input `{document}` contains the papers in order. Each starts with
`## Paper i of n: <title>`; its PDF is in `refs/`, and `[page k]` refers to
that paper's PDF. Treat every paper as an independent source. Open its PDF
when extracted text makes a formula, figure, table, citation, or locator
unclear.
If a source-scope notice says a paper was shortened for the reader AI, read
only the supplied beginning of that paper. Do not open later PDF pages to
fill the gap. State clearly in that paper's **Main results** or **Main
findings** that its source text was too long and the companion is based only
on the beginning. Apply this independently: other papers may be complete.

Write exactly one reader-facing file, `progress/reading/guide.md`.

## The non-negotiable collection shape

The document is a clearly separated sequence of focused, self-contained
mini-companions—one for each supplied paper—with an optional opening
**How the papers are related** section. It is not a concatenation of the usual
full single-paper companions. The application supplies the table of contents;
do not write a separate `## Contents` section or a manual contents list.

After the title, write exactly these reader-facing top-level parts:

1. only when the rule below is met, an opening `## How the papers are related`
   section; then
2. one paper part for every input paper, in input order.

Use exactly this heading pattern, replacing `k` with the paper's input-order
number and `Title of paper k` with its printed title:

```markdown
## Paper k: ``Title of paper k''
```

Enclose the title in two backticks and two apostrophes, as shown.
Immediately below each paper heading, put that paper's boxed source-and-scope
remark, before its Main results or Main findings subsection.

Every paper must start on a new page in the PDF, including the first paper
after the automatic table of contents or **How the papers are related**. The
PDF renderer inserts these page breaks at the paper headings; do not add raw
LaTeX or HTML page-break commands to the Markdown guide.

Each paper part must contain exactly these three subsections,
in this order. Use **Main results** for a pure paper and **Main findings** for
an applied paper:

1. `### Main results` (pure) or `### Main findings` (applied)
2. `### Historical context`
3. `### Bibliography`

Do not add **Main ideas**, **About the proofs**, **Q&A**, **Worked examples**,
**Where the document is thin**, a glossary, a proof survey, a collective
historical essay, or any other substantive section. Put a proof qualification,
notation explanation, limitation, example, or application interpretation into
the appropriate one of the three required subsections only when it is needed
to understand that paper's main result. Prefer omission to reproducing the
single-paper structure by another name. This restriction concerns extra
sections, not the depth of **Main results** or **Main findings**: preserve the
solo method's conceptual explanation within that subsection.

Use this literal heading pattern for pure papers. For an applied paper, replace
each `Main results` subsection with `Main findings`.
Do not vary `Historical context` to “Historical notes” or another name.
The entire **How the papers are related** section below is optional, including
its subsections:

```markdown
# <Short collection title>

## How the papers are related

### <Conceptual relationship between a naturally related subset of papers>

<One or more paragraphs explaining the relationship.>

## Paper 1: ``<title>''

**Remark (source and scope).** The source is *<title>* by <authors>, <date>. This companion is explanatory, not a certification of correctness; readers remain responsible for checking the source.

### Main results

...

### Historical context

...

### Bibliography

...

## Paper 2: ``<title>''

...
```

The application and PDF renderer provide navigation. Do **not** use raw HTML,
including `<a id=...></a>` or `<a name=...></a>`, or invent fragment URLs or
manually generated anchors.

## Optional: How the papers are related

Include `## How the papers are related` as the first section only when two or
more papers in the batch are clearly connected. Give it one or more
descriptively titled `###` subsections. Each subsection explains the higher-level conceptual
relationship between a naturally related subset of at least two papers;
use as many paragraphs as the explanation needs. Different subsections may
discuss different subsets, and unrelated papers need not be included.

In each subsection, explain the conceptual connections between the papers.
If a result in one paper implies a result in another, state this explicitly.
Give precise references and, where relevant, precise dates.

Do not add a separate final linking section.

## What goes in each mini-companion

Apply the selected single-paper method separately to every paper: classify it
as pure or applied (for a mixed paper, choose by its main contribution), then
read the corresponding profile file using the audience's routing instructions.
Preserve its source-fidelity and evidence rules;
and use the audience-appropriate level of mathematical detail. Record the
profile and reason for each paper in `progress/_session_notes.md`, not in the
reader-facing document. A mixed collection may contain both pure and applied
papers. Record all selections in `progress/reading/paper-profile.json` as the
router specifies. The collection structure takes precedence over each loaded
profile's single-paper structure.

### Main results

Follow the selected single-paper **Main results** or **Main findings**
instructions with the same depth and conceptual emphasis as in a solo
companion. The collection changes the outer headings, not the substance or
the space needed to explain a paper. Use as many paragraphs as understanding
requires. A reader should learn:

- the underlying question, stated independently of the paper's strategy;
- the headline theorem, claim, computation, or application result and its
  status;
- the objects and assumptions needed to understand the mathematical statements,
  or the model and evidence supporting applied findings;
- the precise central statement or decisive evidence when needed, with an
  exact source locator;
- the primary-versus-secondary hierarchy of conclusions; and
- the scope of the conclusion and any material open issue.

Begin with the concrete problem and headline result. For a pure paper, explain
the objects and assumptions needed to understand the statements; do not expand
proof ideas beyond what is needed to interpret them. For an applied paper,
explain the central model, mechanism, or methodological idea before technical
detail. Translate principal statements into plain language before
or alongside precise formulations. Make the primary-versus-secondary
hierarchy and the conceptual lesson explicit. Do not turn this into a
theorem-number catalogue or a proof outline. For an
applied paper, adapt to its main contribution: theorem, numerical method,
model, experiment, benchmark, software, survey, or ML result. State the
concrete question and headline findings early. Explain the model, method,
evidence, and limits needed to understand them; map important quantities to
real objects when relevant, and distinguish a practical target from its proxy
or benchmark when that affects interpretation. Give a theoretical model enough
detail to make its objects and assumptions intelligible. Keep the account
focused on the conceptual lesson and limits that change a headline finding.
Include the decisive experiment, numerical check, or proof idea in **Main
findings**, since the collection has no separate validation section. Define
central notation in plain language and cite its source.

### Historical context

Follow `SKILL-common.md`'s historical-research standard separately for each
paper, including References used in the proof for a pure paper using
non-standard tools. Keep that subsection nested within Historical context.
Give enough space to explain the paper's actual place in the subject without
turning its mini-companion into an autonomous history essay. Share searches
where useful, while giving each paper enough research and checking its
bibliography for consequential omissions.

### Bibliography

End each mini-companion with its own complete, locally usable bibliography,
following the shared citation and bibliographic standards. Include the paper
under study and every work cited in its Main results or Main findings and
Historical context. References used in How the papers are related belong in
the local bibliographies of the papers concerned. A source used for two papers
may appear in both; each entry must be cited in that mini-companion or a
relationship subsection involving it. Do not add a detached global bibliography.

## Collection-specific source discipline

- Name the paper and give a local page, section, theorem, equation, figure, or
  bibliography locator whenever a reader needs to distinguish sources.
- Do not merge hypotheses, notation, evidence, or claims across papers. Similar
  symbols can mean different things in different papers.
- Do not claim that the collection establishes a combined theorem, chronology,
  influence relation, or consensus unless the sources actually support it.
- If time is insufficient for equally deep historical research on every paper,
  keep all Main results or Main findings faithful, state the limitation within
  the affected paper's Historical context, and record deferred work in session notes. Do not
  fill gaps with generic prose.

Before finishing, verify that the reader-facing document contains only the
optional opening **How the papers are related** section with its conceptual
subsections, followed by the three required mini-summary subsections for each
paper. Check that each paper starts on a new PDF page. Then write `progress/_session_notes.md`
with each paper's pure/applied classification, the sources most difficult to
verify, and historical work left incomplete.
