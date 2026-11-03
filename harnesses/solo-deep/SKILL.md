---
name: solo-deep
default_task: Continue the work. Open with a written plan, then execute it, auditing yourself on the prescribed cadence.
---

# Method: plan → attack → audit → record, on a cadence

You are working a long session alone. The failure mode of long solo sessions is
drift: forty minutes of plausible activity that establishes nothing, because
nobody stopped to ask whether the current line is still worth pursuing. The
cadence below exists to catch that.

## Open with a plan

Before touching `math.tex`, write `work/plan.md`:

- the specific target of this session, stated as a mathematical claim;
- the route you intend to take, in three to six steps;
- for each step, what would count as success and what would falsify it;
- a named fallback if the main route dies.

Keep it short. Its job is to make drift visible later, not to be complete.

## Attack

Work the plan. Prefer depth on one line over breadth across four — a half-proved
lemma is worth more than four sketched approaches.

Compute when you can. If a claim is checkable numerically, write the script in
`work/`, run it, and record the range you checked. A conjecture with a
reproducible script behind it is a different object from a conjecture without one.

## Audit — every ~10 tool calls, and after every significant claim

Stop and answer these in writing, in `work/plan.md`, appended as a dated block:

1. What have I actually established since the last audit? State it as a claim.
2. Would a hostile referee accept it? Name the weakest step.
3. Am I still on the plan? If not, is the deviation better, or am I drifting?
4. What is the single most likely way this whole line is wrong?

If question 4 has a plausible answer, go test it before continuing. Finding out
that a line is dead after ten minutes is a good outcome; finding out after two
hours is the thing this cadence is for.

## Record as you go, not at the end

Promote results into `math.tex` as they firm up. A session that does all its
writing in the last five minutes loses most of what it learned, and if it runs out
of budget it loses everything.
