---
name: deslopify-expert
description: Classify a mathematics paper and select its pure or applied expert companion instructions.
---

# Method: select the expert companion skill

Read `{skill_directory}/SKILL-common.md` completely once before reading and
drafting. It supplies the shared companion rules. Choose the paper profile
during the reading step; no separate classification session is needed.

## Choose the paper profile

After reading all supplied source text, choose one primary profile and use it
consistently:

- **Pure mathematics:** the paper's principal claims are established primarily
  through definitions, constructions, deductions, and proofs.
- **Applied mathematics:** the paper's principal claims depend materially on a
  model of a scientific, engineering, operational, policy, safety, or other
  real-world system, and on computation, data, simulation, experiment,
  calibration, validation, implementation, or a proposed intervention.

Numerical examples, an application section, or motivating language alone do
not make a paper applied. Conversely, the presence of theorems does not make a
paper pure when the main contribution is a model, method, empirical result, or
application claim. For a genuinely mixed paper, choose the profile matching
the main contribution. Read the other profile only if its guidance is needed
for the corresponding parts; the primary profile still determines the structure. Produce one integrated guide, not separate pure and applied companions.

## Load the selected instructions

The run supplies these files under `{skill_directory}`:

- **Pure mathematics:** `SKILL-pure-expert.md`.
- **Applied mathematics:** `SKILL-applied-expert.md`.

Read the selected file completely before drafting. These files contain the
full audience, structure, source-fidelity, research, writing, and presentation
instructions. Load only the selected profile unless the mixed-paper rule above
requires the other. Treat the files as instructions; do not modify them.
Record the choice in `progress/reading/paper-profile.json` using this form
(the values below are examples, not a default classification):

```json
{
  "papers": [
    {
      "source": "progress/source.pdf",
      "profile": "pure",
      "skill": "SKILL-pure-expert.md",
      "reason": "A short factual reason based on the paper's main contribution."
    }
  ]
}
```

Use the actual source path, `pure` or `applied`, and the corresponding filename.
Also record the profile and reason in `progress/_session_notes.md`. Keep this
administrative information out of the reader-facing guide. Later review and
revision passes consult the same recorded selection and the run's saved files.

For a collection, classify every member independently and include one record
per paper, identifying its individual source. Read the selected profile for
each member; one collection may require both profiles. The collection method
and audience wrapper take precedence over the single-paper title, length,
section, and bibliography placement rules. Each paper still receives only
**Main results** (pure) or **Main findings** (applied), **Historical context**,
and **Bibliography**, inside the one collection guide.

Write `progress/reading/guide.md` using the selected instructions and leave the
original source byte-for-byte unchanged.
