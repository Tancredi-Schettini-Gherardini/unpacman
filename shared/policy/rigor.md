---
name: rigor
---

# What counts as established

The single most common failure in machine-assisted mathematics is a confident,
well-written, wrong lemma. It costs more than no result at all, because it gets
built upon. Everything below is aimed at that failure.

## Before you promote a claim to "established"

Check each of these explicitly, not as a formality:

1. **Quantifier order.** "For all ε there is N" and "there is N for all ε" are
   different theorems. Which one did you prove?
2. **Edge cases.** n = 0, n = 1, the empty set, the trivial group, the degenerate
   configuration, equality in a strict inequality. Does the argument survive?
3. **Every "clearly".** Each one is a claim. Can you expand it in one line? If it
   takes a paragraph, it is a lemma and deserves to be stated as one.
4. **Cited results.** If you invoke a theorem, are *all* of its hypotheses verified
   in your setting? Name them and check them one at a time.
5. **What you actually proved.** Compare the statement you set out to prove with
   the statement your argument establishes. They drift apart more often than you
   would expect — usually by a hypothesis you quietly assumed halfway through.

## Try to break it first

Before recording a result, spend real effort trying to refute it. Look for a
counterexample. Try a slightly stronger version and see where it fails — if the
stronger version does not fail, you may have proved less than you could, and if
it fails for a reason that also threatens the weaker one, you have found a bug.

A result you have genuinely attacked and failed to break is worth several results
you merely wrote down.

## When you use a computation

State what was checked, over what range, with what precision. Floating point does
not prove identities: a residual of `1e-15` is strong evidence and not a proof, and
saying so costs one clause. If a numerical screen is *supposed* to fail — because
some theorem forbids the thing you would otherwise be constructing — run it, and
treat a pass as a bug in your construction, not a discovery.

## Language

Prefer "we have not been able to show" over "it is easy to see". Prefer a precise
open question over a vague claim of near-completion. The reader of your write-up
is a mathematician who will trust what you assert; do not spend that trust.
