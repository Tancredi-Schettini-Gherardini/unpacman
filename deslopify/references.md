# References read for deslopify

The sources themselves are cloned or downloaded into `refs/`, which is
gitignored: they are other people's work, fetchable from the URLs below.

## Given

### 1. math-prose — <https://github.com/TianhuaGao/math-prose> (MIT)

An Agent Skill for drafting, revising and auditing English mathematical prose
*without changing the mathematics*.

- **What it contributes.** A **31-code taxonomy of mathematical behaviour** in
  six groups (declaration/scope, representation/construction,
  transformation/equivalence, inference/results, dynamics/computation,
  interpretation/comparison). Each code carries licensed constructions and, more
  usefully for us, **misuse boundaries** — the conditions under which a
  construction overstates. Also a model of how to build a skill: short
  `SKILL.md`, detailed `references/` loaded on demand, an evidence corpus, a
  validator script, and a forward-test protocol.
- **Why it matters here.** To explain what a proof does, you must first classify
  what each sentence does. This taxonomy is that vocabulary, already built and
  boundary-tested. Its `proof-and-claim-language.md` is a ready-made check on
  claim strength, which is what stops an explanation from quietly promoting a
  hedge into a theorem.
- **What it is not.** It *rewrites*. deslopify does not. Its `audit` mode is the
  closest precedent: analysis returned as findings, the text left alone.

### 2. ProofRank — <https://github.com/insait-institute/proofrank>

Code for *Not All Proofs Are Equal: Evaluating LLM Proof Quality Beyond
Correctness* (NeurIPS 2026 submission, INSAIT). LLM-as-judge pipelines scoring
proofs on conciseness, computational ease, cognitive simplicity, diversity and
adaptivity.

- **What it contributes.** First, a **`core_ideas` extractor** that decomposes a
  solution into *family* (viewpoint), *backbone* (main structural move) and
  *closing engine* (the decisive theorem), with mandatory `evidenceQuotes`, a
  `confidence`, and a `noCoreIdea` flag — under strict instructions not to solve,
  repair or fill gaps. That is a direct, disciplined answer to "what is the main
  idea of the proof?". Second, and more subtly, it **separates two kinds of
  difficulty** that are usually conflated.
- **The distinction that matters most.** *Ideological complexity* is the
  ingenuity of the key move, the niche-ness of the tools, and the burden of
  integrating several ideas. *Computational/cognitive load* is time-to-follow as
  written: notation density, symbol tracking, definition hygiene, bookkeeping.
  ProofRank judges them with separate rubrics and explicitly forbids each from
  contaminating the other.
- **Why that matters here.** The two call for different artefacts. Load is a
  presentation problem and a map or a table of notation genuinely dissolves it.
  Ingenuity cannot be dissolved by presentation — but it can be *named and
  located*, which is what the main-idea artefact is for. An aid that confuses the
  two will try to make a deep proof shallow, and fail.
- Also useful: `verbosity_verifier` is a **fidelity gate** in miniature — no new
  information, meaning unchanged, nothing critical dropped, answer boxed yes/no.

### 3. Simon, *Towards Bidirectional Human–AI Scientific Reasoning: From Concept-Role Graphs to Negotiated Interpretation* (UCLA MS thesis, 2026)

<https://escholarship.org/uc/item/46p5h47f> — the Syrup system.

- **The representation.** A **concept-role graph**: typed nodes (assumption,
  purpose, method, result, conclusion, plus *limitation* as an edgeless
  meta-node annotating a path), typed edges (MOTIVATES, LEADS_TO, CHALLENGES)
  each carrying a justification, single-level same-type containment, kept a DAG
  by a cycle check at insertion. Every node carries a short label, a 1–2 sentence
  description, and **a verbatim quote from the source**. Node IDs are stable and
  type-prefixed (`A0`, `M2`, `R3`), so answers can point at the picture.
- **The result that should govern our design.** A single LLM pass does **not**
  produce a canonical graph. Cross-run node agreement was **0.30–0.40**, edges
  lower; only a third to a half of nodes survive between runs. Three named
  failure modes: assumptions and method nodes are least stable (implicit
  assumptions surfaced unevenly; `method` vs `methodology` drift), **edge types
  drift** between the same pair of nodes, and **paraphrase defeats embedding
  matching**. The productive reading: a **consistent core** is non-empty for
  every paper — agreement is partial, not absent.
- **Grounding works.** An audit of **319 evidence quotations across 7 papers
  found none fabricated**; 96.2% verbatim, the 3.8% residual being PDF
  extraction artefacts. Mandating a verbatim quote per node essentially
  eliminates invention. The thesis is careful that this speaks to *grounding,
  not interpretation*: it does not show the assigned roles and edges are right.
- **Serialization, measured.** An LLM sees a string, not a graph. Across six
  encodings and three frontier models: **compact text is best on the
  accuracy–token frontier; an ASCII layered DAG is the cheapest competitive
  option; JSON is dominated** — more expensive *and* less accurate. Reachability,
  cycle detection and unsupported-node identification are legible from plain text
  (0.90–0.97) and need no precomputation; **centrality is essentially
  unrecoverable (0.07–0.29) and must be computed and injected**.
- **Two mechanisms to steal.** `inspect_structure` detects *unmotivated methods,
  unsupported conclusions, bottleneck nodes and disconnected subgraphs* — i.e.
  argument defects found structurally rather than stylistically. And
  **soft recompilation**: deleting a node runs a pass over its downstream cone
  and surfaces the affected nodes as accept-or-reject proposals.
- **Its design implications, verbatim in spirit.** Make interpretive commitments
  visible, because they are most useful *when wrong* — a mismatched role gives
  the reader something specific to contest. Treat stability as an empirical
  property to be measured, not a consequence of having a schema. And note the
  honest limit: *an extractor that produces the same wrong graph every run is
  perfectly consistent*, so agreement is not fidelity.

## Found while reading

### 4. Mejía-Ramos, Fuller, Weber, Rhoads & Samkoff, "An assessment model for proof comprehension in undergraduate mathematics", *Educational Studies in Mathematics* 79 (2012), 3–18

The missing piece: the only systematic account of **what it means for a human to
understand a proof**, built to be *assessed*. Seven facets, split local/holistic.

| | Facet |
|---|---|
| Local | **L1** meaning of the terms and statements, including the claim being proved |
| Local | **L2** justification of assertions — how each new line follows from earlier ones |
| Local | **L3** the proof framework — direct, contradiction, induction — and how the assumptions and conclusion sit inside it |
| Holistic | **H1** a summary conveying the high-level goals |
| Holistic | **H2** transferring the method to prove something else |
| Holistic | **H3** breaking the proof into its main parts or sub-proofs |
| Holistic | **H4** applying the general method to a specific example |

This matters twice over. It supplies a **completeness criterion** — artefacts
that cover all seven facets, rather than whatever the model found easy — and,
because the model was built for assessment, it supplies **the evaluation design**:
comprehension questions per facet, answerable with the artefacts and not without.

### 5. Lamport, "How to Write a Proof" (1993) and "How to Write a 21st Century Proof" (2011)

<https://lamport.azurewebsites.net/pubs/proof.pdf>

Hierarchically structured proofs: numbered steps, main idea first, detail
revealed only on demand, **4–10 steps per level**, a sketch at every level so a
reader can stop at any depth. Its numbering scheme exists to prevent references
to sub-proofs established under assumptions no longer in scope — which is the
rigorous form of "where is this hypothesis in force?", and therefore of two of
the questions we have to answer.

## Deliberately not adopted

- **Rewriting the text.** math-prose does this well and it is explicitly not the
  brief.
- **Formalisation** (Lean, blueprint dependency graphs). It gives a real
  dependency graph, but demands the proof be formalised first. deslopify must
  work on prose as written, including prose that is wrong.
