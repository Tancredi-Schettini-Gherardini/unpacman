---
name: explore-verify
default_task: Continue the work. Explore freely, then put every claim you want to keep through the referee before promoting it.
---

# Method: explore freely, then verify adversarially

You alternate between two modes. Which mode you are in determines what you are
allowed to write down.

## Explore mode

Generate candidate lemmas, constructions, reformulations, and counterexamples
without restraint. Follow hunches. Try the thing that probably will not work.

Rules while exploring:

- Work in `work/`, never in `progress/math.tex`.
- Mark every speculative claim `[CONJECTURE]` as you write it.
- Compute early. A five-minute numerical screen kills more bad conjectures than an
  hour of thought, and it kills them with certainty.

## Verify mode

When a claim looks solid enough that you want it in `math.tex`, you must first
send it to the `referee` subagent using the Task tool.

Give the referee, in the prompt:

- the exact statement, with every hypothesis and quantifier explicit;
- your full proposed proof, with no steps left as exercises;
- the definitions and any prior results it depends on — it cannot see your
  conversation or the rest of the document.

The referee will try to refute you. Its default verdict is REJECT.

## Promotion rules

- A claim enters `math.tex` under **Established results** only after a referee
  verdict of `accept` or `accept-with-corrections` where you have applied every
  correction.
- A rejected claim goes under **Conjectures and partial results**, together with a
  one-line summary of the objection that killed it. Recording *why* it failed is
  the point; a rejection you do not record will be re-derived next session.
- If the referee raises an objection you believe is mistaken, do not simply
  override it. Write down the objection and your rebuttal in the text. If you are
  right, the rebuttal belongs in the document anyway.

## When to use the `computer` subagent

Delegate numerical screens, symbolic manipulation, and search to `computer` when
the task is self-contained and mechanical — it keeps that work out of your own
context. Give it precise inputs and tell it exactly what to report back.

## What "done" looks like

A session under this harness that produces *one* referee-passed lemma has done
better than a session that produces five unverified ones. Optimise for the number
of claims you would still stand behind next month.
