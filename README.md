# unpacman

unpacman's purpose is to help dealing with the increasingly vast mathematical literature, in a way that tries to prioritise human understanding. unpacman is an interface for asking questions about math papers, and for generating structured summaries.

unpacman includes:

* a multi-agent harness which takes in one or multiple PDFs and returns a structured summary according to the criteria above. This harness can operate in expert mode and in non-expert mode, yielding two different levels of technical details. Each piece of the harness can be inspected and modified.
* a feature for asking further questions about the original PDF or the generated summary. This can be done by highlighting excerpt which are unclear.
* a feature for literature search.

Each feature in the harness can be simply reproduced using an out-of-the-box LLM with some prompt engineering; the point of Unpacman is to give a common home to all functionalities that a mathematician would find useful. We made the interface as user-friendly as possible, and structured the harness so that it can be fully customizable by clicking on "Advanced settings". It offers various reader-friendly features depending on the user’s request, and its internal skill files are completely customisable. It can run with Codex, Claude Code, DeepSeek and any model available through OpenRouter.

We stress that Unpacman is a just a tool! It is not a replacement for the process of assimilating mathematical knowledge, but a useful instrument that helps you focus on the content that really matters.

## Set it up

**On a Mac** (macOS 12 or later), **the quickest and most effective way** is one
line in Terminal, which downloads unpacman directly from this repository. Open
Terminal (press ⌘ Space, type `Terminal`, press Return), paste this line and
press Return:

```
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Tancredi-Schettini-Gherardini/unpacman/mac/install-unpacman.sh)"
```

**For a more traditional installation**, download
[unpacman-mac.zip](https://github.com/Tancredi-Schettini-Gherardini/unpacman/releases/latest/download/unpacman-mac.zip)
and open it, so that the folder `unpacman-mac` is in Downloads. Open Terminal,
type

```
bash ~/Downloads/unpacman-mac/install-unpacman.sh
```

and press Return.

Either way, when it says *Done*, unpacman opens in your browser: install the AI
you have a plan for and sign in to it, then LaTeX; one click each.

**On Windows** (10 or 11): download
[unpacman-windows.zip](https://github.com/Tancredi-Schettini-Gherardini/unpacman/releases/latest/download/unpacman-windows.zip),
unzip it (right-click, *Extract All…*), then double-click **Install unpacman.cmd**
in the extracted folder from your usual Windows account. Setup installs WSL
and Ubuntu if needed and asks separately for Windows administrator approval.
If it requests a restart, save your work and type **RESTART** in the setup
window; this also applies pending Windows updates. After restarting, open
**Install unpacman.cmd** again to continue. *READ ME FIRST.txt* has the full steps.

**On Linux** (Ubuntu, Debian, Mint, Pop!_OS, Fedora, Arch or openSUSE): open a
terminal, paste this line and press Enter:

```
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Tancredi-Schettini-Gherardini/unpacman/linux/install-unpacman-linux.sh 2>/dev/null || wget -qO- https://raw.githubusercontent.com/Tancredi-Schettini-Gherardini/unpacman/linux/install-unpacman-linux.sh)"
```

It asks for your password once, to install a few standard programs with your
package manager, then puts *Unpacman* in your applications menu and opens it in
your browser: install the AI you have a plan for and sign in to it, then LaTeX;
one click each. On Ubuntu 24.04 or later, the *Protection* step shows one more
line to type: it lets unpacman make the sandbox each reading runs in. On Ubuntu
22.04, read with Claude for now: ChatGPT's sandbox does not start there yet.

Each zip's *READ ME FIRST.txt* (Mac and Windows) says all of this in more detail.

## Updates

When a new version is ready, unpacman says so on its first page: press
*Update now*. Your papers and readings are kept.

## More

- The website: <https://tancredi-schettini-gherardini.github.io/unpacman/>
- The code: the `mac`, `windows` and `linux` branches of this repository.
- Questions and ideas: *Feedback*, at the top of unpacman, or
  unpacman.support@gmail.com.

unpacman is made by Antoine Song and Tancredi Schettini Gherardini, and is
free software under the MIT licence.

## Acknowledgements

We thank Finn Klein and Jonas Nehme for testing unpacman and for their useful
feedback.
