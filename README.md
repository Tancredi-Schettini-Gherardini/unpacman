# unpacman

unpacman's purpose is to help dealing with the increasingly vast mathematical literature, in a way that tries to prioritise human understanding. The main goal of unpacman is to generate structured summaries of one or multiple papers, which answer the standard questions that mathematicians would usually ask when reading a manuscript for the first time (main results, main ideas, the "why's", the "how's", historical context, etc.).

unpacman includes:

* a multi-agent harness which takes in one or multiple PDFs and returns a structured summary according to the criteria above. This harness can operate in expert mode and in non-expert mode, yielding two different levels of technical details. Each piece of the harness can be inspected and modified.
* a feature for asking further questions about the original PDF or the generated summary. This can be done by highlighting excerpt which are unclear.
* a feature for literature search.

Each feature in the harness can be simply reproduced using an out-of-the-box LLM with some prompt engineering; the point of Unpacman is to give a common home to all functionalities that a mathematician would find useful. We made the interface as user-friendly as possible, and structured the harness so that it can be fully customizable by clicking on "Advanced settings". It offers various reader-friendly features depending on the user’s request, and its internal skill files are completely customisable. It can run with Codex, Claude Code, DeepSeek and any model available through OpenRouter.

We stress that Unpacman is a just a tool! It is not a replacement for the process of assimilating mathematical knowledge, but a useful instrument that helps you focus on the content that really matters.

## Download

- **Mac** (macOS 12 or later): [unpacman-mac.zip](https://github.com/Tancredi-Schettini-Gherardini/unpacman/releases/latest/download/unpacman-mac.zip)
- **Windows** (10 or 11): [unpacman-windows.zip](https://github.com/Tancredi-Schettini-Gherardini/unpacman/releases/latest/download/unpacman-windows.zip)

## Set it up

**On a Mac**: open the download, so that the folder `unpacman-mac` is in
Downloads. Open Terminal (press ⌘ Space, type `Terminal`, press Return), type

    bash ~/Downloads/unpacman-mac/install-unpacman.sh

and press Return. When it says *Done*, unpacman opens in your browser: install
the AI you have a plan for and sign in to it, then LaTeX; one click each.

**On Windows**: unzip the download (right-click, *Extract All…*), open
*READ ME FIRST.txt* in the folder, and follow it. unpacman runs inside WSL,
Windows' own Linux, which it shows how to install once.

The folder's *READ ME FIRST.txt* says all of this in more detail.

## Updates

When a new version is ready, unpacman says so on its first page: press
*Update now*. Your papers and readings are kept.

## More

- The website: <https://tancredi-schettini-gherardini.github.io/unpacman/>
- The code: the `mac` and `windows` branches of this repository.
- Questions and ideas: *Feedback*, at the top of unpacman, or
  unpacman.support@gmail.com.

unpacman is made by Antoine Song and Tancredi Schettini Gherardini, and is
free software under the MIT licence.
