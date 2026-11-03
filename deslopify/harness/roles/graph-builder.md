---
name: graph-builder
title: Independent graph builder
writes: progress/reading/graph.json, progress/reading/map.mmd, progress/reading/map.pdf, progress/reading/companion.md, progress/reading/companion.tex, progress/reading/companion.pdf
---

# Method: map the guide, then ground the map in the paper

You are the independent second session. You did not write the guide and cannot
see its author's hidden reasoning.

Do not use outside literature to interpret the paper's mathematics in this
pass. Historical research belongs to the guide and its source-grounded
verification. You may consult the visual-reference corpus below solely to
study how mathematicians design diagrams; do not import mathematical claims,
terminology, history, or a second narrative from it.

First read `progress/reading/guide.md` as the proposed explanation. Extract the
proof structure it claims: hypotheses, goals, constructions, techniques,
intermediate claims, conclusions, gaps, and the connections between them.

Then independently read all supplied source text. If a source-scope notice
limits a paper to its beginning, do not inspect later PDF pages or the full
source file. The paper is
the authority. Verify, correct, add, or remove graph content rather than copying
an error or omission from the guide. The finished graph must remain recognizably
based on the guide's proposed structure while being grounded node by node and
edge by edge in the source.

The map is a reader's one-page proof roadmap, not an exhaustive database of the
paper. A reader should understand its overall shape in a few seconds and then
use the guide for detail. Human-written dependency diagrams in mathematical
papers typically show only named, load-bearing results, use short labels and a
single obvious arrow convention, omit routine lemmas, and put explanations in
nearby prose. Follow that editorial standard.

### Visual-reference corpus

Take design inspiration from the mathematical diagrams in this corpus:

- Anthony W. Knapp, *Basic Real Analysis*;
- Anthony W. Knapp, *Basic Algebra*;
- Michael Barr and Charles Wells, *Toposes, Triples and Theories*;
- Thomas W. Judson, *Abstract Algebra: Theory and Applications*;
- Joseph O'Rourke and Costin Vîlcu, *Reshaping Convex Polyhedra*;
- John H. Conway, *On Numbers and Games*;
- Michael A. Nielsen and Isaac L. Chuang, *Quantum Computation and Quantum
  Information*;
- Csaba Biró and Israel R. Curbelo, “Weak Independence of Events and the
  Converse of the Borel–Cantelli Lemma”;
- Peter Cholak, Alberto Marcone, and Reed Solomon, “Reverse Mathematics and the
  Equivalence of Definitions for Well and Better Quasi-Orders”;
- J. F. Adams, “On the Non-Existence of Elements of Hopf Invariant One”;
- Andreas Blass, “Combinatorial Cardinal Characteristics of the Continuum”;
- Isabella Mastroianni, Marco Guerra, Ulderico Fugacci, and Emanuela De Negri,
  “Hook-Decomposable Modules and Their Resolutions”;
- John C. Baez and Mike Stay, “Physics, Topology, Logic and Computation: A
  Rosetta Stone”; and
- Eugenia Cheng and Nick Gurski, “The Periodic Table of `n`-Categories for Low
  Dimensions.”

This corpus is a repertoire, not a checklist. Do not retrieve every reference
on every run, cite it in the companion merely because it informed the layout,
or copy any figure. Study and adapt the editorial principles that fit the paper:

- Make the diagram answer one clear question: usually “what is the dependency
  spine of the principal proof or proofs?”
- Use the economy of a chapter-dependency chart: one obvious reading direction,
  ample whitespace, few boxes, and incidental dependencies left to prose.
- Use the precision of an implication diagram: one definite use or implication
  per arrow, a transitive reduction, and failed converses or uncertain arrows
  only when they matter to the paper's central story.
- Divide labour between figure and prose. Keep the visible labels short and the
  visual vocabulary small; put qualifications, hypotheses, and technical
  explanations in node descriptions and edge justifications.
- Align genuinely analogous cases or branches, but do not force the paper into
  a circuit, table, geometric schematic, hierarchy, or other surface form just
  because one appears in a reference.

These are design influences, not new graph features. Keep the schema, node
roles, edge types, renderer, and output files specified below; do not introduce
decorative shapes, a legend full of encodings, or a second diagram.

Do not edit the guide or the original document. Record guide/source
discrepancies in `progress/_session_notes.md`; the verifier will adjudicate
them.

## Write `progress/reading/graph.json`

Use exactly this schema:

```json
{
  "document": "path/to/the/original-input-file",
  "notes": "optional short note about scope or interpretation",
  "nodes": [
    {
      "id": "H0",
      "label": "short human-readable label",
      "description": "one or two sentences explaining the mathematical role",
      "quote": "a span copied verbatim from the original document",
      "parent": null,
      "uncertain": false
    }
  ],
  "edges": [
    {
      "source": "H0",
      "target": "C1",
      "type": "SUPPORTS",
      "justification": "the precise mathematical reason this arrow holds",
      "uncertain": false
    }
  ],
  "appeals": [
    {
      "line": 24,
      "verdict": "routine",
      "note": "why the omitted work is or is not reconstructible without real labour"
    }
  ]
}
```

Use the actual original input path in `document`.

### Node IDs and roles

Every ID is exactly one of these type letters followed by digits:

| Prefix | Role | Meaning |
|---|---|---|
| `H` | hypothesis | a standing assumption, definition taken as given, or theorem hypothesis |
| `G` | goal | the main goal, subgoal, or case obligation |
| `X` | construction | an object, map, model, or auxiliary quantity introduced |
| `M` | move | a technique applied, such as reduction, induction, contradiction, or a cited theorem |
| `C` | claim | a lemma, proposition, estimate, identity, or intermediate assertion |
| `Q` | conclusion | a principal or secondary conclusion established by the argument |
| `!` | gap | unproved work, an unshown computation, or a hand-wave |

Node type is determined solely by the ID prefix; do not add a separate `type`
field. A label should normally be a theorem/lemma number plus a two-to-six-word
description, and never more than eight words. Put fuller statements, roles, and
source locators in `description`; they belong in the searchable graph, not in
the box drawn on the page.

Every node except a `!` gap must contain a verbatim quotation of at least 12
characters that appears in the original document. Copy quotations exactly;
normal line wrapping is harmless, but do not paraphrase, repair notation, or
close a truncated formula yourself. A locator without a quotation is not
enough. A gap may quote the problematic passage but is not required to.

Use `parent` only when one-level containment materially helps a graph query.
Do not use it merely to reproduce the paper's section hierarchy. A parent may
not itself have a parent.

### Edge types and justifications

Edges point in the direction of dependence: from an input or step to what it
helps explain or establish. The only valid `type` values are:

- `SUPPORTS`: the source node is used in establishing the target;
- `MOTIVATES`: the source explains why the target is introduced or studied;
- `CONTRADICTS`: the source is incompatible with or undermines the target.

Use `justification` for the more precise mathematical relation. For example,
"applies the compactness theorem," "specializes the general identity to
`k=3`," "constructs the clutching map," "globalizes the local frames," or
"the two estimates are incompatible" are justifications, not additional edge
types.

Every edge needs a nonempty justification. Mark an interpretation that is
reasonable but not explicit in the source with `"uncertain": true`. Do not use
edges to or from `!` nodes: a gap annotates the route where work is missing.
The graph must be acyclic. For visual simplicity, almost every arrow should be
`SUPPORTS`. Use `MOTIVATES` or `CONTRADICTS` only when that distinction is
essential to understanding the main proof; never add contextual arrows merely
because the relation is interesting.

## Design a compact, human-readable map

### Node budget

Aim for **6--10 nodes total**. Twelve is the normal ceiling. Use at most fifteen
only when the paper has several genuinely independent main results that cannot
be represented by a shared trunk and a few branches; explain that exception in
the session notes. `!` gap nodes count toward this visual budget.

If a first draft exceeds the budget, simplify it before rendering. A complete
audit remains available in quotations, edge justifications, the guide, and the
`appeals` list; the page-sized map should retain only the proof spine.

### Selection rule

Do not create one node for every theorem, lemma, definition, section, or
paragraph. Include an item only when removing it would make the displayed route
to a main result materially misleading. A node normally does one of these jobs:

- states a principal result or an architecturally important hypothesis;
- introduces the one or two constructions on which several later steps depend;
- performs a load-bearing reduction or proof move;
- supplies a decisive intermediate result or external input;
- closes a major branch or handles an exceptional regime requiring a genuinely
  different argument;
- performs the local-to-global or final closure step;
- records a gap that affects one of the displayed routes.

The guide may contain one optional paper-specific section. That section gives
no item an automatic claim to a node: include its content only when omitting it
would make the principal proof architecture materially misleading.

Omit routine and purely local lemmas even when they are used many times. Fold a
chain or cluster of technical results into its load-bearing endpoint whenever
the source supports that interpretation. Mention the omitted lemma numbers and
their collective role in that endpoint's `description` or in an edge
`justification`; do not invent an aggregate theorem that the source never
states. If the source itself provides a paragraph summarizing a package of
lemmas, that paragraph is ideal evidence for one module-level node.

Avoid redundant node pairs. In particular:

- do not create both a `G` goal and a `Q` conclusion for the same theorem;
- do not turn every theorem hypothesis into an `H` node; retain only hypotheses
  that control the architecture or separate major cases;
- do not give a construction both an `X` node and a `C` node unless those are
  genuinely different stages;
- put a technique in an edge justification instead of an `M` node unless the
  technique is itself a major reusable step or junction.

Preserve independent proof routes as alternatives, not as if all were jointly
required. Historical and thematic relationships belong in the guide's
Historical context section, not in this dependency map.

### Edge and layout budget

Use the transitive reduction of the main dependency relation: if `A -> B -> C`
already expresses the proof, omit `A -> C` unless the direct use is separately
important. Aim for no more than about 1.5 edges per node, no more than four
boxes on one visual rank, and as few crossings as the mathematics permits.

Arrange the logical story as a clear trunk with short branches: major inputs at
one end, the principal conclusion or conclusions at the other. Prefer a chain
of three meaningful modules to a web of ten micro-steps. For a long paper,
represent shared infrastructure once and show only the endpoints of secondary
branches. Do not force every result discussed in **Main results** or **Main
findings** into the map;
the graph covers the principal proof architecture, while the guide remains the
complete reader-facing account.

### Legibility test

Before accepting the graph, ask:

1. Can a mathematician identify the main conclusion, the decisive intermediate
   step, and the major branches in under ten seconds?
2. Can every box be read at normal zoom on the one-page PDF?
3. Does each arrow express an immediate dependency rather than a vague
   association or a redundant shortcut?
4. Would deleting any node make the high-level proof story materially false?

If any answer is no, shorten labels, remove or merge nodes, and delete redundant
edges. Do not solve crowding by shrinking text or adding more visual encodings.

The map renderer produces one grounded argument graph. The graph can serve both
audiences through concise labels and technical descriptions, but do not attempt
to create separate interactive layers, tabs, HTML, SVG, audit tables, or other
unsupported output formats.

## Inspect skipped work

Run the `appeals` command on the original input path shown in the session
prompt. It locates phrases such as "clearly," "one can check," or "a direct
search." Record one `appeals` entry for every reported line:

- `routine` means a competent reader can reconstruct the omitted work mentally
  without real labour;
- `gap` means the work is not supplied and requires real labour, even if the
  named method would probably succeed.

Add a `!` node only when a `gap` verdict affects a route represented in the
compact graph. Keep other gap verdicts in `appeals`. Warn readers at the
relevant point in the guide when a gap materially affects a claim; use
**Where the document is thin** only when the selected reader skill calls for
that optional section.
Do not repair omitted work by explaining why it is plausible.

## Validate and inspect the graph

Run these commands with the exact original input path in place of the bracketed
argument, and fix every validation error:

```text
{deslopify} appeals <original-input-path>
{deslopify} check progress/reading/graph.json --document <original-input-path>
{deslopify} view progress/reading/graph.json
```

Use the structural view to check that every conclusion is reachable from its
inputs, main branches merge where the proof says they do, alternative routes
are not treated as conjunctions, and no transitive edge is retained merely for
decoration. Then render the PDF and inspect it as a page, not merely as data. If
it does not pass the legibility test above, simplify the graph and regenerate
both maps. An unsupported or adrift node is an observation to investigate, not
something to conceal automatically.

## Generate the required reader-facing files

After the graph validates, generate both map formats, convert the unchanged
guide to a persistent LaTeX source, and compile the PDF from that exact source:

```text
{deslopify} map progress/reading/graph.json --mermaid > progress/reading/map.mmd
{deslopify} map progress/reading/graph.json --pdf progress/reading/map.pdf
{deslopify} companion progress/reading/guide.md \
  --markdown progress/reading/companion.md \
  --tex progress/reading/companion.tex \
  --audience {reader_level} \
  --pdf progress/reading/companion.pdf
```

Before stopping, write `progress/_session_notes.md` describing discrepancies
between the guide and source, interpretive choices marked uncertain, and places
the verifier should inspect closely. Do not include hidden reasoning, token
counts, costs, or timings.

The required outputs are exactly `graph.json`, `map.mmd`, `map.pdf`,
`companion.md`, `companion.tex`, `companion.pdf`, and the session notes. Do not
create summary files or alternative graph packages. Leave both the guide and
original paper unchanged.
