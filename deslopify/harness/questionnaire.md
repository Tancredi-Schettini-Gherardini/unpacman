You are preparing a Non-expert reading of the mathematical paper below. Before
the reading begins, its reader — a mathematician from outside the paper's
field — answers a short set of questions about what they already know of what
the paper assumes. Their answers decide how much the companion explains, and
where. Write those questions.

What to ask about: the background the paper relies on without explaining it —
the definitions it takes as known, the theorems it quotes or uses, the
standard arguments and techniques it applies, the objects and notation of the
field. Ask about what this paper actually needs, in the order a reader meets
it, and what matters most first. Do not ask about the paper's own new
definitions or results: the companion explains those to everyone.

How to ask: one item per question, named precisely, in the reader's terms:

- "Do you know the definition of a Riemannian metric?"
- "Are you familiar with the h-cobordism theorem?"
- "Do you know the argument by which Morse theory computes homology from
  critical points?"

Follow-up questions go one step down, to what the parent item itself rests on,
and are asked only when the reader does not know the parent: if they do not
know what a Riemannian metric is, ask whether they know what a smooth manifold
is. Give a follow-up only where knowing its answer would change how the
companion explains the parent. At most three levels deep.

Between 10 and 40 questions in all, follow-ups included; around 15 to 30 for
a typical paper. Fewer for a short or elementary paper, more for a long one
that draws on several fields.

Answer with a JSON object and nothing else — no text before or after it:

{"questions": [
  {"id": "q1",
   "kind": "definition",
   "topic": "Riemannian metric",
   "question": "Do you know the definition of a Riemannian metric?",
   "where": "§2, p. 3",
   "parent": null,
   "ask_if": null},
  {"id": "q2",
   "kind": "definition",
   "topic": "smooth manifold",
   "question": "Do you know the definition of a smooth manifold?",
   "where": "§2, p. 3",
   "parent": "q1",
   "ask_if": ["no", "roughly"]}
]}

- `id`: short and unique, `q1`, `q2`, … ; a follow-up comes after its parent.
- `kind`: one of definition, theorem, argument, technique, object, notation.
- `topic`: the item itself, as a few words a mathematician would use.
- `question`: the question as the reader will see it, at most two lines.
- `where`: where the paper first relies on it (section and page), or "" if
  it is used throughout.
- `parent`: the `id` of the question it follows up, or null.
- `ask_if`: for a follow-up, which answers to its parent make it worth asking
  — from "yes", "roughly", "no"; usually ["no", "roughly"] or ["no"]. null for
  a question that is always asked.
