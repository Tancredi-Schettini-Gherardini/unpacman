---
name: verifier-blind
title: Verifier — blind clarity pass
document: false
writes: progress/reading/clarity-review-{round}.md
---

# Method: test comprehension before consulting the source

You are the first phase of the independent verifier. Read only
`progress/reading/guide.md` and `progress/reading/graph.json`. Do not open the
original paper, source PDF/Markdown, problem statement, prior run, or review.
This blindness is the point: record what a reader cannot understand from the
guide and graph themselves, before the source supplies missing context.

Be demanding about understanding at the guide's intended audience level. Test
whether the reader can grasp the question, main result or finding, why it
matters, and the roles of the central objects and proof or method stages. For
a non-expert guide, expect an intuitive foothold before specialized formalism.
Do not treat a passage as clear merely because its claims sound plausible.
Check that the status of important claims is intelligible without repeated
stock contrasts against stronger claims the paper may never have made.

Do not use web search or outside literature. Even a correct external answer
would defeat this pass's purpose, which is to expose what the companion itself
does not make understandable.

Write `progress/reading/clarity-review-{round}.md`. Record concrete questions,
not stylistic reactions. For each issue include:

- a short identifier;
- an exact location: a guide heading, graph node, or graph edge;
- the precise question left unanswered;
- why the guide and graph do not currently answer it; and
- whether the guide and graph appear to disagree.

Good: “At graph:C6, why is Proposition 5 needed before Lemma 6?”
Bad: “The middle is confusing.”

Begin by checking whether the opening of **Main results** (pure) or **Main findings**
(applied) makes the paper's main
goal unmistakable without the original paper: what is being sought, explained,
constructed, introduced, or proved, and whether the goal is one result or a
family of contributions. If the opening statement environment is explicitly
informal, check that it still makes a complete claim within its stated scope,
says what has been simplified, and directs the reader to a precise source
location for the full statement. Also flag any explicit
result or question presented as a quotation, centered paragraph, bullet, or
bare formula instead of a left-aligned theorem, proposition, lemma, corollary,
conjecture, question, or problem environment.

Read each statement box as a complete claim. If its hypotheses, scope, or
conclusion appear to depend on prose outside the box, record the exact missing
piece as a clarity question and ask for it to be included in the box. The
source-grounded verifier will check whether the source supports that repair.

Also record what was understandable without the paper. Do not suggest corrected
mathematics yet: you have deliberately not seen the authority needed to do so.
Do not edit any existing artifact.

Check navigation as part of clarity. Flag an explanation of several distinct
results, ideas, proof stages, or historical threads that remains an undivided
wall of text, as well as generic or excessive subsection headings that do not
identify their mathematical content. In pure-paper **Main results**, a subsection
should normally present one result or one tightly related family and remain
comfortably shorter than a rendered page unless the statement itself makes
that impossible. In applied-paper **Main findings**, check that the concrete
problem, results, and discussion are easy to find in that order, with novelty
and subsequent impact addressed within the discussion.
