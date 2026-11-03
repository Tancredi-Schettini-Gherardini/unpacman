"""Public PDF downloads: VPN fake DNS, pinned sockets and redirect boundaries."""
import json
import socket
import ssl

import pytest

from deslopify.app import fetch


PUBLIC = "151.101.3.42"
PDF = b"%PDF-1.4\npublic test paper\n"


def dns(monkeypatch, *addresses):
    monkeypatch.setattr(fetch.socket, "getaddrinfo", lambda *args: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))
        for address in addresses])


class Response:
    def __init__(self, data=PDF, status=200, location=None):
        self.data, self.status, self.location = data, status, location
        self.reason = "Test response"

    def read(self, count):
        return self.data[:count]

    def getheader(self, key):
        return self.location if key == "Location" else None

    def close(self):
        pass


class Connection:
    def __init__(self, response):
        self.response, self.requests, self.closed = response, [], False

    def request(self, *args, **kwargs):
        self.requests.append((args, kwargs))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def connections(monkeypatch, *responses):
    opened = []
    queue = iter(responses)

    def connect(host, port, addresses, **kwargs):
        conn = Connection(next(queue))
        opened.append((host, port, addresses, kwargs, conn))
        return conn
    monkeypatch.setattr(fetch, "_connection", connect)
    return opened


@pytest.mark.parametrize("addresses", [
    ("198.18.0.77",),
    ("198.18.0.77", "::ffff:0:c612:4d"),  # Observed macOS/VPN answer.
    ("::ffff:198.18.0.77",),
    ("::ffff:0:198.18.0.77",),
    ("::ffff:0:c613:ffff", "::ffff:198.18.0.1"),
])
def test_vpn_synthetic_dns_is_replaced_by_public_addresses(monkeypatch, addresses):
    dns(monkeypatch, *addresses)
    asked = []
    monkeypatch.setattr(fetch, "_public_dns", lambda host: asked.append(host) or [PUBLIC])
    opened = connections(monkeypatch, Response())
    assert fetch.download_pdf("https://arxiv.org/abs/2610.12763") == PDF
    assert asked == ["arxiv.org"]
    assert opened[0][:3] == ("arxiv.org", 443, [PUBLIC])
    conn = opened[0][-1]
    assert conn.requests[0][0] == ("GET", "/pdf/2610.12763")
    assert conn.closed


@pytest.mark.parametrize("addresses", [
    (PUBLIC, PUBLIC),
    (PUBLIC, "2606:4700::1111"),
    ("2606:4700::c612:4d",),  # A matching tail outside the known wrapper.
])
def test_normal_public_dns_does_not_use_external_resolver(monkeypatch, addresses):
    dns(monkeypatch, *addresses)
    monkeypatch.setattr(fetch, "_public_dns", lambda _: pytest.fail("unexpected public DNS"))
    assert fetch._public_addresses("arxiv.org") == list(dict.fromkeys(addresses))


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1",
    "0.0.0.0", "224.0.0.1", "::1", "fc00::1", "fe80::1", "::ffff:127.0.0.1",
    "::ffff:0:127.0.0.1", "::ffff:0:10.0.0.1", "::ffff:0:169.254.169.254",
    "::ffff:0:c614:1",  # Translated IPv4 outside the synthetic VPN range.
    "::ffff:0:c612:4d%en0", "::ffff:198.18.0.77%en0",
])
def test_nonpublic_dns_is_never_reinterpreted_as_a_vpn(monkeypatch, address):
    dns(monkeypatch, address)
    monkeypatch.setattr(fetch, "_public_dns", lambda _: pytest.fail("private hostname leaked"))
    monkeypatch.setattr(fetch, "_connection", lambda *a, **k: pytest.fail("private connection"))
    with pytest.raises(fetch.FetchError, match="non-public"):
        fetch.download_pdf("https://private.example/paper.pdf")


@pytest.mark.parametrize("url", [
    "https://198.18.0.77/paper.pdf", "http://localhost/paper.pdf",
    "https://[::ffff:0:c612:4d]/paper.pdf",
    "https://[::ffff:198.18.0.77]/paper.pdf",
    "http://printer.local/paper.pdf", "file:///etc/passwd",
    "https://user:password@arxiv.org/paper.pdf", "https://arxiv.org:bad/paper.pdf",
])
def test_unsafe_urls_do_not_get_a_public_dns_fallback(monkeypatch, url):
    monkeypatch.setattr(fetch, "_public_dns", lambda _: pytest.fail("unexpected fallback"))
    monkeypatch.setattr(fetch, "_connection", lambda *a, **k: pytest.fail("unsafe connection"))
    with pytest.raises(fetch.FetchError):
        fetch.download_pdf(url)


@pytest.mark.parametrize("addresses", [
    (PUBLIC, "127.0.0.1"),
    ("198.18.0.77", "::ffff:0:c612:4d", "127.0.0.1"),
    ("::ffff:198.18.0.77", "::ffff:0:10.0.0.1"),
    (PUBLIC, "::ffff:0:c612:4d"),
])
def test_mixed_public_and_private_answer_is_rejected(monkeypatch, addresses):
    dns(monkeypatch, *addresses)
    monkeypatch.setattr(fetch, "_public_dns", lambda _: pytest.fail("private hostname leaked"))
    monkeypatch.setattr(fetch, "_connection", lambda *a, **k: pytest.fail("private connection"))
    with pytest.raises(fetch.FetchError, match="non-public"):
        fetch.download_pdf("https://arxiv.org/pdf/test")


@pytest.mark.parametrize("answer", [
    ["127.0.0.1"], ["198.18.0.77"], [],
    ["::ffff:0:c612:4d"], ["::ffff:198.18.0.77"],
    [PUBLIC, "::ffff:0:127.0.0.1"],
])
def test_public_dns_fallback_must_itself_return_public_ips(monkeypatch, answer):
    dns(monkeypatch, "198.18.0.77")
    monkeypatch.setattr(fetch, "_public_dns", lambda _: answer)
    with pytest.raises(fetch.FetchError, match="synthetic address.*public address could not"):
        fetch._public_addresses("arxiv.org")


def test_dns_failure_is_not_reported_as_a_private_address(monkeypatch):
    def fail(*args):
        raise socket.gaierror("test DNS failure")
    monkeypatch.setattr(fetch.socket, "getaddrinfo", fail)
    with pytest.raises(fetch.FetchError, match="DNS could not resolve arxiv.org"):
        fetch.download_pdf("https://arxiv.org/pdf/test")


def test_encrypted_resolver_uses_a_public_ip_and_only_the_hostname(monkeypatch):
    answer = {"Status": 0, "Answer": [{"type": 1, "data": PUBLIC}]}
    opened = connections(monkeypatch, Response(json.dumps(answer).encode()),
                         Response(b'{"Status": 0}'))
    assert fetch._public_dns("arxiv.org") == [PUBLIC]
    assert len(opened) == 2
    for row in opened:
        host, port, addresses, kwargs, conn = row
        assert (host, port, addresses) == ("1.1.1.1", 443, ["1.1.1.1"])
        assert kwargs["secure"] and kwargs["timeout"] == fetch.DNS_TIMEOUT_S
        args, sent = conn.requests[0]
        assert args[0] == "GET" and "name=arxiv.org&type=" in args[1]
        assert set(sent["headers"]) == {"Accept", "User-Agent"}
        assert conn.closed


@pytest.mark.parametrize("response", [
    Response(b"{}", status=302, location="http://127.0.0.1"),
    Response(b'{"Status": 3}'), Response(b'{"Status": 0, "TC": true}'),
    Response(b"not json"), Response(b'x' * (fetch.DNS_MAX_BYTES + 1)),
])
def test_public_dns_errors_fail_closed_with_actionable_message(monkeypatch, response):
    dns(monkeypatch, "198.18.0.77")
    opened = connections(monkeypatch, response)
    with pytest.raises(fetch.FetchError, match="synthetic address"):
        fetch.download_pdf("https://arxiv.org/pdf/test")
    assert opened[0][-1].closed


@pytest.mark.parametrize("target", [
    "http://127.0.0.1:7720/",
    "http://[::ffff:0:127.0.0.1]:7720/",
    "http://[::ffff:198.18.0.77]/paper.pdf",
])
def test_redirect_to_local_service_is_blocked_before_connecting(monkeypatch, target):
    dns(monkeypatch, PUBLIC)
    opened = connections(monkeypatch, Response(status=302, location=target))
    with pytest.raises(fetch.FetchError, match="non-public"):
        fetch.download_pdf("https://arxiv.org/pdf/test")
    assert len(opened) == 1 and opened[0][-1].closed


def test_public_redirect_is_checked_and_pdf_returned(monkeypatch):
    dns(monkeypatch, PUBLIC)
    opened = connections(monkeypatch, Response(status=302, location="https://papers.example/x.pdf"),
                         Response())
    assert fetch.download_pdf("https://arxiv.org/pdf/test") == PDF
    assert [row[0] for row in opened] == ["arxiv.org", "papers.example"]
    assert all(row[-1].closed for row in opened)


def test_redirect_loop_is_bounded(monkeypatch):
    dns(monkeypatch, PUBLIC)
    opened = connections(monkeypatch, *[
        Response(status=302, location="/again") for _ in range(fetch.MAX_REDIRECTS + 1)])
    with pytest.raises(fetch.FetchError, match="repeated redirect"):
        fetch.download_pdf("https://arxiv.org/pdf/test")
    assert len(opened) == fetch.MAX_REDIRECTS + 1
    assert all(row[-1].closed for row in opened)


@pytest.mark.parametrize("response, message", [
    (Response(b"HTML instead"), "did not lead to a PDF"),
    (Response(status=404), "site answered 404"),
])
def test_download_errors_remain_readable(monkeypatch, response, message):
    dns(monkeypatch, PUBLIC)
    connections(monkeypatch, response)
    with pytest.raises(fetch.FetchError, match=message):
        fetch.download_pdf("https://arxiv.org/pdf/test")


def test_oversized_pdf_is_rejected(monkeypatch):
    dns(monkeypatch, PUBLIC)
    monkeypatch.setattr(fetch, "MAX_BYTES", 10)
    connections(monkeypatch, Response(PDF))
    with pytest.raises(fetch.FetchError, match="larger than"):
        fetch.download_pdf("https://arxiv.org/pdf/test")


def test_socket_uses_validated_ip_but_tls_checks_original_hostname(monkeypatch):
    dialed, names = [], []

    class Socket:
        def settimeout(self, seconds):
            pass

        def connect(self, address):
            dialed.append(address)

        def close(self):
            pass

    class Context:
        def wrap_socket(self, sock, *, server_hostname):
            names.append(server_hostname)
            return sock

    monkeypatch.setattr(fetch.socket, "socket", lambda *args: Socket())
    monkeypatch.setattr(fetch.socket, "getaddrinfo", lambda *args: pytest.fail("second DNS lookup"))
    monkeypatch.setattr(fetch.ssl, "create_default_context", Context)
    conn = fetch._connection("arxiv.org", 443, [PUBLIC], secure=True)
    assert dialed == [(PUBLIC, 443)] and names == ["arxiv.org"]
    assert conn.host == "arxiv.org" and conn.auto_open == 0
    conn.close()


def test_invalid_tls_certificate_is_not_bypassed(monkeypatch):
    closed = []

    class Socket:
        def settimeout(self, seconds):
            pass

        def connect(self, address):
            pass

        def close(self):
            closed.append(True)

    class Context:
        def wrap_socket(self, sock, **kwargs):
            raise ssl.SSLCertVerificationError("untrusted test certificate")

    monkeypatch.setattr(fetch.socket, "socket", lambda *args: Socket())
    monkeypatch.setattr(fetch.ssl, "create_default_context", Context)
    with pytest.raises(fetch.FetchError, match="connect securely"):
        fetch._connection("arxiv.org", 443, [PUBLIC], secure=True)
    assert closed == [True]
