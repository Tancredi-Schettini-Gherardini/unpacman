---
name: deslopify-common
description: Shared source, presentation, statement, illustration, historical-research, and citation rules for every paper companion.
---

# Shared companion instructions

These rules apply to Expert and Non-expert, pure and applied companions.
The selected profile supplies the audience and section content; in a collection,
the collection method controls the outer structure and local bibliographies.
Suggested counts of words, pages, paragraphs, ideas, proof stages, or questions
are guidance, not quotas; adapt them to the paper and the reader's understanding.
Follow the stage deadline supplied by the runtime.

## Source and continuity

Read all supplied source text before drafting. For a new reading, do not consult
companions from earlier runs. For an explicit continuation, reuse the saved
draft, research, figures, and review supplied in the workspace.
If a source-scope notice limits a paper to its beginning, do not inspect later
PDF pages or the full source file. State clearly in that paper's Main results
or Main findings that the source text was too long and the companion is based
only on its beginning. Preserve this limitation through revision.

Never edit the source or silently repair its proof, model, computation,
experiment, or interpretation. Preserve hypotheses, quantifiers, conventions,
scope, and claim strength. Distinguish what the paper proves or claims, computes
or observes, imports, and leaves heuristic, ambiguous, or open, where this helps
understanding. Warn about a substantial gap where the reader encounters it;
do not close it by explaining why the missing method would probably work.
Identify a companion inference when it materially affects the account.

Use supplied appendices, code, data, notebooks, model cards, documentation,
benchmark papers, incident reports, related papers, errata, and author responses
only for their actual role. State what was inspected or rerun; never imply that
unavailable material was checked or let it replace the primary paper as evidence
for that paper's claims.

If a problem, question, conjecture, heuristic, theorem, or other mathematical
idea has a well-established name, use it; add a brief reference if needed.

## Title and source remark

For a single paper, use exactly the applicable first-level title:

```text
# Reader's companion to ``<paper title>'' -- expert mode
# Reader's companion to ``<paper title>'' -- non-expert mode
```

Use the paper's printed title, two backticks before it, two apostrophes after
it, and the double hyphen shown. Add no author or further suffix. These quotes
render as double quotation marks in the web companion and PDF. A collection
uses its own collection title.

Every companion must contain a boxed remark identifying the source paper by
title, authors, and date, together with the correctness notice. For every solo
companion, put it immediately after the main title, before any opening lead or
background section. In a collection, put
each paper's own remark after its paper heading and immediately before its first
subsection, Main results or Main findings. This applies to both audiences and
pure, applied, and mixed papers. Use the source's stated date or version date,
or its year when that is all that is available; never invent a date.

```text
**Remark (source and scope).** The source is *<paper title>* by <authors>, <date>. This companion is explanatory, not a certification of correctness; readers remain responsible for checking the source.
```

Keep source identification and the notice together in this remark. Do not add
a separate routine publication, provenance, or verification paragraph before
it, such as "The complete supplied source is the ... preprint." Keep material
source-scope restrictions and mathematical limitations visible where needed.

## Presentation and clarity

Write clear, natural prose with a conceptual hierarchy. Explain specialized
terms and symbols near first use, put conceptual purpose before paper-specific
formalism, and keep assumptions, results, interpretation, and limitations
distinct when it matters. Prefer synthesis of the few important findings and
mechanisms to inventories of lemmas, experiments, parameters, or caveats.
Use explicit referents when shorthand is ambiguous; modest repetition can help.
Avoid formulaic phrases, rhetorical questions as headings, and compressed
slogans. Clarify vague language with a sentence, concrete example, or formula.

Use explicit formulas when they clarify an object, relationship, claim, or
mechanism. State the domains, assumptions, normalization, and limiting process
needed to read each formula. Use parallel terminology for analogous objects
and explain where an analogy ends when relevant.
State important claim status directly: proved, computed, observed, assumed,
conjectured, or inferred. Avoid stock "X, not Y" disclaimers against stronger
claims the paper never makes. Discuss a stronger unestablished conclusion only
when the source claims it or a reader might reasonably infer it.

Treat discrepancies proportionately. Mention a conflicting count or description
once beside the affected result or figure, without guessing a reconciliation.
Put reconstruction-only details in session notes; use a separate subsection
only for an unresolved discrepancy materially undermining a central conclusion.

### Statements and boxes

Use a left-aligned academic statement environment when explicitly restating a
special mathematical item: Theorem, Proposition, Lemma, Corollary, Conjecture,
Question, Problem, Algorithm, Definition, Claim, Assumption, or Hypothesis.
Preserve the source's type and number. Put the citation immediately after the
type, including the source item number or locator. An established name or a
qualifier such as `(informal)`, `(companion formulation)`, or `(selected assertions)`
follows the citation. Do not repeat the citation at the end.

```text
**Theorem [Abc00, Theorem 3.2].** *Let ... . Then ... .*

**Algorithm [Abc00, Algorithm 1].** *Input: ... . Output: ... .*

**Question [Abc00, §1].** *Does ... ?*
```

Every box must contain a coherent, grammatically complete statement of the
claim it presents. Never leave half a sentence or a continuation of its
conclusion outside the box. Hypotheses, definitions, or equations may appear
elsewhere in the companion if the box clearly identifies and references them
by companion label or descriptive heading. The statement and those explicit
references must determine its material hypotheses, quantifiers, parameter
range, exceptions, and full conclusion. A source citation alone does not fill
an unstated part. Identify selected assertions from a longer theorem and state
each selected assertion coherently within that scope.

For a previously introduced formula used in a statement, refer to its companion
number and role inside the box. Put a formula first introduced with the statement
in a `$$ ... $$` display immediately after the labelled paragraph so the renderer
keeps it in the same box. Do not add a separate provenance paragraph merely to
say where the statement came from. The writer supplies this Markdown; the
auditor checks the rendered boxes after exports are available.

The renderer displays understated pastel boxes with a lightly tinted background,
fine border, generous padding, and dark high-contrast text, using a consistent
pastel family by statement kind and never color alone for logical status.
Do not fake boxes with tables, blockquotes, raw HTML, or raw `\begin{theorem}`
syntax. Blockquotes are reserved for genuine quotations; the source-and-scope
remark uses the labelled Markdown form above.

### Illustrations

Inspect illustrations in the source paper within the available source scope.
For solo companions, if the source paper already contains a diagram that
summarizes a substantial part of the paper, reproduce that existing image in
the companion, whether or not a new proof map is requested.

Include other source illustrations when they materially clarify central
objects, results, methods, or evidence. Choose the clearest when several
serve the same purpose.

Place an image near the first discussion that needs it. Give a short, natural
caption saying what it shows; the source caption may be used when suitable.
Add a precise reference such as `[Abc00, Figure 1]`. Identify companion-created
images and cite their source data or material. Put further explanation or
limitations in nearby prose.

Copy source illustrations faithfully: preserve data, axes, legends, annotations,
aspect ratio, and comparisons. For a page-region crop, render the source page
at 400 DPI (for example `pdftoppm -r 400`) and crop without downsampling. Do not
substitute a viewer screenshot or upscale a low-resolution crop. Inspect the
saved crop at its intended display size: retain complete panels and readable
labels without unrelated page text or excessive blank margins. If clean
extraction is unavailable, explain the figure in words and record the reason
internally. Save images under `progress/reading/figures/` and embed them with
ordinary relative Markdown image syntax.

### Structure, equations, and navigation

Use a short list or compact table for genuinely parallel assumptions, cases,
stages, guarantees, or limitations; use prose for a continuous argument.
Subsection titles should identify the result, obstruction, object, or mechanism,
without generic labels, long undivided walls of text, or one-sentence fragments.
Use proportionate granularity: a result or tightly related family in Main results
or Main findings should typically take less than half a rendered page unless
the statement needs more space. Apply the same judgment to ideas and proof or
validation stages, treating these lengths as suggestions.
Reproduce central formulas needed to understand a result or mechanism rather
than requiring readers to retrieve them from the source. Introduce only the
paper-specific symbols needed locally.

Number displayed equations `(C.1)`, `(C.2)`, and so on only for later reference
or useful navigation. Cite copied or adapted formulas compactly, for example
`[Abc24, equation (6)]`. Explain combined or companion-derived formulas only
when it matters; do not add mandatory Source correspondence blocks. Visual
arrow diagrams need no number.
Use `$...$` inline and `$$...$$` for displays, never `\(...\)`, `\[...\]`, or
code formatting for mathematics. Reserve code formatting for filenames,
commands, and identifiers. Name distant companion targets by a descriptive
heading or local label rather than "as above"; a cross-reference does not
replace a local reminder needed for understanding.

## Historical context and literature research

Treat Historical context as substantial mathematical or scientific scholarship,
usually about 1,000--2,000 words for an ordinary paper, adapted to its complexity
and the collection format. Explain the conceptual and chronological arc: what
relevant works contributed, how ideas moved, and where the present paper fits.
Give non-experts enough local explanation of unfamiliar techniques to follow
the connections. A broad useful bibliography is preferable to artificial sparsity.
Coordinate with any separate proof or method section by cross-reference:
explain mechanisms there and their provenance and development here.

For a pure paper whose proofs use non-standard tools, include a subsection
called **References used in the proof** within Historical context (`###` in a
solo companion, `####` within a collection paper's `### Historical context`).
Explain the main references from which the proof ideas and arguments originate,
what each contributes, and how it is used, with precise references and source
locators.

Research independently from the problem, objects, invariants, proof mechanisms,
decisive intermediate results, distinctive formulas, and neighboring problems.
The source introduction and bibliography are evidence and leads, not the search
boundary or default ranking. Compare them with independent literature, including
parallel developments, earlier formulations, and less-famous contributors.
For applied papers also search from the application, model class, target and
proxy, method, dataset or benchmark, empirical protocol, and proposed use.

Follow these complementary routes as far as the deadline permits:

1. Inventory the source's references, introduction, historical remarks,
   acknowledgments, analogies, conjectures, prior bounds, obstructions, and
   classification results for important origins and external inputs.
2. Retrieve close predecessors, competitors, extensions, and alternative
   treatments. Inspect their introductions, principal statements, relevant
   related-work discussion, and citation neighborhoods. Follow their references
   backward toward recognizable foundations while new sources add explanatory
   value. Record important inaccessible sources internally.
3. Search forward for refinements, corrections, critiques, replications,
   replacements, generalizations, refutations,
   applications, reinterpretations, independent proofs, counterexamples, surveys,
   errata, and repairs. Give consequential later lineage real space, explaining
   what each stage retained, changed, and enabled. For applied work, trace
   documented paths through intermediary methods to research applications,
   deployed technologies, or products; cite the intermediate links and evidence
   of use rather than inferring adoption from resemblance. Go beyond the first
   follow-up when further consequential stages matter.
4. Search beyond the citation network using problem or theorem names, variants,
   predecessor terminology, synonyms, authors, formulas, and neighboring fields.
   Investigate meaningful connections through mechanisms such as gluing,
   localization, duality, deformation, concentration, singular perturbation,
   or convex relaxation; shared vocabulary alone is insufficient.
5. Use authoritative surveys, monographs, textbooks, lecture notes, and historical
   introductions to discover missing strands, compare traditions, check chronology,
   and find good modern entry points. Verify consequential claims against original
   sources where possible.

Cover foundational formulations, directly used external results, closest
predecessors and competing approaches, negative, rigidity, no-go, or counterexample results,
obstructions, corrections, useful neighboring traditions, and important later
work when applicable. Explain enough mathematics for each connection to be
meaningful without writing a proof survey or copying every source reference.
Include origins and changing formulations of the problem, early examples and
structural constraints, the strongest earlier approaches and their limits,
failed or superseded routes, and contemporaneous contributions when relevant.
Credit both origins and useful modern expositions; explain how important ideas
travel between problems and identify good entry points to material actually used.

Organize by descriptive thematic or chronological subsections, not one heading
per citation or an author-by-author list. When useful, show a conceptual genealogy
of the problem, approaches, benchmarks, imported techniques, present contribution,
and unresolved branches. Include a comparison table when it materially helps
readers understand the important sources, using columns such as:

| Reference | Mathematical or research strand | Main result or idea | Relation to this paper | Important difference |
|---|---|---|---|---|
| ... | ... | ... | ... | ... |

Include **Where the present paper fits**, explaining what it inherits, combines,
changes, or plausibly adds and what remains unresolved. Close with a brief
**Literature-search scope and limitations** stating the cutoff and material
limits affecting the historical account or claims, including consequential
inaccessible or language-limited sources. Keep detailed databases,
queries, search routes, citation chains, inspection logs, and pending checks
in `progress/_session_notes.md`; never present a bounded search as exhaustive.

### Evidence, relevance, and credit

Distinguish documented influence or explicit use, a direct mathematical
predecessor technically used or generalized, and structural analogy without
documented transmission. Do not infer influence from chronology or resemblance,
or project modern terminology backward without support. Before finalizing,
reconcile the independent search with the source's introduction and bibliography
and check for parallel credit. Use `introduced`, `conjectured`, `first proved`,
`independently proved`, `simplified`, `generalized`, `strengthened`, and
`gave a modern exposition` precisely; narrow unsupported priority claims.

Include a useful provisionally related reference when its identity is confirmed
by an authoritative record or an explicit source-bibliography entry and a
concrete basis for relevance is clear. Label the connection provisional;
it cannot support an unchecked theorem, technical dependency, influence, or
priority claim. Do not remove a useful lead merely because full text is
unavailable, or pad the bibliography with superficial similarities.

If the source alleges an error, gap, missing hypothesis, or other consequential
defect in earlier work, report it neutrally and visibly with exact locators to
both works. Inspect the criticized work where possible and search for errata,
responses, and repairs. Distinguish the allegation, the earlier work's actual
content, and what an independent check could establish.

Use web search for discovery, not as evidence by itself. Verify substantive
claims and metadata through original papers and authoritative publisher,
journal, proceedings, institutional, DOI, or arXiv records. Prefer the relevant
published version; use a relevant or only-available preprint with version/date
when material. Surveys and textbooks are valuable leads and synthesis, but
consequential priority claims need original support or explicit secondary
attribution naming the intermediary. Do not rely on snippets, blogs, social
media, unsourced summaries, or automated bibliographies for priority.

Keep an internal coverage inventory: source-reference and companion external-
reference counts, major retained or omitted works and why, independently found
candidates, and inspection status. For non-routine historical attributions,
record the claim, contribution type, source/locator, inspection status, and
parallel credit internally. Resolve source disagreements explicitly; narrow
claims when time or access prevents checking an important strand. A generic
scope disclaimer does not replace naming a material omission.

## Citations and bibliography

Cite principal claims, decisive formulas, unusual definitions, important external
inputs, substantial comparisons, and non-routine attributions where used.
Outside Historical context, routine explanatory prose needs no locator.
Historical, priority, influence, correction, and related-work claims require
claim-level citations. A compact locator is enough; avoid separate provenance
paragraphs. In prose, also name authors and year when introducing an important
predecessor, theorem used, major comparison, or disputed result, for example
`Tax and Duin (2004) [TD04]`; this does not replace the citation key.

Use standalone compact alphabetic keys, never opaque source numbers such as
`[17]`: first three surname letters plus two-digit year for one author (`[Abc89]`),
surname initials for two or three (`[AB89]`, `[ABC89]`), or first three initials
and `+` for more (`[ABC+89]`). Use ASCII keys and append `a`, `b`, etc. for
collisions. Preserve author order, names, and diacritics in entries.
Keep every work in its own brackets with its own locator:
`[Abc00, Theorem 1][Xyz00]`, never `[Abc00, Theorem 1; Xyz00]`, `[Abc00; Xyz00]`,
or `[Abc00, Xyz00]`.

For each source of roughly 50 pages or fewer, use a default ceiling of 50
distinct entries, including the source; scale proportionately for longer
papers. This is a navigation guard, not a quota. Near the ceiling prioritize
foundations, direct dependencies, close predecessors, competitors, negative
results, corrections, major later developments, and useful neighboring
connections over redundant variants.

For a solo companion, finish with the unnumbered heading
`## References cited in this companion`, after all substantive sections.
Collections use the specified local Bibliography for each paper. Cite the
source under study and every external work used; every key must resolve to one
entry in its bibliography, and every listed entry must be cited in the prose.
Use one bullet per entry, never tables, manual hanging indents, or long labels:

```text
- [Abc89] Full bibliographic entry. [doi:...](https://doi.org/...)
```

Give all authors, full title, venue, volume/issue, year, page range or article
number where applicable, and a verified DOI or stable arXiv link when available.
Include edition/publisher for books and editors/container title for chapters
or proceedings when available. Use short scholarly link labels. Never invent
missing metadata; retain verified initials when full names are unconfirmed.

Put editorial notes, including "Metadata from ...", metadata-provenance comments,
and pending verification tasks, in `progress/_session_notes.md`, never the
companion or its bibliography. Preserve substantive qualifications affecting
the source, its claims, or a reference's relevance. If a material uncertainty
prevents a fair history, explain its effect briefly in the historical scope
limitation and narrow the affected claims.
