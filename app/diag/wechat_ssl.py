"""In-container TLS diagnostic for the "WeChat API unreachable" 502.

Exposed as ``GET /api/v1/diag/wechat`` (see app.diag.router) so the CloudBase
container can be probed from a browser when there is no remote-terminal access.

Reachability of the app's outbound HTTPS paths is checked WITHOUT a full
handshake against each domain: we run a TCP connect + TLS handshake to
``api.weixin.qq.com`` (the failing upstream) and to ``api.dingtalk.com`` (the
working control), and report whether certificate verification succeeds.

Everything is read-only / non-mutating and returns JSON.
"""

from __future__ import annotations

import logging
import os
import socket
import ssl

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 443

_TARGETS = (
    ("wechat", "api.weixin.qq.com"),
    ("dingtalk", "api.dingtalk.com"),
)


def _tls_probe(host: str, port: int = _DEFAULT_PORT) -> dict:
    """TCP connect + TLS handshake against ``host``.

    Returns a dict describing whether verification succeeded and, when it
    failed, the reason (SSL error string). Reads the peer cert (unverified) so
    the caller can see what cert was actually served even when verify fails.
    """
    out = {
        "host": host,
        "port": port,
        "verify_ok": False,
        "error": None,
        "cert": None,
    }
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                out["verify_ok"] = True
                cert = tls.getpeercert()
                if cert:
                    out["cert"] = {
                        "subject": dict(x[0] for x in cert.get("subject", [])),
                        "issuer": dict(x[0] for x in cert.get("issuer", [])),
                        "notAfter": cert.get("notAfter"),
                    }
    except ssl.SSLCertVerificationError as exc:
        out["error"] = f"CERT_VERIFY_FAILED: {exc}"
        logger.warning("diag TLS verify failed for %s: %s", host, exc)
    except (ssl.SSLError, OSError) as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        logger.warning("diag TLS error for %s: %s", host, exc)
    return out


def _dns_probe(host: str) -> dict:
    """Resolve ``host`` to a list of addresses (with family info)."""
    out = {"host": host, "addrs": [], "families": [], "error": None}
    try:
        infos = socket.getaddrinfo(host, _DEFAULT_PORT, proto=socket.IPPROTO_TCP)
        seen = set()
        for info in infos:
            fam = "IPv6" if info[0] == socket.AF_INET6 else "IPv4"
            if fam not in out["families"]:
                out["families"].append(fam)
            addr = info[4][0]
            if addr not in seen:
                seen.add(addr)
                out["addrs"].append(addr)
    except OSError as exc:
        out["error"] = str(exc)
        logger.warning("diag DNS failed for %s: %s", host, exc)
    return out


def _proxy_env() -> dict:
    """Report proxy/CA env vars that could poison TLS verification."""
    keys = [
        "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
        "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "ALL_PROXY",
    ]
    return {k: os.environ.get(k) for k in keys if os.environ.get(k)}


def run_diag() -> dict:
    """Run the full read-only diagnostic and return a JSON-safe dict."""
    return {
        "targets": [
            {
                "name": name,
                "dns": _dns_probe(host),
                "tls": _tls_probe(host),
            }
            for name, host in _TARGETS
        ],
        "proxy_env": _proxy_env(),
    }
