# Atelier

Atelier helps you work on a piece of mathematics together with an AI assistant,
and keeps an exact record of everything that happened.

You write down a problem. Atelier sets an AI assistant to work on it, saves the
mathematics it produces as a PDF, and records how long it took, what it cost,
and what changed. Because that record is measured rather than reported by the
assistant, you can try the same problem several different ways and compare the
results fairly.

Everything runs on your own computer. Nothing leaves it except your conversation
with the AI assistant.

**New here? Read this page from the top. It takes about ten minutes to set up.**

**Only want to read papers?** The reader, **unpacman**, installs on a Mac with
one line and needs none of the steps below: see *Getting it on a Mac* in
[deslopify/README.md](deslopify/README.md). On Windows it runs inside WSL, on
this branch, first run on a Windows laptop on 2026-10-19: see
[WINDOWS.md](WINDOWS.md).

## What you need before you start

| What | Why | Needed? |
|---|---|---|
| A Mac, or a Linux computer | These are the only ones it has been tested on | Required |
| **Claude Code** *or* **Codex** | One of these is the program that actually talks to the AI. Claude Code needs a paid Claude plan (Pro, Max, Team or Enterprise); Codex needs a paid ChatGPT plan. Free plans do not include either. | One of the two |
| **uv** | Installs the other pieces automatically | Required |
| **LaTeX** | Turns the mathematics into a properly typeset PDF | Strongly recommended |
| **Git** | Keeps a history of your work so nothing is lost | Strongly recommended |

Atelier works with **either** Claude or ChatGPT. You need a paid plan for
whichever you choose. If you have both, you can run the same problem through each
and compare, which is much of the point.

One method, `explore-verify`, is Claude-only. It works by sending every claim to
a separate second agent that tries to knock it down, and Codex has no equivalent.
The other three methods work with both.

## Setting it up

You will type five short commands. Open the **Terminal** program and type them
one at a time, pressing Enter after each. If a command prints something you did
not expect, stop and see *If something goes wrong* below.

**1. Install uv**, which fetches everything else Atelier needs:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**2. Install the program that talks to the AI.** Pick whichever you have a paid
plan for; install both if you have both.

*For Claude:*

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Then type `claude`, sign in through the browser window it opens, and `/exit` to
come back out. Official instructions: <https://code.claude.com/docs/en/setup>.
Do not use the Desktop app offered there — Atelier needs this terminal version.
Requires a paid Claude plan (Pro, Max, Team or Enterprise) or an API key.

*For ChatGPT:*

```bash
brew install --cask codex
codex login
```

Requires a paid ChatGPT plan. If you already use the Codex extension in VS Code
you are probably signed in already, and Atelier will find that login.

**3. Copy Atelier onto your computer.** Replace the address below with the one
you were sent:

```bash
git clone <the address you were sent>
```

That creates a folder called `atelier`. You can put it anywhere and rename it —
Atelier works out where it is on its own.

**4. Go into that folder and install it:**

```bash
cd atelier
uv sync
uv run atelier setup
```

`setup` checks that everything is in place and creates anything that is missing.
It prints a plain description of what it found. If something is missing, it tells
you the exact command that fixes it.

**5. Start it:**

```bash
uv run atelier serve
```

It prints an address that looks like `http://127.0.0.1:7717/#auth=…`, and
usually opens it for you. Otherwise, open that whole address in your web
browser. That is Atelier. The part after `#auth=` is a key made for this start,
so that no website you visit can reach Atelier; it changes each time Atelier
starts, so use the address printed that day rather than a bookmark.

To stop it, go back to the Terminal window and press **Control + C**. To start it
again another day, open Terminal and type `cd atelier` then `uv run atelier serve`.

## Using it

**Your problems live in their own folder**, separate from the program. By
default that is a folder called `problems` inside the `atelier` folder, and
`setup` has already made it for you.

Each problem gets one folder, holding the statement of the problem, any notes
you want the assistant to read, and the work done so far.

A normal session looks like this:

1. Open Atelier in your browser and choose a problem.
2. Press **New session**.
3. Choose a **harness** — this is the working method the assistant will follow.
   `chat` is the plainest: one reply, no tools, like asking in a chat window.
   `solo` lets the assistant read and write files. `explore-verify` makes it
   argue against its own results before accepting them.
4. Choose the **mode**. *Standard* lets the assistant see everything done
   before. *One-shot* hides all previous work, which is how you find out what a
   method achieves from a cold start.
5. Either type what you want done, or point it at one of your own notes.
6. Set a **budget** — a maximum in dollars, in minutes, or both. Atelier warns
   the assistant when it is close to the limit and stops it at the limit. On a
   ChatGPT plan there is no per-token price, so use a limit in minutes there: a
   dollar limit could never trigger, and Atelier converts it to a time limit
   rather than leave the session unbounded.
7. Watch it work. You can pause it or stop it at any time. On Claude you can also
   send it a message mid-session; a Codex session runs as a single turn and
   cannot accept one, so stop it and start again instead.

When it finishes you get a PDF of the mathematics, an entry in the problem's
lab book with the measured cost and duration, and a complete transcript.

**Every session produces a PDF**, including `chat` sessions, which cannot write
files.

- A **standard** session advances the problem's own document, whatever method it
  used. A method with no file access cannot do that itself, so Atelier folds its
  answer in as a new, clearly marked section and rebuilds the PDF. Nothing
  already in the document is changed or removed.
- A **one-shot** session keeps its own PDF with the session instead, because a
  blind attempt is not the problem's state.

In the interface: the **PDF** tab is the problem's own document, and the
**Sessions** tab lists each one-shot with its own separate PDF.

## If something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| `command not found: uv` | Step 1 did not finish. | Close the Terminal window, open a new one, and try again. |
| `command not found: claude` | Claude Code is not installed. | Do step 2. |
| Setup says Claude Code is missing | Same as above. | Do step 2, then run `uv run atelier setup` again. |
| Setup says LaTeX is missing | You will get no PDFs; everything else works. | On a Mac: `brew install --cask mactex-no-gui`. This is a large download. |
| A session ends with no answer | The assistant used up its whole allowance on thinking. | Lower the effort setting, or raise the budget, or use a harness that has tools so partial work is saved as it goes. |
| The page looks wrong or out of date | Your browser kept an old copy. | Reload the page. |
| A method says it does not run on your AI | One method, `explore-verify`, is Claude-only: it needs a second assistant to check the first, and Codex has no equivalent. | Use `solo` or `chat` instead, or a Claude account. |

If you are stuck, run `uv run atelier doctor`. It checks everything and prints
what is wrong.

## Where your things are kept

| Folder | What is in it | Shared with others? |
|---|---|---|
| `problems/` | Your mathematics | **No** — private to your computer |
| `runs/` | Every transcript, prompt and PDF | **No** — private to your computer |
| `harnesses/` | The working methods | Yes — shared by everyone |
| `atelier.local.toml` | Your own settings, e.g. where you keep problems | **No** — private to your computer |

Nothing private is ever sent to the shared copy of Atelier. If you want your
problems kept somewhere else, create a file called `atelier.local.toml` in the
`atelier` folder containing:

```toml
[paths]
problems = "~/wherever/you/want"
```

---

# Reference

Everything below is detail. You do not need it to use Atelier.

## How it is arranged

Three things, kept strictly separate — that separation is the whole design:

```
PROBLEM                    HARNESS                   SESSION
data, durable              method, versioned         one execution, logged
problems/                  atelier/harnesses/        atelier/runs/
```

A problem never contains harness configuration. A harness never contains problem
state. A session is the only thing that touches both, and it leaves a complete
record.

## Commands

| Command | What it does |
|---|---|
| `atelier serve` | The web interface |
| `atelier doctor [--live]` | Environment checks; `--live` probes the provider CLIs and account auth |
| `atelier run --problem P --harness H` | One session, headless, streamed to the terminal |
| `atelier preview --problem P --harness H` | Print the exact compiled prompt without spending anything |
| `atelier new <slug>` | Scaffold a problem |
| `atelier import <path>` | Adopt an existing folder as a problem |
| `atelier init-git` | Make the problems directory a git repo |
| `atelier reindex` | Rebuild the database from `runs/` on disk |

Useful flags on `run`: `--model`, `--effort {low,medium,high,xhigh,max}`,
`--mode {standard,oneshot,continue}`, `--max-usd`, `--max-minutes`,
`--task "…"`, `--task-file strategy/plan.md`.

## A problem folder

```
problems/collatz-subsums/
├── problem.toml            metadata: title, tags, which file is the statement
├── problem.md              STATIC — the statement
├── strategy/*.md           STATIC — your notes and prior art
├── refs/                   STATIC — papers, data you supply
├── progress/               AGENT-WRITABLE
│   ├── math.tex            the mathematics; compiled to math.pdf on every edit
│   ├── state.json          machine-readable status; drives the UI
│   └── labbook.md          session log — written by the app, not the agent
├── work/                   AGENT-WRITABLE scratch: scripts, data, experiments
└── .atelier/               app-owned (lock, indices); gitignored
```

Static/writable is **enforced**, not merely documented: a `PreToolUse` hook
refuses edits to the statement and explains why. `--allow-static-edits` overrides
it per session when you genuinely want the statement revised.

## The file contract

Every session must, before it can end:

1. fold its results into `progress/math.tex`;
2. update `progress/state.json` honestly;
3. write `progress/_session_notes.md` — its account of the session.

A `Stop` hook checks all three and refuses to let the session finish otherwise
(it relents after two attempts and records the gap, so a session can never wedge).

**The agent never writes the lab book.** It writes the narrative; the app wraps
it in a header carrying *measured* tokens, cost, duration and turn counts. Models
are unreliable narrators about their own resource use, and a lab book with
invented numbers is worse than none.

## Harnesses

A harness is a directory under `harnesses/`:

```
harnesses/explore-verify/
├── harness.toml     model/effort defaults, budgets, tools, permissions, context
├── SKILL.md         the method — injected into the system prompt, always on
├── agents/*.md      optional subagents (passed via --agents, session-scoped)
├── skills/          optional on-demand skills (loaded as a session plugin)
└── orchestrate.py   only for runner = "script"
```

A manifest may also declare simple reader or method variants. Each variant
appears as its own choice while reusing the same roles and pipeline, and may
select a different skill file such as `SKILL-expert.md`.

Shipped:

| id | What it is |
|---|---|
| `chat` | One turn, zero tools, no filesystem. The "asked it in the chat window" control. |
| `solo` | One agent, full file access, the contract, no methodology. |
| `solo-deep` | One agent at max effort with a plan → attack → audit cadence. |
| `explore-verify` | Free exploration, then every claim faces a hostile referee before it may enter `math.tex`. |
| `deslopify-expert` | **Reads** a document for a specialist or nearby expert: builds a dense guide and argument map, and never edits the text. |
| `deslopify-nonexpert` | Reads the same way for a PhD mathematician outside the field, at an advanced-Wikipedia level. |

Two baselines rather than one is deliberate: `solo − chat` measures the value of
the agentic shell, `harness − solo` measures the value of the method.

### Harnesses that read rather than solve

A harness declares `kind = "solve"` (the default) or `kind = "read"`, and the two
owe different things. A solving session must advance `progress/math.tex`; a
reading session must leave it **byte-for-byte unchanged**, which a `Stop` hook
verifies by hash rather than by asking. A reading session also receives
`shared/policy/fidelity.md` in place of `rigor` — rigor governs promoting a claim
to established, which a reader never does.

`deslopify` is the first of these. It lives in `deslopify/` with its own README,
design notes and tests; the harness directory is symlinked into `harnesses/` so
Atelier discovers it. Start at [deslopify/README.md](deslopify/README.md).

If you only want a paper explained, you do not need the lab at all. Deslopify
has its own reading page, **unpacman**: run `uv run python -m deslopify.app`,
drag a PDF onto it, choose **Expert** or **Non-expert**, check the AI and its
limits just below, and press **Start**. The companion can be read alone, beside
the paper, or the paper alone; anything unfamiliar in either can be selected and
right-clicked to have it explained or to ask about it; and the method the AI
follows can be read and changed in its Advanced settings. What it does and how
to use it is in [deslopify/README.md](deslopify/README.md), under *The reader*.

### Sessions that review their own work

A harness may declare a **review loop**: the first session drafts, a second one
reads what it produced against the source, and a third answers what the second
found. Each is a separate program with its own memory of the conversation, so the
reviewer cannot see how the drafter arrived at anything — which is the point,
because a reviewer who can is checking a piece of work for internal consistency
rather than checking it against the evidence.

The reviewer is deliberately **not** asked whether the writing is clear. Text
written by an AI is already clear; that is what it is good at. Asking for more of
it gets you a smoother document that says the same things, and now it looks as
though somebody checked. So the reviewer is asked something narrower and
answerable: where does this companion say something the source does not? Every
point it raises has to name a place — a step on the map, a heading, a line of the
document — and quote the source where it is making a claim about it. A point that
cannot be located cannot be checked, cannot be argued with, and cannot be shown to
have been dealt with.

Every point raised must then be answered: **fixed**, saying where the change is,
or **rejected**, saying why. Disagreeing with the reviewer is free and is often
right. Saying nothing is not allowed, because that is how an inconvenient point
quietly disappears.

You choose how many rounds when you start a session (`Rounds of review` in the
dialog, or `--rounds`). Two is the default and costs roughly three times a single
reading, so the budget you set is divided between the passes; one round costs
less, and zero gives you the plain single reading. A round that finds nothing
worth acting on ends the loop there. Afterwards, `progress/reading/review-log.md`
records everything the reviewer raised and what became of it — including what was
refused, and why.

Runs are **hermetic** (`--setting-sources ''`), so a session never inherits your
personal `~/.claude` settings or `CLAUDE.md`. Without that, comparing two
harnesses proves nothing. Verified empirically on 2026-08-15: a `CLAUDE.md`
planted in a problem folder did not reach the session, and its marker appears
nowhere in the compiled artefacts.

## Two providers, and where they genuinely differ

Atelier drives Claude Code and Codex. They are not interchangeable, and pretending
they were would make every comparison between them meaningless. The differences
below were found by probing codex-cli 0.147.0, not by reading its documentation.

| | Claude Code | Codex |
|---|---|---|
| Mid-session steering | yes, lands at the next turn | **no** — `exec` is one turn with no input channel |
| A genuinely tool-free run | yes (`--tools ""`) | **no** — the weakest sandbox still reads files |
| Harness method delivered as | a system prompt | folded into the user prompt |
| The file contract | enforced by a hook that refuses to stop | checked afterwards and recorded |
| Subagents | yes | no |
| Cost | measured per token | not billed per token on a subscription |
| Resume | yes | yes |

Three of these change what a result *means*, so Atelier states them rather than
smoothing them over:

- **The `chat` control is weaker on Codex.** Claude can be given no tools at all.
  Codex's least-powerful setting still permits reading files and running
  commands, so its `chat` session is *asked* to use none and the run records how
  many it actually used. A session that used any is flagged as no longer a
  baseline.
- **The method arrives in a different place.** Codex has no system-prompt flag —
  `experimental_instructions_file` is accepted and then ignored — so the harness
  text is folded into the prompt. Same words, different position.
- **`explore-verify` is Claude-only**, because it depends on a separate referee
  agent. The same name over a different method would be worse than not offering it.

Two things are handled for you. Codex reads `$CODEX_HOME/AGENTS.md` even under
`--ignore-user-config`, so every session runs against a sanitised home holding
only a link to your credentials — otherwise personal standing instructions would
silently apply to every run. And `codex exec` blocks forever unless its input is
closed, so Atelier closes it.

## Session modes, and where results land

| Mode | Sees prior progress | Writes to | Lock |
|---|---|---|---|
| `standard` | yes — state, lab book, `math.tex` | problem folder for solving; per-run workspace for reading | exclusive for solving; reading is parallel-safe |
| `continue` | yes, and resumes the previous conversation | problem folder for solving; per-run workspace for reading | exclusive for solving; reading is parallel-safe |
| `oneshot` | **no** — static files only | `runs/<id>/workspace/` | none; parallel-safe |

A reading harness such as Deslopify is a deliberate exception to the normal
standard-session lock. At launch Atelier snapshots the current problem into
`runs/<id>/workspace/`; the run writes only there, and its final artifacts are
archived under `runs/<id>/reading/`. This makes expert/non-expert runs, different
models, or repeated runs on the same paper safe to run simultaneously. A
proof-writing harness still takes the exclusive problem lock because two agents
editing the same `math.tex`, `state.json`, and lab book would conflict.

A one-shot session is a **blind control**. It runs in an isolated copy, and
`additionalDirectories` is forced empty so it cannot reach the real problem
folder — a leak there would let a "blind" arm read `progress/` and silently
invalidate every comparison. A test asserts no prior work can reach the
workspace.

Its results are therefore surfaced deliberately rather than written into the
problem:

- `runs/<id>/answer.md` — the final text. Written **even when the session
  produced none**, with the token count, duration and cost, because a missing
  file looks like an app bug while an empty answer with a reason is a finding.
- `runs/<id>/math.pdf` — compiled from the workspace document when the harness
  had tools. Embedded in the run view under **Document**.
- `runs/<id>/answer.pdf` — the answer itself, typeset by the app, when there is
  no document to compile. Embedded in the same place.
- `runs/<id>/math.tex`, `state.json`, `_session_notes.md` — copied alongside.

Where to find these in the interface, and why they are kept apart:

| Tab | Shows | Kept how |
|---|---|---|
| **PDF** | the problem's own document, from `progress/math.tex` | one living file, holding the work of every standard session |
| **Sessions** | one document per one-shot or isolated reading session | one file per session, never merged or overwritten |

A standard solving session never appears under Sessions with a document of its
own: its work is in the problem's PDF, which is where standard solving work
belongs. Isolated reading sessions do appear there with their own companion.
A method without file access is no exception — Atelier writes on its behalf,
marking the section with the session it came from so a reader can tell curated
mathematics from text the app inserted.

The separation is the point. A one-shot session is an independent blind attempt:
several of them on the same problem are several *different* answers, and the
interesting question is how they differ. Collapsing them into one, or promoting
the newest into the PDF tab, would destroy exactly the comparison they exist to
support — and would make a blind attempt look like the problem's established
state. So each keeps its own PDF in its own run directory, and the PDF tab shows
only what standard sessions have actually established.

## Every session yields a PDF

A tool-less harness has no filesystem and so can never write `math.tex`. Its
mathematics still belongs in a PDF, so the app converts the answer itself
(`src/atelier/mdtex.py`) once the session has ended, and compiles it with the
problem's own preamble. The model gains no capability from this — the conversion
happens after the fact, from text it had already emitted — so `chat` remains a
true zero-tool control.

Two details matter in practice, and both are covered by tests:

- **Math is never touched.** `$…$`, `$$…$$`, `\[…\]` and fenced code are lifted
  out before any Markdown rewriting and put back last, because `_`, `*` and `\`
  mean different things in TeX and in Markdown. Dollar spans follow the same
  rule the web view's dollarmath uses, so "costs $5, but $x$ is math" parses the
  way a reader would.
- **Unicode cannot kill the compile, and cannot quietly corrupt the result.**
  pdflatex with `utf8` inputenc dies on any character outside Latin-1, and a
  single ✓ in a summary table is enough to lose the whole PDF. Symbols models
  actually emit — ✓ ✗ → ≤ ∀ ℝ and the Greek alphabet — map to `\ensuremath`
  macros; sub- and superscripts (`k₁`, `x²`) come from Unicode's own
  decomposition data rather than a hand-written list, because enumerating them
  by hand misses most of them. Anything still unknown becomes a **visible
  `[U+XXXX]` marker**, never a `?`: in mathematics a stray `?` reads as a
  legitimate symbol, so `k₁` arriving as `k?` is a wrong document that looks
  right. The run also records a warning naming the characters, and `answer.md`
  always holds the original.

If LaTeX is unavailable the run says so and the answer is still there as
Markdown; nothing is lost, and the run view falls back to an **Answer** card.

## Session control

| Action | Mechanism | Semantics |
|---|---|---|
| **Wrap up** | steering message | Graceful. The preferred stop. |
| **Freeze / Thaw** | `SIGSTOP` / `SIGCONT` to the process group | A true pause. Safe for short holds; an in-flight HTTPS request can time out past ~60s, and the UI warns. |
| **Checkpoint & stop** | `SIGINT` → `SIGTERM` → `SIGKILL` | Ends the process; the conversation stays resumable by session id. |
| **Abandon** | `SIGKILL` + `git checkout` | Reverts everything the session wrote. |

Steering works mid-run: messages are written to the provider's stdin and land at
the next turn boundary, with delivery confirmed by the provider's echo.

## Budgets

Set them per session in the **Budget** row of the New Session dialog (Max $ /
Max minutes / Max output tokens, pre-filled from the harness, empty means no
cap), or with `--max-usd` / `--max-minutes` on the CLI, or permanently in
`[budget]` in the harness's `harness.toml`.

Four caps — USD, wall-clock, output tokens, turns — and whichever is closest to
its limit is the binding one. At 80% (configurable per harness) the app injects a
wrap-up message with the real numbers; at 100% plus a grace period it stops the
session. The provider's own `--max-budget-usd` is passed as an independent
backstop at 2× (minimum +$1), far enough above ours that our graceful wrap-up
always fires first. Too tight a margin and the CLI kills the session mid-wrap-up,
losing the record it was in the middle of writing.

A caveat worth knowing: a tool-less harness at high effort can spend its entire
output ceiling on reasoning and return nothing, and steering cannot rescue it
because steering only lands at a turn boundary. The run view says so explicitly
when it happens.

## Cost and credits

Cost is computed from observed token counts against a date-versioned price table
(`pricing.toml`), so historical runs stay costed at the rate in force when they
ran. Both our figure and the provider's are stored, and a divergence over 10% is
flagged — that is how a stale price table gets noticed.

**No public API exposes "credits remaining" for a subscription plan.** The
accounts view shows four signals separately, with their provenance:

1. **measured** — exact spend from token counts we observed;
2. **calibrated** — rolling 5h/7d utilisation against caps you set in `accounts.toml`;
3. **reported** — the provider's own `rate_limit_event` (status, window, reset time),
   captured whenever the CLI volunteers it;
4. **observed** — actual rate-limit events and the cooldown they implied.

## Multiple accounts

Each account gets its own credentials directory:

```bash
mkdir -p ~/.atelier/accounts/max-primary
CLAUDE_CONFIG_DIR=~/.atelier/accounts/max-primary claude auth login
```

then an `[[account]]` block in `accounts.toml` pointing `config_dir` at it. No
secret ever goes in that file: API keys are referenced by keychain item,
environment variable or file name (`key_ref = "keychain:…" | "env:…" |
"file:…"`).

**Open-weight models** run through Claude Code pointed at another host that
speaks Anthropic's API — Ollama on this computer, OpenRouter, or DeepSeek's own
service — as provider `open` (`src/atelier/providers/openweight.py`). The
reader always has three such accounts; for the lab, declare one:

```toml
[[account]]
id       = "ollama"
provider = "open"
auth     = "local"
base_url = "http://127.0.0.1:11434"
```

(`auth = "api_key"` with a `key_ref` for a hosted one, e.g.
`base_url = "https://api.deepseek.com/anthropic"` for DeepSeek). Web search is
not available through Ollama, and not known to work through OpenRouter (not
yet tried with a key) — DeepSeek runs it itself — and the CLI's own cost
figure is ignored: a hosted model is priced from `pricing.toml`
(or, in the reader, from its host's published rates), a local one is held to
time. A harness that takes Claude takes them too.

**Paper readings are sandboxed** (`src/atelier/access.py`,
`docs/unpacman-access-policy.md`): a reading's commands may read only its own
folders and installed tools, and write only its workspace; Codex must be recent
enough to understand every setting of that policy, or it refuses to start, and
installed whole: it runs every command through `codex-code-mode-host`, beside
its own program. The reader's Getting ready checks both, and *Update Codex* runs
Codex's own installer.

A looped run may also give a pass to a different AI: `RunSpec.role_agents`
maps a role (`draft`, or one of the loop's) to a provider, account, model and
effort; each pass is compiled for its own CLI and priced at its own model.

## Layout

```
atelier/
├── atelier.toml   accounts.toml   pricing.toml
├── harnesses/     the method library
├── shared/        policies and skills harnesses import
├── runs/          one directory per session: events.jsonl, prompt, argv, usage, result
├── src/atelier/   the application
├── web/           templates, css, js, vendored KaTeX (no build step)
└── docs/PLAN.md   the design document this was built from
```

`runs/` on disk is the source of truth; `var/atelier.db` is a queryable cache you
can delete and rebuild with `atelier reindex`.

## Tests

```bash
uv run pytest -q
```

1,330 tests, no provider API calls, about three minutes. Twenty-one are
skipped unless asked for: five page tests need Node on PATH, fourteen live sandbox
tests need `UNPACMAN_SANDBOX_TEST=1` outside any sandbox (on a Mac, or on Linux
under WSL — three of them on a Mac only), one public HTTPS request needs
`UNPACMAN_NETWORK_TEST=1`, and one needs Linux's own `/proc`. They cover pricing, problem and
file classification, locking, harness parsing, prompt compilation, the lab book,
LaTeX error extraction, Markdown→LaTeX conversion, the event log and hub, the
Claude stream-json parser, the argument graph and its checks, and the review loop
— its pass planning, budget division, lane enforcement and convergence
measurement — and the Deslopify reader: uploads refused and accepted, papers
named from their own text, the map's layout, the review said in plain
language, and the choice of AI — only what can run here is offered, and the
cap the page states is the cap the reading gets — and explanations on demand:
what the AI is shown, how it is called (with a stand-in for each CLI), and
that an answer is kept, merged and never asked for twice — and setting it up:
what Getting ready reports and installs (with stand-ins for every program and
download), the reading refused before anything is spent when a sign-in or
LaTeX is missing, updates (against throwaway repositories, including going
back when a new version cannot start), the icon, packages and the installer
itself (run for real on throwaway repositories), and reading a PDF with no
PDF tools installed.

Several exist because of bugs that only appeared against the real CLI or a real
browser, and would silently return if the code were refactored:

- per-message usage is **repeated** across streamed blocks and must be
  deduplicated by `message.id`, or every total double-counts;
- `rate_limit_event` is a first-class credits signal, not noise;
- 5-minute and 1-hour cache writes bill at different multipliers;
- one-shot must never widen filesystem access, and its hooks must target the
  workspace rather than the problem folder;
- the provider's own budget cap must leave room for our wrap-up;
- appending to a closed event log must not raise (steering a finished run);
- Markdown→LaTeX must leave mathematics byte-for-byte intact, and must not lose
  a document to one Unicode glyph;
- a reading harness must never be handed the solving contract, and support in an
  argument graph is conjunctive rather than reachability — a proof step needs
  every premise, so "which step fails without A?" is answerable.

For frontend changes there is no Node on this machine — see **Development
notes** below.

## Development notes

**Editing the frontend.** App code is served from `/asset/<stamp>/…` where the
stamp is the newest mtime under `web/js` and `web/css`. ES modules import each
other by relative path, so versioning only the entry point is not enough — a
fresh `app.js` would still pull a cached `launch.js`. Putting the whole tree
under one stamped prefix changes every module URL together, and the shell HTML is
`no-store` so it always carries the current stamp. A plain reload is sufficient;
no hard refresh, no server restart.

**Verifying the frontend.** There is no Node here. Syntax-check the modules with
JavaScriptCore and render the pages with a real WKWebView, both through
`osascript -l JavaScript`. This is not a fallback — it caught four real bugs that
reading the code did not. The app also POSTs client-side errors to
`/api/client-error`, so runtime failures appear in the server log.

**Editing harnesses.** `harness.toml` and its selected skill file are read at launch, so
changes apply to the next session with no restart. `atelier preview --problem P
--harness H` prints the exact compiled prompt and spends nothing.

## Known caveats

- **Live token counts are provisional.** Per-message `output_tokens` is a partial
  snapshot; only the terminal `result` carries the true total, which is
  reconciled at the end. Cost is barely affected — cache tokens dominate.
- **Fable 5 pricing is unsettled.** `pricing.toml` says $10/$50 per MTok; the CLI
  reported an effective output rate near $25/MTok on a real run. Haiku and Opus
  agree within 10–20%. Prefer a time cap over a dollar cap on Fable until this is
  resolved; the >10% divergence warning is what surfaced it.
- **Budget defaults are judgement, not calibration.** They predate any Opus data.
  *Accounts → Spend by harness* accumulates the real medians.
- **Codex models have no price table.** On a ChatGPT subscription tokens are not
  billed per token, so no honest dollar figure exists. Atelier says "not billed
  per token" rather than "$0.00", and a dollar budget on an unpriced model is
  replaced by a wall-clock cap — a cap that cannot fire is worse than none.
- **Codex sessions cannot be steered.** Stop and resume instead.

## Requirements

Python 3.13, `git`, a TeX distribution providing `latexmk`, and at least one of
`claude` (Claude Code 2.1+) or `codex` (codex-cli 0.147+).
