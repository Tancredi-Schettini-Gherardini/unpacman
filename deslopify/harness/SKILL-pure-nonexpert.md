---
name: deslopify-pure-nonexpert
description: Write and independently ground a non-expert companion to a pure mathematics paper for a mathematically mature reader outside the paper's specialty.
---

# Method: write the pure-mathematics non-expert guide before anyone draws the graph

You are the first independent reading session. Your main reader-facing
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

This is the pure-mathematics nonexpert method. The entry skill selects this file
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

### Pure-mathematics profile

Before drafting, identify the following and the minimal background needed to understand each part:

1. the principal theorem or claim, including the hypotheses and quantifiers that
   materially control its scope;
2. the obstruction that makes it nontrivial;
3. the main ideas, structural backbone, and closing mechanism;
4. roughly four to eight load-bearing proof stages and where distinct branches
   split or merge;
5. the decisive estimates, constructions, identities, or external results;
6. exceptional or boundary cases that matter to the argument; and
7. the last implication that closes the proof, together with substantial gaps,
   unclear transitions, or important work delegated elsewhere.

Include a lemma in the guide only when it introduces a central object, removes
an obstacle, provides a load-bearing bridge, globalizes the construction, or
closes the proof. Explain what it does and what later step needs it. Preserve
source notation when useful, but define paper-specific notation before relying
on it.

Use source references selectively so traceability supports rather than
interrupts exposition. Give compact explicit references for principal results,
decisive formulas, unusual definitions, important external inputs, and passages
whose correctness or interpretation is discussed. A reference such as
`[Abc00, Theorem 1]` (or `\cite[Theorem 1]{Abc00}` in LaTeX) is enough. Add
section, equation, figure, or page locators only when they materially help the
reader; routine explanatory steps need no locator.

## Write `progress/reading/guide.md`

### Opening lead for a beginner

After the title, before the source-and-scope remark and **Main results**, write one to three
short orientation paragraphs. Introduce the broad area, the objects, and the
question in familiar mathematical language. A classical or simple case can
help show why the problem is interesting. Define only the few terms needed for
this foothold; leave the paper's formal result to **Main results**.

Mark the limit of a simplified example when it matters. The lead should make
the subject approachable without suggesting broader claims than the paper
supports.

### Companion structure

Use these top-level sections in this order: **Main results**, **Main ideas**,
**About the proofs**, **Q&A**, optional **Worked examples**, **Historical
context**, optional **Where the document is thin**. All except **Worked examples**
and **Where the document is thin** are required. A short unheaded lead may
precede **Main results**, but no substantive **Introduction** or **Overview**
may do so. You may add at most one genuinely paper-specific top-level reader aid
under the rule below.

**About the proofs** should explain the proof architecture and only the results,
assumptions, formulas, and external inputs needed to understand the major steps.
It is not an audit trail of every dependency.

### Main results

Make **Main results** the first substantive section after the short lead.
Translate the headline and each principal statement into plain language before
or alongside its precise formulation; supply a concrete example where it helps.
In roughly one to three rendered pages for an ordinary paper, tell a
mathematically mature newcomer what the paper proves or claims, why it matters,
which objects and assumptions its statements concern, which conclusions are primary versus
secondary, what those conclusions cover, and any material open issue. This
section is not a compressed proof outline or catalogue of theorem numbers.

Treat the source as authoritative for its statements, hypotheses, terminology,
qualifications, open questions, and claims about prior literature. Do not
silently strengthen, weaken, repair, or reinterpret a result. Use accurate verbs
such as “proves,” “claims,” “computes,” or “conjectures,” and do not describe a
result as verified, accepted, or resolved without evidence.

When useful and accessible, consult zbMATH Open or MathSciNet reviews of close
predecessors or competing work as private expository calibration: use them to
see what a mathematical review foregrounds, not as authority for the source
paper's theorem or for priority or correctness claims.

Unless the mathematics clearly suggests a better order, use the following
subsections and hierarchy.

#### Headline claim

After a brief concrete orientation, state in one or two direct sentences the
result that makes the paper matter, before construction-specific notation or a
long theorem statement. State its status and give a compact source locator.

#### The underlying mathematical question

State the central question independently of the paper's strategy whenever it
can be described without distortion: what objects are given, what is being
estimated, constructed, classified, or proved, and what would count as an
answer. Then state the paper's main goal separately. Do not formulate the
question in a way that presupposes the paper's own reduction or representation.

If the goal has a reasonably self-contained mathematical formulation, give a
short semantic version here and defer the full formal statement until the
necessary objects have been introduced. If that semantic version suppresses material
hypotheses, name them and cite the precise statement.

#### Objects needed to understand the statements

Introduce the principal objects, assumptions, conventions, and notation needed
for the formal statements. Explain what the objects mean and why the result
matters; leave proof constructions, mechanisms, and reductions to Main ideas
and About the proofs unless they are needed to interpret the statement itself.

#### Precise statements and secondary results

State every principal result, organized by theme or dependency, and distinguish
main theorems from important corollaries, classifications, constructions, or
negative results. Give the hypotheses, quantifiers, parameter ranges, and
conclusions needed to establish scope. Do not inflate an auxiliary lemma into a
main result or suppress a major result merely because it is technically hard to
state.

Give each main theorem in two layers: a concise semantic formulation and then
the precise formal statement, or a complete statement of clearly identified
selected assertions that establish its scope.
Use the shared boxed statement style. If a formula is part of the theorem,
include or reference it according to the statement-environment rules.

After a principal statement, explain its meaning, scope, and important
consequences. Present secondary invariants or conclusions after the headline
is clear, using a compact comparison when helpful. Leave explanations of why
the proof works to Main ideas and About the proofs.

#### Relation to prior work and what remains open

Include a short comparison here only when it is central to understanding the
result landscape—for example a sharpened bound, correction, resolved
conjecture, or counterexample. Put detailed genealogy in **Historical context**.
If the source criticizes earlier work, attribute the criticism unless an
independent check establishes more.

Discuss material limitations or open issues near the end when they clarify the
result's scope: for example unresolved uniqueness, parameter dependence,
classification, or a conflict with earlier work. Use a **Remark** box or compact
list when several distinct issues need to be visible. Do not add a stock caveat
or invent a limitation to fill a box.

### Main ideas

Write **3 to 5 ideas** for a mathematically mature reader new to the field, each under a clear, descriptive subsection heading. This
section comes before **About the proofs**, so use it to show what makes the paper
mathematically worth reading and how the ideas fit together.

“Main ideas” means conceptual reductions, unexpected viewpoints, useful
constructions, reusable technical devices, or mechanisms that overcome a real
obstruction—not restatements of the main theorems. Prefer ideas with broader
conceptual value over routine lemmas or long computations.

For each idea, give enough context to make it intelligible, then explain:

- the idea itself and the obstacle or role it addresses;
- why it is valuable or illuminating;
- one key object, formula, or precise statement when useful; and
- how it connects to the overall strategy or to the other selected ideas.

Be fair about novelty: distinguish an idea introduced by the paper from a
substantial adaptation or an effective use of a standard tool. Keep detailed
genealogy in **Historical context** unless it is required to understand the idea
itself. Each idea may take up to three paragraphs when needed for clarity; do
not squeeze its context, mechanism, and significance into one paragraph or
expand it merely to fill the subsection.

### About the proofs

Write a section titled **About the proofs**.

The purpose of this section is to help a mathematically mature newcomer who
knows the main statements but is encountering the proof ideas for the first time.
Explain what each new object is for before technical notation.
Do **not** write a lemma-by-lemma summary, and do **not** follow the paper
section-by-section unless that is genuinely the clearest organization.

Start with a **high-level proof roadmap** developed over one to three paragraphs.
Give it more than one paragraph when the proof has distinct mechanisms or
branches. Explain the overall strategy before introducing technical notation:
what the proof is trying to connect or reduce, what the main intermediate objects
are for, what the decisive mechanism is, and where the argument splits into
distinct branches. The roadmap should be clear enough that the reader can
retain the architecture even if they skip the technical details that follow. A
compact chain may accompany the prose when it helps, for example:

$$
\text{original problem}\to\text{auxiliary object}
\to\text{key estimate or structural step}
\to\text{final deduction}.
$$

Then explain the proof in **3–6 large conceptual stages**, each with a clear,
descriptive subsection heading. Each stage should:

1. state its goal and the obstacle it overcomes before introducing notation;
2. explain the central mechanism and introduce only the objects needed to see
   why that mechanism works;
3. state what the stage produces and how that output is used in the next stage;
4. identify the genuinely delicate point when the obvious heuristic is
   insufficient.

Keep the technical layer lean. Omit routine calculations, long chains of local
lemmas, parameter bookkeeping, and exact implementation details unless they
contain an idea needed to understand the proof. Use formulas only when they
make the mechanism clearer; do not reproduce formulas merely for auditability.
A compact citation such as `[Abc00, Proposition 4.5]` is enough to locate a
technical result in the source.

When the proof has logically different branches—such as the main comparison or
existence argument, equality or rigidity, sharpness, classification, or an
asymptotic construction—separate them clearly rather than forcing them into one
linear narrative. The reader should finish **About the proofs** knowing the
proof's global architecture, the few load-bearing mechanisms, why each is
needed, and where the genuinely difficult conceptual steps lie.

### Q&A

Typically include about five substantive questions, each under a descriptive
subsection heading. They should be genuine questions a serious first-time reader
would ask, not rhetorical signposts.

Prioritize conceptual stakes: why a construction is needed, what the most
load-bearing idea is, what would fail without a key hypothesis, how an
exceptional case is handled, what a result does or does not imply, or where the
paper leaves an important issue open. The first two questions should usually be
the challenging conceptual questions one might ask after a one-hour research
talk rather than local technical details.

Give enough context for each answer to stand on its own, define specialized
terms before relying on them, and answer at the length needed for clarity. A
cross-reference may help but should not substitute for a local explanation.
Each Q&A subsection may take up to three paragraphs when needed to explain the
context, reasoning, and qualifications. Distinguish the paper's answer from a
companion inference, and say plainly when the paper does not settle the
question; do not manufacture a resolution.

### Worked examples

This section is optional and should usually be omitted. Include it only when a
toy model, concrete case, boundary case, or source example genuinely clarifies
a main result, proof mechanism, or important hypothesis. Prefer an example from
the source when it is particularly illuminating and cite it precisely. Label a
companion-created example as such, and never imply that it appears in the paper.

#### Optional paper-specific section

Add at most one extra top-level reader aid when the paper genuinely needs one
and the material does not fit naturally elsewhere—for example **Dictionary
between the two formalisms**, **The parameter regimes at a glance**, or
**Anatomy of the model construction**. Place it where the reader needs it and
use whatever form is clearest: prose, table, notation guide, comparison, or
schematic. Give it a content-specific title; do not use it to repeat another
section, add generic background, or evade the required structure.

### Historical context

Follow the historical-research and citation instructions in `SKILL-common.md`,
with the audience and level of explanation specified above.

### Where the document is thin

This section is optional. Include it only if your reading identifies serious
mathematical gaps in the paper. Explain each gap and where it occurs. Otherwise
omit the section; delicate steps, unanswered questions, and limits on your own
verification do not by themselves warrant it.

Finish with the bibliography required by the shared companion instructions.

## Before stopping

Write `progress/_session_notes.md`. Record what was hardest to explain, where a
later auditor should be skeptical, which supplementary artifacts or computations
were inspected or rerun, the literature cutoff and search limitations, source
location or extraction problems, and which broadly useful illustrations were
included or omitted and why. Record the selected pure profile and its basis. Do
not include hidden reasoning, token counts, costs, or timings.

Leave the source byte-for-byte unchanged. Generate a new proof map only when the run
supplies its optional diagram instructions. Do not create summary files or
other PDFs in this pass. The later audit copies the guide to
`companion.md`, generates the persistent `companion.tex`, and compiles the PDF.
