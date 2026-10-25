"""Bounded public HTTP with DNS pinning; never uses environment proxies/cookies."""

import http.client
import ipaddress
import json
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

from app.providers.call_options import Execution, bounded_call


class ToolError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def public_ip(value):
    addr = ipaddress.ip_address(value)
    if (
        not addr.is_global
        or addr.is_multicast
        or getattr(addr, "is_site_local", False)
        or getattr(addr, "ipv4_mapped", None)
    ):
        return False
    # Reject transition/translation ranges that can encode a private IPv4 target.
    if addr.version == 6 and any(
        addr in ipaddress.ip_network(net)
        for net in ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32")
    ):
        return False
    return True


def public_url(url: str) -> str:
    try:
        p = urlsplit(url)
        if (
            len(url) > 2048
            or any(ord(c) <= 32 or ord(c) == 127 for c in url)
            or "\\" in url
            or p.scheme not in {"http", "https"}
            or not p.hostname
            or p.username is not None
            or p.password is not None
            or p.port not in {None, 80 if p.scheme == "http" else 443}
            or "%" in p.hostname
        ):
            raise ToolError("unsafe_url")
        host = p.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        if not host or len(host) > 253:
            raise ToolError("unsafe_url")
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            if host == "localhost" or host.endswith(
                (".localhost", ".local", ".internal")
            ):
                raise ToolError("unsafe_url") from None
        else:
            if not public_ip(str(addr)):
                raise ToolError("unsafe_url")
        authority = f"[{host}]" if ":" in host else host
        return urlunsplit((p.scheme, authority, p.path or "/", p.query, ""))
    except (UnicodeError, ValueError) as exc:
        raise ToolError("unsafe_url") from exc


def public_addresses(host, port):
    addresses = list(
        dict.fromkeys(
            r[4][0] for r in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        )
    )
    if not addresses:
        raise ToolError("dns_error")
    for value in addresses:
        if not public_ip(value):
            raise ToolError("unsafe_address")
    return sorted(addresses, key=lambda value: ipaddress.ip_address(value).version)


@dataclass(frozen=True)
class Response:
    url: str
    status: int
    headers: dict
    body: bytes


class PublicHTTP:
    def request(
        self,
        url,
        execution=None,
        *,
        method="GET",
        headers=None,
        data=None,
        max_bytes=1_048_576,
        redirects=3,
        timeout=20,
        url_check=None,
    ):
        execution = execution or Execution.start(timeout)
        # DNS, connect, TLS and body reads all run inside the cancellable boundary.
        return bounded_call(
            lambda: self._request(
                url,
                execution,
                method,
                headers or {},
                data,
                max_bytes,
                redirects,
                timeout,
                url_check,
            ),
            execution,
            timeout,
        )

    def _request(
        self,
        url,
        execution,
        method,
        headers,
        data,
        max_bytes,
        redirects,
        timeout,
        url_check=None,
    ):
        url = public_url(url)
        for hop in range(redirects + 1):
            execution.check()
            if url_check:
                url_check(url)
            p = urlsplit(url)
            host, port = p.hostname, 443 if p.scheme == "https" else 80
            addresses = public_addresses(host, port)
            connection = http.client.HTTPConnection(
                host, port, timeout=min(10, timeout, execution.remaining())
            )
            try:
                # Connect to the checked numeric address, never resolve again.
                last_error = None
                for address in addresses[:4]:
                    execution.check()
                    try:
                        connection.sock = socket.create_connection(
                            (address, port),
                            min(3, connection.timeout, execution.remaining()),
                        )
                        break
                    except OSError as exc:
                        last_error = exc
                if connection.sock is None:
                    raise last_error or ToolError("connection_error")
                peer = ipaddress.ip_address(connection.sock.getpeername()[0])
                if str(peer) not in addresses or not peer.is_global:
                    raise ToolError("unsafe_peer")
                if p.scheme == "https":
                    connection.sock = ssl.create_default_context().wrap_socket(
                        connection.sock, server_hostname=host
                    )
                connection.sock.settimeout(min(timeout, execution.remaining()))
                sent = {
                    "User-Agent": "StudyRAG/0.1",
                    "Accept-Encoding": "identity",
                    **headers,
                }
                connection.request(
                    method, urlunsplit(("", "", p.path or "/", p.query, "")), data, sent
                )
                response = connection.getresponse()
                received = {k.lower(): v for k, v in response.getheaders()}
                if response.status in {301, 302, 303, 307, 308}:
                    if (
                        hop == redirects
                        or method != "GET"
                        or headers.get("Authorization")
                    ):
                        raise ToolError("redirect_rejected")
                    target = received.get("location")
                    if not target:
                        raise ToolError("invalid_redirect")
                    url = public_url(urljoin(url, target))
                    continue
                if received.get("content-encoding", "identity") != "identity":
                    raise ToolError("unsupported_encoding")
                if int(received.get("content-length", "0")) > max_bytes:
                    raise ToolError("response_too_large")
                body = bytearray()
                while True:
                    execution.check()
                    if connection.sock is not None:
                        connection.sock.settimeout(min(timeout, execution.remaining()))
                    part = response.read1(min(16384, max_bytes + 1 - len(body)))
                    if not part:
                        break
                    body.extend(part)
                    if len(body) > max_bytes:
                        raise ToolError("response_too_large")
                execution.check()
                return Response(url, response.status, received, bytes(body))
            finally:
                connection.close()
        raise ToolError("redirect_rejected")


def response_json(response):
    if response.status != 200:
        raise ToolError(
            {
                401: "auth",
                403: "forbidden",
                404: "not_found",
                429: "rate_limit",
                432: "quota",
                433: "quota",
            }.get(response.status, "upstream_error")
        )
    try:
        return json.loads(response.body)
    except (ValueError, UnicodeError) as exc:
        raise ToolError("invalid_response") from exc
