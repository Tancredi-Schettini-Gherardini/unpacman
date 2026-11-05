---
name: file-contract
---

# The working contract

You are one session in a long campaign against a hard problem. Sessions before
you left the state you inherited; sessions after you will inherit what you leave.
Everything below exists so that chain does not degrade.

## Where things go

| Path | What it is |
|---|---|
| `progress/math.tex` | The mathematics. The durable artefact. Compiles to `math.pdf`. |
| `progress/state.json` | Machine-readable status. What is proved, what is not, what is next. |
| `progress/labbook.md` | The session log. **Do not edit it** — it is written for you. |
| `progress/_session_notes.md` | Your account of *this* session. You write it; it is folded into the lab book and then removed. |
| `work/` | Scratch. Code, data, experiments, half-formed notes. Nothing here is load-bearing. |
| `problem.md`, `strategy/`, `refs/` | The human's specification. **Read-only.** |

## How to use `math.tex`

It has five sections and each one means something specific.

- **Established results** — only what you are prepared to defend line by line. If a
  step rests on "clearly" or "it follows that" and you cannot expand it on demand,
  it does not belong here yet.
- **Conjectures and partial results** — everything you believe but have not proved.
  Mark each with `\conjectural`. Say what evidence you have: numerical to what
  range, proved in what special case.
- **Open questions and next steps** — concrete enough that the next session can act
  on the first item without re-deriving your reasoning.
- **Failed approaches** — what you tried and precisely why it broke. This is not an
  admission of defeat; it is the most reusable thing you will produce. A dead end
  recorded once is a dead end nobody walks again.

Never delete or weaken a previously established result to make a new claim fit. If
you find an error in earlier work, that discovery is the most valuable output of
your session: fix it, and lead your session notes with it.

## Honesty

State exactly what you have. "I reduced the problem to X but could not prove X" is
a good session. "I proved the theorem" when you have a gap at step 4 is a bad one,
and it is worse than no session at all, because it poisons everything downstream.

When you are unsure whether an argument is rigorous, say so in place, in the text,
where a reader will meet it — not in a footnote and not only in your notes.

## Numerical work

If the problem admits computation, compute. Write the script into `work/`, keep it
runnable, and record what it checked and to what range. A conjecture backed by a
reproducible script is worth far more than one backed by a claim that you checked
it. Never report a numerical result you did not actually run.
