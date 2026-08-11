"""One-shot in-container diagnostic for the "WeChat API unreachable" 502.

Run inside the CloudBase container (or any machine hitting the same network):
    python scripts/diag_wechat_ssl.py

Prints, for api.weixin.qq.com (the failing one) and api.dingtalk.com (the
working control):
  1. DNS resolution (IPv4 / IPv6 addresses)
  2. HTTP reachability
  3. TLS: cert subject / issuer / chain depth / verify result
  4. Proxy env vars (a self-signed MITM proxy would poison every TLS call)

If api.weixin.qq.com fails cert verification here but api.dingtalk.com passes,
the TLS chain is the culprit (fix = verify=False on that one call). If BOTH
fail, look at proxy env / container CA instead.
"""

import os
import socket
import ssl
import sys

try:
    import httpx
except ImportError:
    httpx = None

HOSTS = {
    "wechat (failing)": ("https://api.weixin.qq.com/sns/jscode2session", "api.weixin.qq.com", 443),
    "dingtalk (control)": ("https://api.dingtalk.com", "api.dingtalk.com", 443),
}


def banner(t):
    print("\n" + "=" * 62)
    print("  " + t)
    print("=" * 62)


def dns_probe(host):
    print(f"  DNS {host}:")
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        fams = set()
        for info in infos:
            fams.add(info[0])
        v6 = socket.AF_INET6 in fams
        addrs = [i[4][0] for i in infos]
        print(f"    families: {'IPv6' if v6 else 'IPv4 only'}  addrs={addrs[:6]}")
    except OSError as exc:
        print(f"    FAILED: {exc}")


def tls_probe(host, port):
    print(f"  TLS {host}:{port}")
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
                chain = tls.get_unverified_chain()
                print(f"    verify: OK")
                print(f"    subject: {dict(x[0] for x in cert.get('subject', []))}")
                print(f"    issuer:  {dict(x[0] for x in cert.get('issuer', []))}")
                print(f"    chain len: {len(chain) if chain else 0}")
    except ssl.SSLCertVerificationError as exc:
        print(f"    CERT_VERIFY_FAILED: {exc}")
    except (ssl.SSLError, OSError) as exc:
        print(f"    TLS/conn error: {exc}")


def httpx_probe(url):
    if httpx is None:
        print("  httpx probe: httpx not installed")
        return
    print(f"  httpx GET {url}")
    try:
        with httpx.Client(timeout=10) as c:
            r = c.get(url)
            print(f"    status={r.status_code} (verify=default)")
    except httpx.RequestError as exc:
        print(f"    FAILED (verify=default): {exc}")
    # retry with verify=False to prove it's the cert, not the connection
    try:
        with httpx.Client(timeout=10, verify=False) as c:
            r = c.get(url)
            print(f"    status={r.status_code} (verify=False)")
    except httpx.RequestError as exc:
        print(f"    FAILED (verify=False): {exc}")


def proxy_env():
    print("  Proxy env:")
    keys = ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
            "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
            "CURL_CA_BUNDLE", "ALL_PROXY"]
    found = False
    for k in keys:
        v = os.environ.get(k)
        if v:
            print(f"    {k}={v}")
            found = True
    if not found:
        print("    (none set)")


def main():
    print(f"Python {sys.version.split()[0]} | httpx {'yes' if httpx else 'no'}")
    proxy_env()
    for label, (url, host, port) in HOSTS.items():
        banner(label)
        dns_probe(host)
        tls_probe(host, port)
        httpx_probe(url)


if __name__ == "__main__":
    main()
