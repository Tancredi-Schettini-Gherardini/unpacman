---
name: latex-writeup
description: How to structure and maintain progress/math.tex across many sessions. Read this before restructuring the document or when a compile fails.
---

# Maintaining `math.tex`

The document is written once and edited by dozens of sessions. Optimise for the
next editor, who will not have your context.

## Structure

Keep the five top-level sections. Do not rename or reorder them; downstream tooling
and every other session expects them.

Inside **Established results**, one `\begin{lemma}` … `\end{lemma}` per claim, each
immediately followed by its proof. Label everything: `\label{lem:mod3}`. Refer with
`\cref{lem:mod3}`, never by number — numbers move.

## Editing rules

- **Append, don't rewrite.** Prefer adding a new lemma to restructuring three
  existing ones. Large refactors lose information and are hard to review in a diff.
- **Never silently weaken a claim.** If a previously established result turns out to
  need an extra hypothesis, say so in a `\begin{remark}` next to it, with the
  session number. Silent weakening destroys the audit trail.
- **One idea per paragraph.** Long proofs get `\begin{enumerate}` steps so a referee
  can point at "step 3".

## Notation

Fix notation once, in the **Problem** section, and use it everywhere. If you must
change notation, change it document-wide in the same session — half-converted
notation is worse than either convention.

Define every symbol at first use. `\newcommand` for anything used more than twice;
put those in `preamble.tex`, not scattered through the body.

## Keeping it compiling

`math.tex` is compiled after every edit and the errors come straight back to you.
Common causes, in order of frequency:

- unbalanced `{`, or `\left` without `\right`;
- an environment opened and not closed;
- a `%` you meant literally — write `\%`;
- `_` or `^` in text mode — write `\_`, or wrap in `$…$`;
- a `\newcommand` that collides with an existing macro — check `preamble.tex`.

If a compile failure is not obvious, isolate it: comment out the section you just
added, confirm the document builds, then re-add in halves.

## Status markers

- `\conjectural` after any statement not yet proved.
- `\status{...}` for a short prose note on how solid something is.

A document where every claim carries an honest status marker is worth far more than
one that reads as uniformly confident.
