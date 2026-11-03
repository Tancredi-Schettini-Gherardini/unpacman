"""Hearing about new versions: an email address, given once, for that alone (news.py).

Nothing is sent anywhere: the sign-up form is stood in for, and the email
route only makes a link for the reader's own email program.
"""

from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from deslopify.app import news, ready
from test_app import app_cfg, client  # noqa: F401  (fixtures)


def test_an_address_is_suggested_from_a_sign_in_never_sent(client):
    st = client.app.state.deslopify
    client.ready.signed_in = {"claude"}
    original = client.ready.get

    def with_who(*, fresh=False):
        r = original(fresh=fresh)
        for ai in r.ais:
            if ai.provider == "claude":
                ai.who = "reader@example.org"
        return r
    client.ready.get = with_who
    got = client.get("/api/news").json()
    assert got["asked"] is False and got["suggestion"] == "reader@example.org"
    assert got["from"] == "your Claude sign-in" and got["email"] is None
    assert not news.store(st.cfg).exists()          # looking stored nothing


def test_the_email_route_makes_a_two_line_message_to_send(client):
    st = client.app.state.deslopify
    r = client.post("/api/news", json={"email": "  reader@example.org "})
    out = r.json()
    assert r.status_code == 200 and out["via"] == "email" and out["email"] == "reader@example.org"
    link = urlsplit(out["link"])
    assert link.scheme == "mailto" and unquote(link.path) == client.get("/api/feedback").json()["to"]
    query = parse_qs(link.query)
    assert query["subject"] == ["unpacman: keep me posted"]
    assert "Please email me at reader@example.org" in query["body"][0]
    kept = json.loads(news.store(st.cfg).read_text())
    assert kept["answer"] == "yes" and kept["email"] == "reader@example.org"
    again = client.get("/api/news").json()
    assert again["asked"] is True and again["email"] == "reader@example.org"


def test_no_thanks_is_kept_and_not_asked_again(client):
    assert client.post("/api/news", json={"answer": "no"}).json() == {"answer": "no"}
    got = client.get("/api/news").json()
    assert got["asked"] is True and got["answer"] == "no" and got["email"] is None


@pytest.mark.parametrize("bad", ["", "reader", "reader@", "@example.org", "a b@example.org"])
def test_what_is_not_an_address_is_refused(client, bad):
    r = client.post("/api/news", json={"email": bad})
    assert r.status_code == 400 and "email address" in r.json()["detail"]
    assert client.get("/api/news").json()["asked"] is False


def test_a_sign_up_form_is_posted_to_when_there_is_one(app_cfg, monkeypatch):
    sent = []

    class Answer:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(request, timeout):
        sent.append((request.full_url, request.data.decode()))
        return Answer()
    monkeypatch.setattr(news.urllib.request, "urlopen", urlopen)
    form = {"url": "https://docs.google.com/forms/d/e/X/formResponse",
            "email": "entry.1", "version": "entry.2"}
    out = news.answer(app_cfg, email="reader@example.org", to="us@example.org",
                      version="abc1234", form=form)
    assert out == {"answer": "yes", "email": "reader@example.org", "via": "form"}
    [(url, data)] = sent
    assert url == form["url"] and parse_qs(data) == {"entry.1": ["reader@example.org"],
                                                     "entry.2": ["abc1234"]}
    news.stop(app_cfg, to="us@example.org", version="abc1234", form=form)
    assert parse_qs(sent[-1][1])["entry.2"] == ["stop abc1234"]
    assert news.load(app_cfg)["answer"] == "no"


def test_taking_the_address_back(client):
    client.post("/api/news", json={"email": "reader@example.org"})
    out = client.delete("/api/news").json()
    assert out["answer"] == "no" and "stop" in unquote(out["link"])
    assert client.get("/api/news").json()["email"] is None


def test_chatgpts_sign_in_names_its_owner(tmp_path):
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    token = f"{b64({'alg': 'none'})}.{b64({'email': 'reader@example.org'})}.sig"
    (tmp_path / "auth.json").write_text(json.dumps({"tokens": {"id_token": token}}))
    assert news.codex_email(tmp_path) == "reader@example.org"
    (tmp_path / "auth.json").write_text("{}")
    assert news.codex_email(tmp_path) == ""
    r = ready.Readiness(ais=[], tex=ready.Tex(), pdf="", checked=0)
    assert news.suggestion(r) == ("", "")
    # The ChatGPT account unpacman reads with — its own, it may be (signins.py).
    own = ready.SignIn(account="codex", provider="codex", installed=True, signed_in=True,
                       who="own@example.org", own=True)
    r = ready.Readiness(ais=[own], tex=ready.Tex(), pdf="", checked=0)
    assert news.suggestion(r) == ("own@example.org", "your ChatGPT sign-in")
