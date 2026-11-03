---
name: deslopify-search
description: Find papers on a mathematical question — the latest arXiv preprints in a field, or the literature on a topic — check each at its source, and report them with full references and links.
default_task: Read the request in `{document}` and carry out the search it asks for, as described in your method. Write `progress/reading/guide.md` and `progress/reading/results.json`.
---

# Method: a search of the mathematical literature

You are searching the literature for a working mathematician. The request is
in `{document}`, with the date it was asked on. Requests are of two broad
kinds, and some are both:

- **What is new**: "the latest arXiv preprints on X", "what appeared this
  month in math.GT on Y". Recency is the point: search by submission date, and
  say exactly which window you covered.
- **What exists**: "which papers computed Z", "who has worked on W", "the
  standard references for V". Coverage and standing are the point: find the
  papers that established the topic as well as recent ones, and say which is
  which.

## The one rule that matters most

**Every paper you list must be one you have seen at its source in this
session** — its arXiv abstract page, its DOI landing page, its record in
zbMATH Open, OpenAlex, Semantic Scholar or Crossref, or the publisher's page —
and every detail you give (authors, title, year, identifiers) must be copied
from there. Never list a paper from memory, never complete a half-remembered
reference, never guess an arXiv number or a DOI. A plausible paper that does
not exist is the worst thing this search can produce: the reader will spend
time looking for it, and will stop trusting everything else on the list. If
you believe a paper exists but could not confirm it, leave it out of the list
and mention it, as unconfirmed, under the limits of the search.

## How to search

Use the tools you have: web search to discover, and fetching pages and API
endpoints to confirm. Good sources for mathematics, all free:

- **arXiv API** — `https://export.arxiv.org/api/query?search_query=…&sortBy=submittedDate&sortOrder=descending&max_results=50`.
  Field prefixes `ti:`, `abs:`, `au:`, `cat:` (e.g. `cat:math.GT AND abs:"exotic sphere"`),
  combined with `AND`, `OR`, `ANDNOT`; URL-encode spaces and quotes. Gives
  title, authors, abstract, dates, categories, and the arXiv id.
- **arXiv listings** — `https://arxiv.org/list/<category>/recent` and
  `…/pastweek?show=500`, for "what is new in a field".
- **OpenAlex** — `https://api.openalex.org/works?search=…&filter=publication_year:>2019&per-page=50`,
  for published papers, citation counts, and venues.
- **Semantic Scholar** — `https://api.semanticscholar.org/graph/v1/paper/search?query=…&fields=title,authors,year,venue,externalIds,abstract,citationCount`.
- **zbMATH Open** — `https://api.zbmath.org/v1/document/_search?search_string=…`
  and its web pages, for the mathematical literature before arXiv as well.
- **Crossref** — `https://api.crossref.org/works?query=…&rows=20`, to confirm a
  DOI and a journal reference.

Work in rounds: interpret the request, search broadly with several phrasings
(the field's own terms, older names for the same object, the names of the
central results), read what comes back, follow the most relevant papers'
references and citations where that is how the literature is connected.
Inspect the introductions and relevant cited references of closely related
papers found online, then follow their useful reference leads to see whether
further papers should be added. Work within the supplied time budget and
prefer depth on the request over breadth around it.

Read each candidate's abstract before deciding. A paper belongs on the list
only if it answers the request; say in one sentence how. Mentioning the topic
in passing is not answering it.

## What to write

Two files, about the same papers in the same order.

### `progress/reading/guide.md`

```
# <a short title naming the question>

<what was asked, and how you understood it: the window of dates, the arXiv
categories, what you counted as relevant and what you left out on purpose —
two to five sentences>

## <a group of papers>
- **<Authors>**, *<Title>*, <arXiv:id or journal, volume, pages> (<year>).
  <link>
  <one to three sentences: what the paper does — from its abstract, in your
  own words — and why it answers the question>

## <another group>
…

## How this search was done
<the sources you used and the actual queries, the dates covered, how many
candidates you looked at, and what you did to confirm each one>

## Limits of this search
<what it may have missed and why: sources you could not reach (MathSciNet is
behind a subscription), work not on arXiv, terms you did not think of; any
paper you believe exists but could not confirm>
```

Group by what helps the reader most: by theme or approach for "what exists",
by date (newest first) for "what is new", with the most important papers
first in each group. Say plainly if the search found little; an honest short
list is a result.

### `progress/reading/results.json`

```json
{
  "query": "<the request, as asked>",
  "searched_on": "<the date in the request>",
  "results": [
    {
      "title": "…",
      "authors": ["First Author", "Second Author"],
      "year": 2026,
      "venue": "arXiv preprint (math.GT)",
      "arxiv": "2609.01234",
      "doi": "",
      "url": "https://arxiv.org/abs/2609.01234",
      "pdf": "https://arxiv.org/pdf/2609.01234",
      "summary": "What the paper does, in one to three sentences.",
      "why": "Why it answers the question, in one sentence.",
      "group": "<the heading it is under in guide.md>",
      "found_by": "<the source where you confirmed it, e.g. arXiv API>"
    }
  ]
}
```

`arxiv` is the bare identifier (no `arXiv:` prefix, no version unless it
matters); `doi` the bare DOI. Leave a field empty rather than guess it. Give a
`pdf` link only where the PDF is openly available at that address (an arXiv
paper's always is). The reader can add any paper with a PDF link to unpacman
in one click and read it there.

## Rules

- **Copy, do not recall**: every detail comes from a page you fetched in this
  session (see above).
- **Do not download PDFs** or write anything but the two files above and your
  session notes. Do not change `{document}`.
- **Summaries say what the abstract says.** Do not attribute to a paper a
  result its abstract does not state; if the abstract is vague, say so.
- **Dates are submission dates** for arXiv ("first submitted"), publication
  years for journals; say which.

Before you finish, write `progress/_session_notes.md`: what was hardest to
find, and what a later search should try first.
