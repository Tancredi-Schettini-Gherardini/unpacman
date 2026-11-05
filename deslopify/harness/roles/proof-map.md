---
name: proof-map
title: Optional pure-mathematics diagram
effort: high
writes: progress/reading/graph.json, progress/reading/map.mmd, progress/reading/map.pdf
---

# Optional one-page diagram

The user requested a diagram. Generate it only when the single paper's recorded
primary profile in `progress/reading/paper-profile.json` is `pure`. For an
`applied` profile, do not generate `graph.json`, `map.mmd`, or `map.pdf`. A mixed paper
follows its primary profile.

Show the principal proof's essential hypotheses, constructions, moves, claims,
conclusions, and gaps: normally 6--10 nodes, at most twelve unless the paper has
separate main routes. Use short labels (normally 2--6 words, at most eight), a
clear reading direction, few crossings, and text readable on one PDF page at
normal zoom. Remove redundant arrows: usually omit A -> C when A -> B -> C
already expresses the dependency. Simplify crowded diagrams rather than shrinking text.

The diagram uses `progress/reading/graph.json` with this structure, filled with
the actual source path and the paper's content:

```json
{
  "document": "path/to/source",
  "nodes": [
    {"id": "H0", "label": "Standing hypothesis", "description": "Its role and source locator.", "quote": "An exact passage from the source.", "parent": null, "uncertain": false},
    {"id": "Q1", "label": "Main conclusion", "description": "Its role and source locator.", "quote": "Another exact source passage.", "parent": null, "uncertain": false}
  ],
  "edges": [{"source": "H0", "target": "Q1", "type": "SUPPORTS", "justification": "Why the proof uses this input.", "uncertain": false}],
  "appeals": [{"line": 24, "verdict": "routine", "note": "Why the omitted step is routine."}]
}
```

IDs use H (hypothesis), G (goal), X (construction), M (move), C (claim), Q
(conclusion), or ! (gap), followed by digits. Every non-gap node needs a verbatim
source quotation of at least 12 characters. Arrows run from input to dependent
step, with a justification; types are SUPPORTS, MOTIVATES, or CONTRADICTS.
The graph is acyclic; gap nodes have no arrows. Mark uncertain interpretations.
Use `parent` only for useful one-level containment between nodes of the same type.

Run the skipped-work check and record every reported line in `appeals` as
`routine` or `gap`, with a reason. Preserve genuine gaps without completing the
source's argument. The commands below use the supplied source text, respecting
any source-scope limit; keep the original input path in the graph's `document`.
To create or repair a diagram, validate and render it:

```text
{deslopify} appeals {document}
{deslopify} check progress/reading/graph.json --document {document}
{deslopify} coverage progress/reading/graph.json progress/reading/guide.md
{deslopify} map progress/reading/graph.json --mermaid > progress/reading/map.mmd
{deslopify} map progress/reading/graph.json --pdf progress/reading/map.pdf
```

Inspect the rendered page for legibility. Review findings use `graph:<node>`,
`graph:<node>-><node>`, or `appeal:<line>`. After graph changes, validate and
regenerate both map files. For revision's `dispose` check, include
`--graph progress/reading/graph.json` and
`--before-graph {lane}/graph.json` when that snapshot exists.
