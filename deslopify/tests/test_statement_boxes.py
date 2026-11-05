"""A boxed statement holds the whole statement (2026-10-23).

A tester reported boxes in the companion PDF that stopped short of their
statement. The box ended after the statement's paragraph and the displays
right after it, so a sentence a display interrupted ("Then $$…$$ for every
prime $p$."), a list the statement introduced, or more of its italic text was
left outside. These pin what now goes inside, and what still stays out; the
page's boxes (app.js `markStatements`) follow the same rules.
"""

from __future__ import annotations

import re

import pytest

from deslopify.summary_pdf import _companion_document


def boxes(markdown: str) -> list[str]:
    _, latex = _companion_document(markdown, audience="expert")
    return re.findall(r"\\begin\{statement\w+\}\n(.*?)\n\\end\{statement\w+\}", latex, re.S)


def after_last_box(markdown: str) -> str:
    _, latex = _companion_document(markdown, audience="expert")
    return latex[latex.rindex(r"\end{statement"):]


@pytest.mark.parametrize("markdown, rest", [
    ("**Theorem (paper, Theorem 1).** *Let $n \\ge 3$. Then*\n$$\nf(n) = 0\n$$\n"
     "*for every prime $p$.*\n", r"\emph{for every prime $p$.}"),
    ("**Theorem (paper, Theorem 1).** *Let $n \\ge 3$. Then*\n\n$$\nf(n) = 0\n$$\n\n"
     "*for every prime $p$.*\n", r"\emph{for every prime $p$.}"),
    ("**Theorem (paper, Theorem 6).** *Let $n\\ge 1$. Then*\n$$\n\\sum_k a_k < \\infty,\n$$\n"
     "where $a_k$ is the $k$-th coefficient.\n", "where $a_k$ is the $k$-th coefficient."),
    ("**Theorem (paper, Theorem 2).** *The following are equivalent:*\n\n"
     "1. *$X$ is compact;*\n2. *$X$ is sequentially compact.*\n", r"\end{enumerate}"),
    ("**Theorem (paper, Theorem 3).** *Let $X$ be compact. Then $A$ holds.*\n\n"
     "*Moreover, if $Y$ is open, then $B$ holds.*\n", r"\emph{Moreover, if $Y$ is open"),
    ("**Proposition (paper, Proposition 4).** *For the set $\\{x\\}$ and all $t$:*\n\n"
     "*the set $\\{x\\}$ is closed.*\n", r"\emph{the set $\{x\}$ is closed.}"),
    ("**Proposition (paper, Proposition 5).** *Write the sets as follows:*\n\n"
     "*The left brace $\\{$ opens each of them.*\n", r"\emph{The left brace $\{$ opens"),
    ("**Theorem (paper, Theorem 11).** *Let $n \\ge 3$. Then*\n$$\nf(n) = 0\n$$\n"
     "*for every prime* $p$.\n", r"\emph{for every prime} $p$."),
], ids=["display-then-italic", "display-then-italic-blank-lines", "display-then-where",
        "list-after-colon", "second-italic-paragraph", "escaped-braces", "a-lone-escaped-brace",
        "partly-italic"])
def test_what_goes_on_with_a_statement_is_inside_its_box(markdown, rest):
    found = boxes(markdown)
    assert len(found) == 1
    assert rest in found[0]


def test_a_statement_continued_after_two_displays_as_a_reading_wrote_it():
    """The one stored companion the old rule had cut (a reading of 2026-10-04)."""
    markdown = (
        "**Theorem (The two conservation laws; paper, section 5).** *For a symmetric "
        "Dyck path encoded by a Dyck prefix $u$, the following identities hold:*\n"
        "$$\\text{bounce}(\\zeta(u)) = A(u),$$\n"
        "$$A(\\zeta(u)) = \\text{dinv}(u).$$\n"
        "*The square map $\\Phi = \\zeta^2$ is thus a bijection on symmetric Dyck paths "
        "satisfying*\n"
        "$$\\text{bounce}(\\Phi(P)) = \\text{dinv}(P).$$\n\n"
        "The paper constructs three objects:\n\n"
        "1. **The folded zeta map** on Dyck prefixes.\n")
    found = boxes(markdown)
    assert len(found) == 1
    assert r"\text{bounce}(\Phi(P)) = \text{dinv}(P)." in found[0]
    rest = after_last_box(markdown)
    assert "The paper constructs three objects:" in rest
    assert r"\begin{enumerate}" in rest


@pytest.mark.parametrize("markdown, outside", [
    ("**Theorem (paper, Theorem 4).** *Then*\n$$\nf = 0.\n$$\n"
     "This shows that the bound is sharp.\n", "This shows that the bound is sharp."),
    ("**Theorem (paper, Theorem 5).** *Every $x$ is even.*\n\n"
     "The proof is a parity count.\n", "The proof is a parity count."),
    ("**Theorem (paper, Theorem 7).** *Every group here has these properties.*\n\n"
     "1. It is finite.\n2. It is abelian.\n", r"\begin{enumerate}"),
    ("**Theorem (paper, Theorem 8).** *Then*\n$$\nf = 0\n$$\n*for all $n$.*\n\n"
     "$$\ng = 1\n$$\n", "g = 1"),
    ("**Definition.** *A term is admissible:*\n"
     "**Source correspondence.** This is Definition 1 in the paper.\n",
     r"\textbf{Source correspondence.}"),
    ("**Theorem (paper, Theorem 9).** *Then:*\n\n"
     "Here the constant is explicit.\n", "Here the constant is explicit."),
    ("**Theorem (paper, Theorem 10).** *Then*\n\n"
     "$a_k$ denotes the coefficient.\n", "$a_k$ denotes the coefficient."),
    ("**Theorem (paper, Theorem 12).** *Then*\n$$\nf = 0\n$$\n*for all $n \\ge 1.$*\n\n"
     "$$\ng = 1\n$$\n", "g = 1"),
    ("**Theorem (paper, Theorem 13).** *Then*\n$$\nf = 0\n$$\n*for all $n$, and*\n"
     "$$\ng = 1.\n$$\n\n$$\nh = 2\n$$\n", "h = 2"),
    ("**Theorem (paper, Theorem 14).** *The following are equivalent:*\n\n"
     "1. *$X$ is compact;*\n2. *$X$ is sequentially compact.*\n\n$$\nh = 2\n$$\n", "h = 2"),
], ids=["upright-prose-after-display", "upright-prose-after-statement",
        "list-after-a-complete-sentence", "display-after-a-finished-sentence",
        "source-note", "upright-sentence-after-colon", "formula-first",
        "display-after-a-sentence-ending-in-its-formula",
        "display-after-a-display-that-ended-it", "display-after-its-list"])
def test_what_does_not_go_on_with_it_stays_outside(markdown, outside):
    assert len(boxes(markdown)) == 1
    assert outside in after_last_box(markdown)


def test_a_box_never_takes_in_the_next_statement():
    found = boxes("**Theorem (paper, Theorem 1).** *The following hold:*\n\n"
                  "**Lemma (paper, Lemma 2).** *Every $x$ is even.*\n\n"
                  "*for all $x$.*\n")
    assert len(found) == 2
    assert "Lemma" not in found[0]
    assert r"\emph{for all $x$.}" in found[1]
