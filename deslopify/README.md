# deslopify

A harness that reads a finished piece of mathematics and builds a companion:
an explanation, questions and answers on the confusing points, useful examples
when warranted, and—when requested—a map of the argument. **It never edits the
document.**

`DESIGN.md` is why it is built this way and what the research says.
`references.md` is what it is built from. This file is how to use it and how it
works.

---

## Using it

### Getting it on a Mac

unpacman installs with one line, typed once into **Terminal** (press ⌘ Space,
type `Terminal`, press Return). It is downloaded as a **package**: the zip
`unpacman-mac.zip`, about 5 MB, from unpacman's website or the releases of its
public repository (github.com/Tancredi-Schettini-Gherardini/unpacman), which
unpacks into a folder of the same name with a *READ ME FIRST* inside. With the
folder in Downloads, the line is:

```bash
bash ~/Downloads/unpacman-mac/install-unpacman.sh
```

(or type `bash `, drag `install-unpacman.sh` from the folder onto the Terminal
window, and press Return — that works wherever the folder is). No GitHub
account is needed: unpacman is inside the package.

It works in your own home folder and asks for no password. macOS may ask
whether Terminal may use files in your Downloads folder (*Allow*). If your Mac
lacks Apple's command-line tools it asks macOS to install them — click
*Install* in the window that appears; that takes 5–15 minutes. It then puts
unpacman in a hidden folder, `~/.unpacman`, and an **Unpacman** icon in your
Applications folder. Finally, it opens unpacman.

The same installer also installs straight from the public repository, with no
sign-in: `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Tancredi-Schettini-Gherardini/unpacman/mac/install-unpacman.sh)"`.
However it was installed, the copy then follows the public repository's `mac`
branch, where it looks for new versions by itself; a copy installed before
unpacman was public — from an older package, or from the private repository —
is moved over to it the next time an installer runs, keeping everything,
unless it has changes of its own.

### On Windows

Download `unpacman-windows.zip`, choose **Extract All**, and double-click
**Install unpacman.cmd** from your usual Windows account. Setup installs and
checks **WSL 2** and Ubuntu, requests Windows administrator approval separately
when needed, then installs unpacman and opens it in your Windows browser.
If setup requests a restart, save your work and type **RESTART** in its window;
this also applies pending Windows updates. After restarting, open the same
installer again. Ubuntu asks for a Linux username and password; type `exit`
when its setup reaches a prompt ending in `$` so installation can continue.

The ZIP's **READ ME FIRST.txt** has the full instructions. An existing Ubuntu
installation is reused, and a Start-menu shortcut opens unpacman afterwards.
For an already prepared Ubuntu, `bash install.sh` in the extracted folder
remains available. The Linux installer installs the programs a reading's
sandbox needs (including bubblewrap and socat) and asks for the Ubuntu password.

**Publishing a version** (for its makers): the public repository's branches
are made from the private ones by `uv run python -m deslopify.app.publish
--message "What is new, in a line" --packages FOLDER` — one new commit on each
of `mac` (from `unpackman`), `windows` (from `unpacman_windows`), `main` (the
front page, `deslopify/public/README.md`, and the licence) and `website`
(`deslopify/public/website/`), without the developers' notes, under the
publisher's name and GitHub no-reply address — into a bare clone of it,
`~/Code/unpacman.git`, and the two zips `unpacman-mac.zip` and
`unpacman-windows.zip` from those branches. Nothing is on GitHub until it is
pushed from there, and the zips attached to a release.

The first page, **Getting ready**, lists what a reading still needs on this
computer, with one button for each:

- **An AI to read with** — Claude (Claude Code, needing a paid Claude plan:
  Pro, Max, Team or Enterprise), ChatGPT (Codex, needing a paid ChatGPT
  plan), DeepSeek or OpenRouter (each paid per use from an account of its own,
  with credit added in advance: paste its key; each reads through Claude Code,
  which must be installed, but needs no Claude plan). *Install* fetches the
  maker's own program into your home folder; *Sign in* opens their sign-in
  page in your browser, and the page notices when you are done. If the
  sign-in page shows a code instead, paste it into the box that appears. Any
  one of them is enough. A Codex too old for the protections a reading runs under (see
  *Worth knowing*), or installed without one of its parts, is said so, with
  *Update Codex*, which runs Codex's own installer. Each card says which
  account it is signed in with; *Change account* signs in with another — also
  from Advanced settings › This computer › Accounts, which says who unpacman
  reads with — for unpacman only: Claude Code and Codex elsewhere on your Mac — in
  Terminal, say — keep the account they have, and nothing changes until the
  new sign-in has finished. Not while a reading is using that AI.
  *Use this Mac's sign-in again* goes back (and signs unpacman's own account
  out). For DeepSeek and OpenRouter, *Use another key*.
- **LaTeX**, which typesets the companion. If your Mac already has MacTeX, it
  is used. Otherwise *Install LaTeX* puts a small one (TinyTeX, about 330 MB)
  in your Library folder; a few minutes.
- **Reading PDFs** needs nothing: it is built in.

Nothing on that page costs money. You can look around and add papers before it
is all in place; a reading starts once it is.

**Every day after that**: open **Unpacman** from Launchpad, from Spotlight (⌘
Space, type `unpacman`), or from the Dock if you drag it there. It runs in the
background — closing the browser tab, or every window, does not stop a
reading — and keeps the Mac from falling asleep while a reading runs (closing
the lid still would). **Advanced settings › This computer** has the rest:

- **Updates.** When a new version is ready, the first page says so; *Update
  now* installs it and reloads the page, keeping your papers, readings and
  settings. If the new version would not start, unpacman goes back to the one
  you had and says why. It also brings Claude Code up to date. Every copy looks
  for new versions in the public repository, however it was installed (a copy
  installed from a package before unpacman was public says to run a newer
  package's installer once).
- **Quit unpacman** stops it (it asks first if a reading is running).
- **Copy a report** puts a short report on the clipboard to email to whoever
  helps you: versions, what is installed, how the last readings ended, the end
  of its log. Never a paper, its title or its text.

**Feedback**, at the top of every page (and *Tell us about it* beside anything
that failed), writes an email to the people who make unpacman in your own
email program, with that report attached if you agree — you can read it
first — and you send it from there; unpacman itself sends nothing. If your
email lives in a browser and nothing opens, *Copy the message* puts it on the
clipboard instead.

Running the installer again is safe: it repairs what is missing and brings
the copy up to date — if unpacman is open, it is closed for that and opened
again at the end, unless a reading is running, when it is left alone. To remove unpacman, delete the Unpacman icon
and the folder `~/.unpacman` (in Finder: ⇧⌘G, type `~/.unpacman`) — the
second holds your papers and readings, so keep a copy of anything you want.

The same from a Terminal, for those who prefer it: `python -m deslopify.app
--check` says what this computer still needs, `--app` does what the icon does,
`--make-app` makes the icon for a copy you cloned yourself.

### The reader — unpacman

The simplest way in: a separate, quiet page that does one thing. You give it a
paper; it gives you back a companion. In the browser it is called **unpacman**
(the package, the harness and the lab still say Deslopify).

Installed as above, open the Unpacman icon. From a copy of this repository:

```bash
cd ~/Code/atelier
uv run python -m deslopify.app
```

Your browser opens at `http://127.0.0.1:7720/` (or the next free port, if
something else holds 7720; started a second time, it opens the running one
instead of starting another). The first time, small tips
point out what each part of the page does, one at a time; *Skip tips* stops
them, and the **?** in the bar shows a screen's tips again.

**Starting a reading.** Drag a PDF anywhere onto the page, or click to choose
one — or, for a paper on arXiv, type its number under the drop box
(`2309.01234`, `math/0601001`, with a version if you like, or paste its
arxiv.org address or the line from its margin) and press *Fetch*: unpacman
fetches the PDF from arXiv and goes on exactly as if you had dropped it,
naming the paper as arXiv lists it. Then choose who the companion is for —
**Expert** or **Non-expert**. They are two different methods: Expert writes a
dense, research-level companion for
someone working in or near the field; Non-expert an advanced-encyclopedia
companion for a mathematician from outside it. Choosing starts nothing. Below
the two cards, in plain view, is what the reading will use: the **AI** (any
Claude or ChatGPT model the lab offers, on whichever is installed here), its
**effort** (low to maximum, all visible at once), and its two **limits**, in
dollars and in minutes — it stops at whichever it reaches first. They start at
the server's defaults; type another value and it is kept, *Use the defaults*
puts them back, and a value outside $0.10–$200 or 1–600 minutes is refused
before anything starts. A ChatGPT model has no per-token price, so only its
time limit can apply, and the money field says so by switching off. Tick **For pure
math papers, add a one-page diagram (optional)** if you want it. The reading begins only when you press
**Start the … reading**; nothing is spent before — and Start stays off, saying
why with a link to *Getting ready*, while the chosen AI is not signed in here or
LaTeX is missing, since such a reading could not finish. A paper can have both
readings — read it one way, then *Also read it for a non-expert*, and switch
between them under the title — and reading it again keeps the companion you
have until the new one is finished. A paper opens at its **Runs** page: every
reading of it, newest first, each numbered for good (*Run 1* is the first);
*Companion ready* marks the companion each level opens at, and earlier ones
stay there to open and compare.

**A Non-expert reading asks first what you know.** After *Continue to the
questions*, the AI that will read the paper looks at what it assumes and asks
you between 10 and 25 short questions — "Do you know the definition of a
Riemannian metric?" — answered *Yes*, *Roughly* or *No*; a *No* can bring a
follow-up one step down ("…of a smooth manifold?"). The questions take up to a
minute or two the first time and cost a few cents; they are kept with the
paper, and so are your answers, to start from next time. Every pass of the
reading is told your answers: the companion opens with a *Background you will
need* section that explains what you said you do not know and recalls what
you know roughly (something the paper needs at one point only is
explained where it is used), and skips what you know well — its mathematics
unchanged. *Skip the questions and start* reads the paper as
before; *See your answers*, under a companion's title, shows what it was
written for. Several papers read together are asked about in the same way
before their Non-expert companion: one set of questions for all of them, each
saying which paper needs it, and your answers kept with the set.

**Several papers at once.** Drop or choose several PDFs together (up to twelve),
or type several arXiv numbers at once — or one, then more: once a paper is
chosen, *Add another paper* under it (or a PDF dropped anywhere on the page, or
an arXiv number in the box just below it) adds to it, one at a time, and each
paper of the set has *Remove* until it is read — and they are read together:
each becomes a paper of its own, still readable alone, and together they are a
set, with its own launch, runs and reading, at either level, checked and
revised as a paper's reading is. The set's **Papers** tab lists its papers, each
opening as the paper it is. A single paper or a set may have at most 10,000
source-PDF pages in total.

If the extracted text will not fit the selected reader AI, unpacman uses only
the beginning that fits. In a set, it makes this decision separately for each
paper, keeping shorter papers complete. Each affected paper's companion says
clearly that its source text was too long and the explanation covers only the
beginning; the uploaded PDFs themselves remain complete.

**Finding papers.** Under the drop box, ask for papers on a subject — "the
latest 5 arXiv preprints on the Langlands program", "which papers computed …". The
search runs like a reading, with the same AI, effort and limits (by default
$4 and 20 minutes): it looks on arXiv, zbMATH Open, OpenAlex, Semantic Scholar
and the web, confirms every paper it lists at its source, and reports them —
grouped, each with what it does and why it is there, and links. A paper whose
PDF is open can be added to your papers in one click (*Add to my papers*), and
read there. *Search again*, on its Runs page, runs it anew — with the question
changed if you like — and keeps the earlier results.

While it works you see the three Balanced steps, each described in a sentence,
and what each one found once it is done. You can close the page: the reading
carries on for as long as unpacman is running, and unpacman keeps the Mac awake
meanwhile. A reading that stops without a companion says why in plain words
where it can — the AI not signed in, a plan's usage limit reached, no internet —
with the way to put it right. When it finishes, the
companion opens — the explanation in sections and what the independent check
found. A proof map, if asked for, sits after *About the proofs*: click any step
to see what it rests on, what it is used for, and its passage in the paper.
The boxed source-and-scope remark precedes Main results/findings, after any
opening lead; in a collection, each paper has its own remark below its heading.

**The paper, the companion, or both.** The bar switches between the
*Companion*, the *Paper* itself — drawn inside the page rather than in the
browser's PDF viewer, so that it can be selected like the companion — and
**Side by side**: the companion on the left and the paper on the right, each
scrolling on its own, with a divider between them. Drag the divider to give
either side more room, or move it with the arrow keys; double-click it to
centre it. Where you left it is remembered. On a narrow screen the two are
stacked, the companion above the paper, and the divider moves up and down. A
page number in an explanation moves the paper's side to that page. The paper
is there before the companion is, too: *Read the paper while you wait* while a
reading runs, *Look at the paper first* before starting one. It can be zoomed
— **−** and **+** on its bar, or a pinch on a trackpad over the pages — from
half to four times the width it fits, drawn sharp at each size; the level
shown on the bar takes it back to that width when clicked, and is remembered.
A page wider than its column then scrolls sideways within it.

**Anything unfamiliar can be explained where you meet it — and asked about.**
Select a word, a symbol, a formula or a passage — in the companion or in the
paper — and right-click it. *Explain* sends a few sentences to a note beside
the text (first words in about a second, the whole answer in two to four on
Claude), saying what it means *in this paper*: the definition and its page if
the paper gives one, the standard meaning and a plain statement that the paper
does not define it if not. *Ask about this…* turns the menu into a small box
for a question of your own — why a step holds, where something is used, what
would settle a claim — answered from the paper and the companion under the same
rules, saying plainly when the paper does not settle it. Under any note,
**Explain more** adds a longer explanation, and the follow-up box continues
the conversation about the same words: each follow-up is answered knowing what
was said before. New conversations are saved with the paper and restored when
you reopen it, including in a new browser tab; click a note's term to
fold it out of focus and again to bring it back, *Hide* folds the whole panel
into a button, and *Show in text* finds the words again. Drag the panel's left
edge to make it wider or narrower (double-click it for the usual width); the
text beside it moves over, and the width is remembered. **Ask a question**, in
the bottom-right corner, asks about the paper as a whole, with nothing
selected — what it proves, what it assumes, where to start — and the answer is
a note like the others, with its follow-ups. The AI that read the
paper answers, unless you choose another under *Explained by*; asking the same
thing twice costs nothing, because every answer is kept with the paper. On a
touch screen, select and tap *Explain* or *Ask…* on the button that appears; a
second right-click opens the browser's own menu. The first time you use
unpacman, once your first reading has started, a box in the corner suggests it
— open the paper while you wait, highlight, right-click, ask: there is no need
to wait for the companion — and stays until you have asked about a selection
once; it is then gone for good, and someone who had asked before it existed
never sees it.

**Key terms are links, Wikipedia style.** When a companion opens, the terms it
most needs — more of them for a non-expert reading — become blue links: the
first mention in each section. Point at one and a small card shows a short
explanation, under the same rules as any other; *Explain more* in the card
keeps it as a note and asks for the longer one. The terms are chosen once per
companion (about fifteen seconds and two cents on Haiku, the first time) and
kept with the paper.

**Companions use academic heading numbering**: sections 1, 2, …; subsections
1.1, 1.2, …; and subsubsections 1.1.1, … . Main section titles are larger and
more prominent in both the webpage and PDF. References remain unnumbered.

**The companion as a PDF has a table of contents**, every section and the
references, each a link. An older companion gets the current heading style
too: the first time its PDF is opened it is typeset again, from its own LaTeX
source (or, for readings older than the sources, from its guide), once, and
kept with the paper; the reading's own record is left as it was.

Multi-paper companions may open with **How the papers are related** when
several papers are clearly related. Its subsections explain conceptual
relationships within naturally related subsets of the batch. There is no handwritten Contents
section, and each paper starts on a new page in the PDF.

**Advanced settings** — on the first screen, and at the foot of the launch
panel — holds what used to be command-line flags: which AI reads and at what
effort, the limits for each reading (the same ones as on the launch panel), how
many readings may run in parallel, which AI explains, whether key terms are
linked, and **Appearance**: light, dark, or as this computer is set (the
default). A model not yet in the configured menu can be entered by its exact ID
under **Custom model** and assigned to an installed Claude or ChatGPT account.
Settings are kept in this browser.

**Your mathematical background**, also there, is a box for what you know — a
few lines, or your CV (*Read it from a file…* takes a PDF or text file, to
look over before it is kept): your field, what you work on, what you know well
and what you do not. It is kept on this computer, saved as you type. Every
reading of a paper or of several is then written for you — the launch panel
says so, and *Write it for your background* can be unticked for one reading —
the questions before a Non-expert reading are chosen with it, and
explanations are pitched to it. It is sent to the AI with each of them, as the
paper is; a companion written for it says so and shows it.

**About**, in the bar, says in a few lines what unpacman is for, what it can
reach on your computer and what it sends where, and that it makes the mistakes
of the AI it reads with.

**The method itself can be read and changed there**, under *The method*: every
file a reading is made from — for a paper, Expert and Non-expert side by side,
each first deciding whether the paper is pure or applied mathematics and then
following that file; the check, the revision, the proof map, the fidelity rule
every pass works under, the manifest (`harness.toml`: passes, budgets, tools,
permissions) — and the instructions explanations, questions and key terms
follow. Open one, change it, *Save*. Your
version is kept in `atelier/method.local/` (on this computer only, never
committed); the original is never changed, *Show the original* swaps in the
file as it ships to compare with yours, and *Reset to the original* — or *Reset
every file to the original* — undoes it. A version that would stop the method loading is refused
with the reason (invalid TOML, a role renamed, a level's variant gone, a role's
header removed), and removing a placeholder such as `{deslopify}` is allowed
but said. A reading started while anything is edited runs with its own frozen
copy of the method, in its run folder, so editing again cannot change it
half-way; its companion says *with your own method*, and so does its line on
the Runs page. Explanations use edited instructions from the next question on,
and keep what was answered under the originals apart. The roles of the lab's
five-pass pipeline are listed too, folded away: readings started here do not
run them unless the manifest says so.

Paper companions share `harness/SKILL-common.md` for source handling, statement
boxes, illustrations, historical research, and citations. Readers load that
file once alongside their pure/applied audience profile; revisers use the same
saved copy. It is also editable under *The method*.

Worth knowing:

- **The companion follows the method's format**: a boxed remark naming the
  source, authors, and date with the correctness notice, theorems and
  questions set as statements, citations such as `[Mil56, Theorem 2]` that link
  to the bibliography at the end (hover one to see the entry), and at most one
  section of the paper's own (a dictionary, a table of regimes) where it is
  needed. *Its LaTeX source* in the rail is the file the PDF is typeset from.
- **Papers are kept in `atelier/papers/`**, not in your problems folder. That
  folder holds research, and a paper you read is not a research problem.
  `--papers <folder>` keeps them somewhere else. They are never committed to git.
- **The same PDF dropped twice is recognised** and opened, not read and paid for
  again.
- **A file opened in a tab of its own** — the paper's PDF, the companion's —
  needs unpacman open in that browser. If unpacman was closed or restarted
  since, or the browser forgot its session, the tab renews it by itself when
  it can, and otherwise says in words to open unpacman and reload the tab.
- **A scanned PDF is refused at once**, since there is no text in it to read.
  Run OCR on it first. The text of any other PDF is read with `pdftotext` or
  Ghostscript if installed, else with PDFium (`pypdfium2`), which comes with
  unpacman — so nothing needs installing for it. If an external extractor
  returns mostly layout padding, unpacman uses PDFium for that paper instead.
- **Until you choose, it uses the harness's own model** — Claude Opus 5.5
  (GPT-6.1 Sol on ChatGPT) at high effort, up to $10 or 50 minutes a Balanced
  reading. The launch panel
  suggests 20–30 minutes for a companion and 5–10 minutes for a literature
  search, in the warning colour when the budget is below the suggested range.
  To try the interface
  cheaply, choose Claude Haiku 4.5 at low effort — but read *Choosing a model*
  first: a small model's companion is not one to rely on, and the page says so
  both when you choose it and at the top of what it writes.
- **The companion says which AI wrote it** — *Expert reading by Claude Opus 5.5* —
  and "Try again" goes back through the launch panel, so a reading that failed
  on one AI can be tried on another.
- **Questions and explanations can research public sources.** Each receives the
  paper, companion and complete follow-up thread, with web search and page
  fetching but no local file tools, commands or connected apps. Effort defaults
  to High and is adjustable beside the chat's model selector or under
  Advanced settings → For explanations. Both controls use the same setting. There
  are no application limits on question length, answer length, response time or
  conversation history; the selected model's context and provider limits still
  apply. Answers are kept per model and effort in the paper's
  `.atelier/explanations.json`, outside the lab's spending figures.
  Complete new threads are saved in `.atelier/conversations.json`; older tab
  histories are not imported. An answer interrupted by quitting unpacman can
  be retried when you reopen its note.
  Notes appear oldest first, with a visible follow-up box under each answer.
- **The command line still works**, for scripts and tests: `--model` and
  `--effort` choose where the page starts each time it opens (a choice made on
  the page since then wins), and `--max-usd` / `--max-minutes` set the default
  limits the launch panel starts from.
- **The companion is honest about its own status.** If the check raised points
  that were never answered, or the reading stopped before anything checked it,
  the page says so at the top, before the explanation begins.
- **Each step can have its own AI.** A reading is three steps — the first
  writes the companion, the check reads it against the paper, the revision
  answers the check — and *Advanced settings › Each step of a reading* can give
  the check or the revision to another AI, Claude's or ChatGPT's or an
  open-weight one: a second opinion rather than the same reader checking its
  own work. The launch panel lists the steps' AIs before Start, and the
  companion says who wrote, checked and revised it.
- **The menu has the newest models.** Besides the lab's own list, unpacman
  asks the programs: Claude Code names the newest model of each kind (Opus,
  Fable, Sonnet, Haiku — asked without sending any message, so nothing is
  spent), and Codex keeps the list of models your ChatGPT account can use. A
  model released after this checkout still appears; a Claude model without a
  price in `pricing.toml` reads under the time limit only, and the page says so.
- **DeepSeek** reads beside Claude and ChatGPT, from DeepSeek's own service
  (a key pasted on Getting ready or in *Advanced settings › Other ways to
  read*; paid in advance per use, at DeepSeek's prices, so a money limit
  works): *DeepSeek V4 Pro* and *DeepSeek V4.1 Flash*, through Claude Code,
  which must be installed but needs no Claude plan. It searches the web
  itself, so its companions' references are searched, and it can run a
  literature search.
- **[OpenRouter](https://openrouter.ai)** reads too: one account, paid in
  advance per use, for models of many makers (a key pasted on Getting ready or
  in *Advanced settings › Other ways to read*; each model's price is
  OpenRouter's own, so a money limit works). Claude's newest models come first
  — OpenRouter says Claude Code works for certain only with Anthropic's — then
  open-weight ones (DeepSeek, Qwen, Kimi and others); any other it runs can be
  named as a custom model. In the menu they say "(OpenRouter)", and a
  companion is credited "… through OpenRouter". Whether web search works
  through it is not known yet, so the page says the references *may* come from
  memory, and it is not offered for a literature search.
- **Other open-weight models** — Qwen, Kimi, gpt-oss and others — can read
  too, on this Mac with [Ollama](https://ollama.com) (free and private; a
  model that can use tools, such as `gpt-oss:20b`, and Claude Code installed).
  They cannot search the web here, so the references they give come from
  memory, and the page says so; they are not offered for a literature
  search. On this Mac they are slow, and a model
  takes most of the Mac's memory while it reads (the suggestions are sized
  for twice their download). A model on this Mac reads with a copy of
  itself made to hold a whole paper (`unpacman/<model>`, no extra space), and
  when the extracted paper is longer than the chosen reader AI can hold,
  unpacman reads its beginning and says so clearly in the companion. A model
  too small to hold even the method and beginning is refused before it starts.
- **Old runs can be deleted**: *Delete* beside each run on a paper's Runs
  page, *Delete the earlier runs* under the list (all but the companion each
  level opens with), *Delete this paper*, and in *Advanced settings › This
  computer* the earlier runs of every paper at once. Each deletion asks for
  confirmation. A run still reading is stopped first. A run keeps its
  number whatever is deleted.
- **News of new versions** is offered once, on the home page: the address of
  the account you signed in with is filled in, and nothing is sent unless you
  press *Keep me posted* (it goes as a short email your own email program
  opens). *This computer › News* shows it and stops it.
- **A reading is kept to its own folder**: its commands may read only the
  paper, its own working files and installed tools, write only its workspace,
  and reach public websites for its research (Antoine's access policy,
  `docs/unpacman-access-policy.md`). Codex must understand every setting of it
  — an older one is refused, and Getting ready offers to update it.
- `--reload` restarts the server whenever its code changes. That is for working
  on the interface, and it stops any reading in progress.

The interface lives in `deslopify/deslopify/app/` and has its own tests in
`deslopify/tests/test_app.py`, `test_explain.py`, `test_method.py`,
`test_ready.py` (getting ready, the icon, running in the background),
`test_updates.py`, `test_package.py` (packages, and the installer itself),
`test_kinds.py` (questions, several papers, searches),
`test_collection_questions.py` (the questions before reading several papers),
`test_arxiv.py` (a paper by its arXiv number), `test_page_policy.py` (the
page loads nothing from another website), `test_steps.py`,
`test_open_weight.py`, `test_removal.py`, `test_news.py`, `test_cv.py` (your
mathematical background), `test_deepseek.py`, `test_openrouter.py`,
`test_catalog.py` (which models the programs offer), `test_fetch.py` and
`test_search_selection.py`, and page logic in `tests/web/*.test.mjs` (run by
pytest when `node` is installed). It
drives the ordinary Atelier pipeline, so a paper read here leaves exactly the
run records a session from the lab does. What it does not do yet — among them,
that a finished companion keeps no record of how its reading went — is listed
in `ISSUES.md`, part two. `deslopify/tools/` holds what is used to look at it
in real browsers: two Playwright scripts for Chrome, two small WebKit programs
for Safari's behaviour, and `isolated_server.py`, which runs the reader on its
own papers, runs, database and method edits (optionally seeded with a copy of a
real paper) so that testing never touches yours. `DESIGN.md` §13 is why the
reader is built the way it is, §14 why explanations are, §15 how two readings
of one paper live side by side, §16 key terms and settings, §17 what Antoine's
Balanced pipeline changed in it, §18 the launch, side by side, questions, the
editable method, tips and the contents, §19 how it is installed, started and
updated by someone who has never used Terminal, §20–21 the rounds of 6–7
October, §22 each step's AI, open-weight models, deleting and news, §23 the
questionnaire, About, the method list, papers one at a time, a mathematical
background, DeepSeek and the sandbox made to work with Claude, §24 Codex
installed whole, §25 the newest models and OpenRouter, §26 About in
Tancredi's words, the time a reading is given, and another account, §27
Windows, through WSL (branch `unpacman_windows`), §28 the first run's
suggestion, Change account from Advanced settings, and a refused tab of its
own, §29 the suggestion shown while the first reading runs, light or dark,
zoom, a resizable panel of answers, questions about the paper as a whole, and
statement boxes that hold the whole statement, §30 the questions before a
Non-expert reading of several papers, §31 a paper by its arXiv number, §32
what the page may load — nothing from another website, so that text an AI
wrote cannot make the browser call one — and the lab's front door, §33 why,
between a reading's steps, nothing of its AI's own folder is ever followed —
the controller works on a private copy, and §34 why a stopped reading is read
only from its archive.
`isolated_server.py --pretend fresh` shows Getting ready as a new Mac would,
with stand-in installs, and `--standin` answers every AI call with the
stand-in programs in `tools/standin/`, so that a whole reading can be watched
for nothing.

### From the Atelier lab

`New session` → pick the problem → choose either **Deslopify — Expert reader**
or **Deslopify — Non-expert reader** → mode **Standard** → a capable model
(see *Choosing a model* below) → start.

When either Deslopify entry is selected, the launch window also shows a
**Reader level** selector. Switching it to **Expert** or **Non-expert** selects
the corresponding skill before the run is launched.

Expert mode assumes a mathematician working in or very near the field and
prioritizes dense, inspection-ready proof detail. Non-expert mode assumes a PhD
mathematician outside the field and aims at an advanced-Wikipedia level: it
defines specialist language and supplies conceptual bridges while preserving
the exact theorem and proof architecture. Both modes have the same eight
required guide sections, may add one genuinely useful paper-specific section
with a title and form adapted to the paper, and use the same Balanced pipeline.

A **Verification pipeline** switch appears for these reader variants. `1` runs
the three Balanced passes; `0` runs only the guide writer. The base `deslopify`
harness remains the five-pass Thorough pipeline for comparisons. The budget
covers the selected pipeline, with enforced handoffs between its sessions.

### From the command line

```bash
cd ~/Code/atelier
uv run atelier run --problem <slug> --harness deslopify-nonexpert --mode standard \
  --model claude-opus-5 --effort high --max-usd 10 --max-minutes 50 \
  --task-file progress/source.pdf
```

Use `--rounds 1` for the harness's verification pipeline, or `--rounds 0` for
the guide-writer alone. The Expert and Non-expert harnesses use three-pass
Balanced; the base `deslopify` harness uses five-pass Thorough.

Use `--harness deslopify-expert` for the specialist version. The older
`--harness deslopify` name remains a backward-compatible alias for expert mode.

Point at one Markdown or PDF file in the interface, or pass it with
`--task-file`. If no file is selected, Deslopify looks for
`progress/source.md` or `progress/source.pdf`; `progress/math.tex` remains a
backward-compatible fallback. To deslopify something else, make a problem for it:

```bash
uv run atelier new my-document --title "Whatever it is"
cp /path/to/the/paper.pdf ~/Code/problems/my-document/progress/source.pdf
```

(For an `.rtf`, convert first: `textutil -convert txt -stdout in.rtf > out.tex`.)

### What you get

During the run the files are built in its private
`runs/<id>/workspace/progress/reading/` directory. When the run ends, the
reader-facing products are archived in `runs/<id>/reading/` and listed on that
session's webpage under **Document** and **Run artefacts**:

| File | What it is |
|---|---|
| `guide.md` | the reader-first explanation, written before the graph |
| `graph.json` | an independent agent's grounded argument graph of the guide and paper |
| `map.mmd` | the map as Mermaid source |
| `map.pdf` | the same graph as a directly viewable vector PDF |
| `clarity-review-1.md` | what was unclear when an independent verifier read only the guide and graph |
| `companion.md` | the guide under its reader-facing filename |
| `companion.tex` | the persistent LaTeX typesetting source generated from the guide |
| `companion.pdf` | the directly viewable form compiled from `companion.tex` |
| `review-log.md` | what the second reader objected to, and what was done about it |
| `review-N.json` | one round's findings, each located and, where it is about the document, quoted |
| `dispositions-N.json` | the answer to every finding: fixed and where, or rejected and why |

`review-log.md` is written by the app rather than by any session, for the same
reason the lab book is. It is the only place that records where this reading was
contested and where it held, which makes it the most useful page in the
companion for a referee — and the place to look first if you distrust the guide.

The run record keeps one set of notes for the whole pipeline, with each pass's
account of itself inside it, plus measured cost and duration. The source problem
is not edited or committed. Because every run has a different workspace and
archive, several Deslopify runs can safely analyze the same problem at once.

### Asking your own questions afterwards

The graph is queryable. From the problem folder:

```bash
D=~/Code/atelier/.venv/bin/python
$D -m deslopify view    progress/reading/graph.json      # the whole structure
$D -m deslopify companion progress/reading/guide.md \
       --markdown progress/reading/companion.md \
       --tex progress/reading/companion.tex --audience expert \
       --pdf progress/reading/companion.pdf
$D -m deslopify map     progress/reading/graph.json --ascii
$D -m deslopify map     progress/reading/graph.json --pdf progress/reading/map.pdf
$D -m deslopify why     progress/reading/graph.json X5   # what was it introduced for?
$D -m deslopify uses    progress/reading/graph.json H3   # where is it used?
$D -m deslopify without progress/reading/graph.json C21  # what fails without it?
$D -m deslopify between progress/reading/graph.json C9 C12
$D -m deslopify check   progress/reading/graph.json      # re-verify every quotation
$D -m deslopify appeals progress/source.pdf              # where work was skipped
```

And, for the review loop's own files:

```bash
$D -m deslopify coverage progress/reading/graph.json progress/reading/guide.md
$D -m deslopify review   progress/reading/review-1.json \
       --graph progress/reading/graph.json --guide progress/reading/guide.md
$D -m deslopify dispose  progress/reading/review-1.json \
       progress/reading/dispositions-1.json --graph progress/reading/graph.json \
       --before-graph <run>/loop/lane/graph.json
$D -m deslopify diff     <run>/loop/round-0/graph.json progress/reading/graph.json
```

`--before-graph` is the companion as the critique found it: a fix that *removed*
a wrong edge names a location that no longer exists, and judging it against the
revised graph alone would reject the cleanest kind of repair.

`coverage` is arithmetic on the graph and the guide together: a gap recorded and
never mentioned to the reader, a reference to a node that does not exist, a
hypothesis the guide never locates. It is run for you before a critic is asked
for an opinion, so that a reader's attention is not spent on what a regular
expression does perfectly.

---

## How it works

### 1. The one idea

The questions a reader asks — *what is the main idea, why was $X$ introduced,
where is $H$ used, which step fails without $A$, how do these two lemmas relate*
— are not five separate tasks. Each is a **query against a dependency graph of
the argument**.

The harness now writes the guide first, then asks an independent session to
turn its claimed structure into a graph grounded in the paper. The graph builder
aims for a sparse, one-page proof roadmap—normally 6--10 nodes—and draws its
editorial principles from a specified corpus of human mathematical dependency,
implication, and conceptual diagrams. It borrows their economy and precision,
not their mathematical content or surface appearance. After that, the graph is
the queryable analysis: the map is not a decorative picture, but a rendering of
`graph.json`. The deliberate guide/graph independence makes their disagreements
visible to the verifier.

### 2. The graph

Nodes are typed, and the type letter is the id prefix, so an answer can point at
the map:

| Id | Role |
|---|---|
| `H` | hypothesis — a standing assumption or definition taken as given |
| `G` | goal — what is to be proved; a sub-goal; a case obligation |
| `X` | construction — an object, map or quantity brought into existence |
| `M` | move — a technique: induction, contradiction, a cited theorem |
| `C` | claim — a lemma or intermediate assertion |
| `Q` | conclusion — the theorem, as established |
| `!` | gap — an unproved step, an appeal to unshown work, a hand-wave |

Edges run in the direction of support: `SUPPORTS` (used in establishing),
`MOTIVATES` (exists in order to serve), `CONTRADICTS` (undermines). Each carries
a justification. The graph is acyclic. Gaps carry no edges — they annotate a path.

**Every node carries a verbatim quotation from the document.**

### 3. Support is conjunctive

This is the one place where borrowing the representation from the literature —
which was built for empirical papers — would have been wrong, and it is what
makes *"which step fails without $A$?"* answerable at all.

A paper's conclusion may rest on several findings *any* of which lends it
weight. A proof step needs **every** premise it invokes. So a node stands only
when all of its supports stand, and `without A` removes $A$ and reports
everything that no longer stands. Under plain reachability, removing one of two
premises would leave the target "still reachable" and the question would
silently answer *nothing*.

### 4. What a session actually does

Set up before the model runs, because `harness.toml` declares `kind = "read"`:

- the prompt gets the **reading contract** instead of the solving one (which
  would have told it to fold its results into the document it must not touch);
- the system prompt gets **`shared/policy/fidelity.md`** instead of `rigor` —
  rigor governs promoting a claim to established, which a reader never does;
- `{deslopify}` in the method is replaced by the real interpreter path, and the
  session is given permission to run it;
- **the SHA-256 of the selected Markdown/PDF source is recorded** in the run directory;
- the Stop hook is set to `check_reading.py`;
- the complete Markdown text or extracted PDF text is put in every source-aware
  pass; it is deliberately omitted from the blind verifier's prompt.

The Thorough run uses five fresh sessions: a guide-writer reads the paper and writes
`guide.md`; an independent graph-builder reads that guide and then the paper,
builds `graph.json`, validates it, and renders both maps; a blind verifier reads
only guide and graph and records concrete clarity questions; a source-grounded
verifier reads the original paper and files findings; and a reviser answers
those findings in both guide and graph. The reader-facing `companion.md`, its
persistent LaTeX typesetting source `companion.tex`, and the PDF compiled from
that source contain the final guide. No separate summary files are produced.

The reader-facing Balanced variants use a fresh auditor session to check source
fidelity, explanatory clarity, and reasonable historical coverage, then run a
focused reviser only when the audit raises something worth fixing. They always
compile the same companion files. For a requested diagram of a paper classified
as pure mathematics, the writer creates the graph and both map files. The
auditor checks them and the reviser repairs them when needed.

All providers follow the same delivery policy. With independent checking and
an overall time limit T at most 20 minutes, drafting gets T/2. For 20 < T < 60,
drafting gets T-10 minutes; from 60 minutes onward it gets T-20 minutes. The
controller enforces the drafting handoff, including retries.
Review and revision split the remaining time roughly 60/40; an enforced review
handoff preserves revision time. An early drafting finish gives checking more
time. The auditor may use up to five extra minutes beyond its planned
allocation, and the reviser has a separate five-minute allowance. Unused audit
grace does not transfer to revision; the run can exceed T by at most ten minutes.
A single-stage reading uses its full overall allowance.

Writers first save useful content in every required section, including the
bibliography, before expanding explanations, illustrations, or literature
research. Partial drafts proceed with an explicit missing-content checklist.
Review and revision use the remaining budget to complete and check them; a
missing section never blocks that handoff. If a writer returns with no guide,
the controller tries once to recover it. If a reviser returns with required
content, a compilable PDF, or its revision record still missing, the controller resumes that
session within the remaining overall budget until it finishes or time runs out. Reviewers always
start fresh conversations. No pass relies on a live deadline reminder.

The auditor saves supported findings progressively before optional PDF
inspection or further research. Visual checks use saved page images opened
with an image-viewing tool. If viewing is unavailable, the report says so;
encoded image data is never printed into the AI's tool output.

Each reading stage can check its clock with
`python -m atelier.time_remaining --state PATH`, using the exact command and
timing-file path in its instructions. The JSON reports stage and whole-run
seconds remaining and sets `wrap_up` at 90% of the current stage allocation.
Writers check before new research, after substantial tool calls, and
after saving sections. The clock uses the runner's timing, survives completion
retries without resetting the allocation, and works through the agent's shell
on every provider. The controller records a wrap-up warning at 90% and sends a
live nudge where the provider supports steering. The agent checks the clock to
receive the warning even when live steering is unavailable. Stage handoffs and
the overall limit remain enforced by the controller.

The controller generates Markdown, LaTeX, and PDF companion exports from the
guide before review, after changes, and when an interrupted run ends. Unchanged
exports are reused. A run with no guide is **failed**. The reader sees
**Completed** when content and configured checking/repair are finished;
**Draft — review unfinished** when the content exists but checking, revision,
or final delivery checks remain; or **Incomplete draft**, with missing content
listed. Both draft states use the internal `incomplete` run status.

An unfinished saved result offers **Continue this draft**. The reader selects
an additional time allowance. A new isolated run copies the saved workspace,
guide, figures, research, and reader answers, using the same AI choices and
spending limit. The original run stays available. A complete text goes directly
to a fresh independent audit; a partial text first gets its missing sections
completed using the saved evidence. Previous review records are retained as
research but cannot be mistaken for output from the new audit.

Continuations use the same T-10 or half-time drafting rule for their additional
allowance; resumed drafting cannot borrow the checking reserve.
Compilation and pass
transitions also count against the overall limit. Known export errors carry
forward as explicit repair tasks. The exporter defines the Tate--Shafarevich
symbol `\Sha`; other undefined commands are reported with their source context
for correction in the guide, without guessing their mathematical meaning.

On the way out, `check_reading.py` refuses to let the session end unless the
document's hash is unchanged, the graph validates, and the
guide, reader-facing companion, and session notes exist. It blocks at most twice,
then relents and records the gap, so a
session can never wedge.

### 5. The checks, and what they can and cannot catch

**Grounding is mechanical.** `check` confirms every quotation appears in the
document — and refuses to run if it cannot find the document at all. That second
half matters more than it looks: the `document` field is written by whoever wrote
the graph, so a graph naming a file that is not there once had every quotation
accepted unread, and exited 0, which a Stop hook reads as "grounded". Naming the
wrong file was the cheapest possible way to defeat the only guarantee this
package makes. The session's Stop hook now also supplies the document the run
recorded before the session began, so the graph's own claim cannot redirect it. Whitespace and line breaks are forgiven; a changed word or symbol is
not. This caught the first hand-built graph, where a quotation had been
truncated mid-formula and closed with a `$` the document does not contain.
Tidying a quotation *is* fabricating it.

**Structure is mechanical.** The graph is asked what it implies, and each answer
has a plain reading: a construction with no outgoing edge was introduced for
nothing; a claim with no incoming support rests on nothing; a component that
reaches neither goal nor conclusion is not part of this argument. `focus`
reports what share of the document serves its stated problem — 26% for a
document that accumulated material over many sessions, 100% for a tightly
argued one.

**Centrality is computed, not asked for.** Measured finding from the Syrup
thesis: models recover reachability and unsupported-node detection from a plain
text serialization (0.90–0.97) but essentially cannot recover centrality
(0.07–0.29), while it is trivial once injected. So it is computed here and put
in front of the model.

**The graph is handed over as compact text, not JSON.** Also measured: across
six encodings and three frontier models, JSON was dominated — more expensive
*and* less accurate.

**What no check can catch is repair by explanation**, and it is the commonest
failure in this task. The document asserts a search and never shows one; the
companion explains why such a search would have worked; the hole closes and
every quotation is still honest. The mitigation is `appeals`: it finds every
place a phrase stands in for an argument, and the graph must record `routine` or
`gap` for each, with a note. **The verdict is free; not deciding is not.** The
test to apply is not *"would that method work?"* but:

> Could a competent reader reconstruct the omitted work in their head, right
> now, without real labour?

### 6. The verification pipelines

The reader uses three separate model sessions:

    guide writer --> independent source auditor --> focused reviser (if needed)

The auditor first compares the guide's main results, definitions, hypotheses,
formulas, and limitations with the source. It then identifies unclear passages
and checks whether Historical context is reasonably complete. It does not
review the writer's skill files or edit the guide: its located findings go to
the reviser. For requested pure-mathematics diagrams, it also checks the writer's
diagram. The reviser runs
only for findings at `worth-fixing` or worse.

The base Thorough harness keeps the original five stages for experiments and
maximum scrutiny.

The five stages are separate model sessions with separate write lanes:

    guide writer --> graph builder --> blind verifier --> grounded verifier --> reviser

The graph builder encounters a finished guide but not the guide writer's hidden
reasoning. The blind verifier receives guide and graph but not the paper, so it
records authentic comprehension failures before source context can repair them.
The grounded verifier then reads the paper, resolves those questions, and files
only evidence-backed improvements. The reviser answers every finding in the
guide and/or graph and regenerates all derived files.

**The loop is pointed at fidelity, not at polish, and this is the whole design.**
Ask a model to judge writing for clarity, quality and smoothness and it will
reward fluency, because fluency is what those words name. The documents this
harness reads are machine-written; fluency is the one thing they never lack, and
a companion to one will be fluent too. A round of revision against that rubric
returns a guide that is better organised, more confident and no more faithful —
and the confidence is now earned by having survived review. That artefact is
worse than the one it replaced, and nobody downstream can tell.

So a finding is a claim about a **discrepancy**, and three rules keep it one:

- **It must be locatable.** `where` resolves against a node (`graph:C21`), an
  edge (`graph:H3->C21`), a heading of the guide, a line of the document, or an
  adjudication — or it is not admitted. "The middle drags" can be neither fixed
  nor refused on grounds, and cannot be shown to have been dealt with; it can
  only be complied with.
- **A claim about the document must quote it.** Exactly as graph nodes do, and
  verified the same way. A critic is not exempt from the rule the whole package
  exists to enforce.
- **A critic may not supply prose.** Whatever it writes would be pasted in, and
  the round would become the critic drafting the guide with an extra step — its
  own errors now carrying the authority of review. `fix` says what must be
  *established*, in a sentence or two, and the limit is enforced.

There is one kind for genuine confusion, `unclear`, and it is admitted only in a
form a revision can be measured against: it must state the question the reader is
left with. "This is confusing" has no answer; "which of the two maps does $T$
denote here?" has one or does not.

**Every finding must be answered.** The revision pass records each as `fixed` —
with the location where the change landed, so it can be looked at — or
`rejected`, with a reason. Rejecting is free and is often right: the critic did
not write the companion either and is wrong a useful fraction of the time. What
is not free is silence, which is how an inconvenient finding disappears.

**A pass may write only its own files.** The controller snapshots
`progress/reading/` before each pass and restores anything else it touched. The
verifier writes `review-N.json` and nothing more; the reviser may not edit the
review it is answering. Telling a model this is not enough — editing a file it
is holding an opinion about is the most natural thing in the world — and the
snapshot makes the instruction true instead of merely stated.

**Convergence is measured, not assumed.** Between rounds the graph is compared
with the graph before it, and changes to what the companion *claims* — a node,
an edge, a quotation, a verdict — are counted apart from changes to how it
reads. Both are legitimate, and neither is a fault on its own. What *is* flagged
is the contradiction: a finding recorded as fixed at a location in the graph
while the graph is byte-identical. That is the one way a revision can look like
work without being any, and it is the only case the app calls a problem.

Either pipeline stops before revision if its auditor/verifier raises nothing at
`worth-fixing` or worse. The auditor's compiled guide is then the final Balanced
companion; the graph-builder's compiled guide is the final Thorough companion.

**What it costs.** Balanced divides its 50-minute default cap across three
passes; Thorough divides 75 minutes across five. `--rounds 0` disables every
follow-up pass and leaves only the guide writer.

### 7. Choosing a model

**This does not work on a small model.** Measured on one document, same prompt,
one variable changed:

| Model | Appeals judged | Gaps found |
|---|---|---|
| `claude-haiku-4-5`, low | 12 routine, 0 gaps | 0 |
| `claude-sonnet-5`, low | 1 routine, 11 gaps | 10 |

Haiku adjudicated everything routine with notes like *"the method is sound"* —
answering *"would that method work?"* when the question is *"is the work
shown?"*. Sharpening the prompt did not move it. A weak model here produces a
well-formed, fully grounded, entirely reassuring companion to a document full of
holes, which is the one artefact this exists to prevent.

Observed cost at `claude-opus-5 --effort high`: **$3.35 and 13 minutes** on a
600-line research document, producing 52 nodes and 77 edges.

### 8. Limits

- **Grounding is not interpretation.** A verbatim quotation proves a node came
  from the document. It does not prove the role or the edge is right. That part
  is the model's reading, and it is meant to be visible and contestable rather
  than hedged into uselessness.
- **Consistency is not fidelity.** An extractor that produces the same wrong
  graph every run is perfectly consistent.
- **A single pass is not canonical.** The thesis measured 0.30–0.40 cross-run
  agreement on scientific papers. The review loop gives one independent reading
  of the *companion*, which is not the same thing: nobody builds a second graph
  from the document and compares. So the interesting hypothesis — that
  disagreement between independent readings localises where the prose is
  unclear — remains untested.
- **The critic inherits the drafter's frame.** It reads the document itself, but
  it reads the graph alongside it, and a structure the draft never noticed is a
  structure the critique is unlikely to invent. It is good at finding what the
  companion gets wrong about the document, and weaker at finding what both of
  them missed.
- References and credit are model-researched rather than mechanically proved.
  Harness version 12 requires compact alphabetic author-year citations, a
  complete LaTeX-set academic bibliography, primary-source checks, a fairness
  audit, and explicit scope limitations where priority or metadata cannot be
  verified; the stop hook checks citation-key integrity and the grounded
  verifier independently challenges the substance before revision.
