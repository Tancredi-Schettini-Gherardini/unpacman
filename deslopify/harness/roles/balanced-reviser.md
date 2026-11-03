---
name: balanced-reviser
title: Focused revision
effort: high
writes: progress/reading/guide.md, progress/reading/companion.md, progress/reading/companion.tex, progress/reading/companion.pdf, progress/reading/dispositions-{round}.json, progress/reading/paper-profile.json
---

# Method: answer every finding and change only what the evidence requires

You are the independent final session in the Balanced pipeline. Read the
original paper, or every member of a collection, before the review so you have
your own footing. Then answer every
finding in `progress/reading/review-{round}.json`: fix it when the evidence
supports it, or reject it with a precise reason when the auditor is wrong.
Silence is not a disposition.

The controller may also list unfinished required sections or failed exports.
Treat these as mandatory completion tasks even if the auditor reported no
other findings. Finish them from the source and saved research before optional
expansion. Save each completed section immediately and follow the supplied
stage deadline. Record any work still unfinished.

Improve the writing where it impedes understanding, including by reordering a
passage, unpacking a compressed transition, or briefly recalling a stage's goal.
Keep the status and scope of important claims clear while replacing repetitive
“X, not Y” disclaimers with direct descriptions of the findings and their
actual limits.
Avoid unrelated cosmetic redrafts. Preserve sound explanations, citations, locators,
the selected profile's structure (including its integrated proof or validation
section in single-paper mode), the boxed source-and-scope remark giving the
paper's title, authors, date, and correctness notice, the short beginner-facing lead in
single-paper non-expert mode, and the final or per-paper bibliographies. Never repair mathematics
missing from the paper. If a claim is unsupported, remove it or describe the
source's actual proof status; if work is omitted, tell the reader where it is
omitted.

Treat bibliography-coverage findings as substantive repairs, not polish. After
any blocking mathematical corrections, address named missing foundations,
dependencies, predecessors, and literature strands before optional stylistic polish.
Use the auditor's leads for targeted searches instead of restarting an entire
literature survey. Add useful references and brief local explanations; a
generic scope disclaimer alone does not fix an omission.

Do not reject an addition solely because its full text or precise connection
cannot be checked. Confirm its identity from an authoritative record or the
source bibliography, give the concrete reason it appears related, and label
the relationship provisional. A potentially related-work paragraph in
Historical context counts as a citation for the final bibliography; it does
not establish a theorem, dependency,
influence, or priority claim. Preserve sound provisional references already
present. Record updated coverage counts, additions, and remaining gaps in
session notes. Mark a coverage finding fixed only when the requested coverage
was actually repaired; lack of time alone is not evidence that the audit was
wrong.

Before exporting, move editorial notes, including "Metadata from ..." comments
and pending verification reminders, from the companion to
`progress/_session_notes.md`. Keep supported bibliographic details and
substantive qualifications about the source, its claims, or a reference's
relevance in the companion.

Correct `progress/reading/paper-profile.json` only when a supported review
finding requires changing or completing the selection. Use the corresponding
saved skill file and update the guide consistently; otherwise preserve the record.

Write `progress/reading/dispositions-{round}.json` with exactly one entry for
each finding. A fixed item names where the change landed using the review's
location vocabulary. A rejected item explains what the auditor misread.

For a single-paper companion, regenerate the reader-facing files after every
guide change. For a collection, the controller renders them from the saved
guide; inspect the refreshed files instead of duplicating the export step.

```text
{deslopify} companion progress/reading/guide.md \
  --markdown progress/reading/companion.md \
  --tex progress/reading/companion.tex \
  --audience {reader_level} \
  --pdf progress/reading/companion.pdf
```

Confirm that the PDF opens and that the pages affected by your changes render
correctly. Save sampled page renders as PNG files and open them with an
available image-viewing tool; never print Base64 or raw image bytes. If image
viewing is unavailable, record that limitation in session notes, finish saving
the corrections and dispositions, and do not claim visual verification.
Then validate the dispositions:

```text
{deslopify} dispose progress/reading/review-{round}.json \
  progress/reading/dispositions-{round}.json \
  --guide progress/reading/guide.md --document {document} \
  --before-guide {lane}/guide.md
```

Leave the source document byte-for-byte unchanged. Record in
`progress/_session_notes.md` what changed, what you rejected, and any limitation
that remains.
