"""Fetching a paper a search found, so that it can be read here.

Only ever a PDF, only ever from the open internet: the address comes from what
a search wrote (`results.json`), and a search reads web pages, so a page could
have put any address there. One that names this computer or a private network
is refused before anything is sent; one that answers with something other than
a PDF is refused after.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import ssl
from urllib.parse import urlencode, urljoin, urlsplit

MAX_BYTES = 100 * 1024 * 1024
TIMEOUT_S = 60
USER_AGENT = "unpacman/1 (a reader for mathematics papers)"
MAX_REDIRECTS = 5
# Some VPNs resolve public domains to this benchmarking range, then route the
# synthetic address themselves. Never connect to it: ask public DNS instead.
VPN_FAKE_IP = ipaddress.ip_network("198.18.0.0/15")
# Some VPN DNS gateways also wrap IPv4 answers in this IPv6 prefix.
VPN_IPV4_TRANSLATED = ipaddress.ip_network("::ffff:0:0:0/96")
DNS_TIMEOUT_S = 10
DNS_MAX_BYTES = 64 * 1024


class FetchError(RuntimeError):
    """The paper could not be fetched, said so a reader can act on it."""


def pdf_address(url: str) -> str:
    """Where the PDF itself is: an arXiv abstract page names its PDF."""
    url = url.strip()
    m = re.match(r"^https?://(?:www\.)?arxiv\.org/abs/(.+?)/?$", url)
    if m:
        return f"https://arxiv.org/pdf/{m.group(1)}"
    return url


def _is_vpn_fake_ip(address: str) -> bool:
    """Recognize known synthetic DNS forms only; never authorize a connection."""
    if "%" in address:
        return False  # Scoped addresses still go through the strict rejection.
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in VPN_IPV4_TRANSLATED:
            ip = ipaddress.IPv4Address(ip.packed[-4:])
    return ip in VPN_FAKE_IP


def _checked_addresses(host: str, addresses: list[str]) -> list[str]:
    """Reject the whole answer if any address is non-public, not just RFC1918."""
    if not addresses:
        raise FetchError(f"No public address was found for {host}.")
    checked = []
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise FetchError(f"DNS returned an invalid address for {host}.") from exc
        if not ip.is_global or ip.is_multicast or ip.is_reserved or "%" in address:
            raise FetchError(f"{host} resolves to a non-public address. "
                             "Unpacman blocks local and private-network downloads.")
        checked.append(str(ip))
    return list(dict.fromkeys(checked))


def _connection(host: str, port: int, addresses: list[str], *, secure: bool,
                timeout: float = TIMEOUT_S) -> http.client.HTTPConnection:
    """Dial only checked numeric IPs; retain the original hostname for TLS/Host.

    No implicit second DNS lookup, environment proxy or automatic reconnect.
    The VPN may still carry the traffic; its settings are not changed.
    """
    checked = _checked_addresses(host, addresses)
    context = ssl.create_default_context() if secure else None
    last_error = None
    for address in checked:
        ip = ipaddress.ip_address(address)
        sock = socket.socket(socket.AF_INET6 if ip.version == 6 else socket.AF_INET,
                             socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout)
            sock.connect((address, port))
            if context is not None:
                sock = context.wrap_socket(sock, server_hostname=host)
            conn = http.client.HTTPConnection(host, port, timeout=timeout)
            conn.auto_open = 0
            conn.sock = sock
            return conn
        except OSError as exc:
            sock.close()
            last_error = exc
    raise FetchError(f"Could not connect securely to {host}. "
                     "Check your network/VPN, or save and upload the PDF manually.") from last_error


def _public_dns(host: str) -> list[str]:
    """Encrypted fallback for VPN fake-IP answers; never sends files or tokens.

    The resolver is reached at a fixed public address with certificate checking,
    so resolving the resolver cannot fall back into the VPN's synthetic DNS.
    No redirects or ambient proxy configuration are used for this request.
    """
    addresses = []
    for kind in (1, 28):  # A and AAAA, including IPv6-only publishers
        conn = _connection("1.1.1.1", 443, ["1.1.1.1"], secure=True,
                           timeout=DNS_TIMEOUT_S)
        response = None
        try:
            path = "/dns-query?" + urlencode({"name": host, "type": kind})
            conn.request("GET", path, headers={"Accept": "application/dns-json",
                                              "User-Agent": USER_AGENT})
            response = conn.getresponse()
            if response.status != 200:
                raise FetchError("The public DNS service did not answer successfully.")
            raw = response.read(DNS_MAX_BYTES + 1)
            if len(raw) > DNS_MAX_BYTES:
                raise FetchError("The public DNS response was too large.")
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("Status") != 0 or data.get("TC"):
                raise FetchError(f"Public DNS could not resolve {host}.")
            answers = data.get("Answer", [])
            if not isinstance(answers, list):
                raise FetchError("The public DNS response could not be read.")
            addresses.extend(row["data"] for row in answers
                             if isinstance(row, dict) and row.get("type") in (1, 28)
                             and isinstance(row.get("data"), str))
        finally:
            if response is not None:
                response.close()
            conn.close()
    return _checked_addresses(host, addresses)


def _public_addresses(host: str) -> list[str]:
    """Keep normal public DNS; resolve VPN fake IPs without allowing private IPs."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return _checked_addresses(host, [host])  # no fallback for literal private IPs
    if ("." not in host or host.endswith((".localhost", ".local", ".lan", ".internal", ".home"))
            or not re.fullmatch(r"[a-z0-9.-]+", host)):
        raise FetchError("Only public website addresses can be downloaded.")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise FetchError(f"DNS could not resolve {host}. Check your network/VPN, "
                         "or save and upload the PDF manually.") from exc
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    # Do not reinterpret arbitrary private answers as VPN addresses. Genuine
    # local/private destinations stay blocked, including mixed public/private DNS.
    # Decode known IPv6 wrappers only for this classification; the original
    # synthetic addresses are never returned or passed to a socket.
    if addresses and all(_is_vpn_fake_ip(a) for a in addresses):
        try:
            return _checked_addresses(host, _public_dns(host))
        except (FetchError, OSError, ValueError, http.client.HTTPException) as exc:
            raise FetchError(f"Your VPN/DNS supplied a synthetic address for {host}, "
                             "but a public address could not be obtained. "
                             "Open its PDF link in your browser and upload it manually.") from exc
    return _checked_addresses(host, addresses)


def download_pdf(url: str) -> bytes:
    """The PDF at `url`, or why not. Blocking; run it in a thread."""
    url = pdf_address(url)
    try:
        for hop in range(MAX_REDIRECTS + 1):
            parts = urlsplit(url)
            if (parts.scheme not in ("http", "https") or not parts.hostname
                    or parts.username is not None or parts.password is not None):
                raise FetchError("That is not a public web address.")
            host = parts.hostname.rstrip(".").encode("idna").decode("ascii").lower()
            port = parts.port or (443 if parts.scheme == "https" else 80)
            addresses = _public_addresses(host)
            conn = _connection(host, port, addresses, secure=parts.scheme == "https")
            response = None
            try:
                path = parts.path or "/"
                if parts.query:
                    path += "?" + parts.query
                conn.request("GET", path, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "application/pdf,*/*"})
                response = conn.getresponse()
                if response.status in (301, 302, 303, 307, 308):
                    location = response.getheader("Location")
                    if not location or hop == MAX_REDIRECTS:
                        raise FetchError("The PDF link has a missing or repeated redirect.")
                    url = urljoin(url, location)
                    continue  # every redirect gets a fresh public-address check
                if response.status != 200:
                    raise FetchError(f"The paper could not be fetched: the site answered "
                                     f"{response.status} ({response.reason}).")
                data = response.read(MAX_BYTES + 1)
                break
            finally:
                if response is not None:
                    response.close()
                conn.close()
    except FetchError:
        raise
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise FetchError(f"The paper could not be fetched ({exc}). Check the "
                         "internet connection, or open the link and save the PDF "
                         "yourself.") from exc
    if len(data) > MAX_BYTES:
        raise FetchError("That PDF is larger than 100 MB.")
    if not data[:1024].lstrip().startswith(b"%PDF-"):
        raise FetchError("The link did not lead to a PDF — the paper may be behind "
                         "a paywall. Open the link, save the PDF, and drop it here.")
    return data
