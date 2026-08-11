"""共享 TLS 上下文:给发往 ``api.weixin.qq.com`` 的 httpx 调用用。

背景(2026-08-11 诊断,见 app/diag/wechat_ssl.py):腾讯云托管容器网络把
``api.weixin.qq.com`` 解析到内网网关 ``169.254.10.1``(链路本地地址,非公网 IP),
该网关用自签证书(issuer==subject==api.weixin.qq.com)应答。这份 CA 在容器的
**系统信任库**里(``ssl.create_default_context()`` 校验通过),但**不在 Python
的 certifi 包里** —— 而 httpx 默认 ``verify=True`` 加载的正是 certifi 的
``cacert.pem``,于是真实调用抛 ``CERTIFICATE_VERIFY_FAILED: self-signed
certificate`` → 登录 502 "WeChat API unreachable"。

修复:用系统信任库构建 ``ssl.SSLContext`` 传给 httpx 的 ``verify=``。证书链
**仍然校验**(不是 ``verify=False``),只是信任源从 certifi 换成系统库;钉钉 /
Supabase 等走真实公网 IP 的上游不受影响(系统库同样信任正规 CA)。本地开发
环境走的仍是微信真实证书(正规 CA),也照常通过。
"""

from __future__ import annotations

import ssl

# 进程级缓存一份即可 —— 系统信任库在容器生命周期内不会变。
_SSL_CONTEXT = ssl.create_default_context()


def wechat_ssl_context() -> ssl.SSLContext:
    """Return the system-store TLS context used for ``api.weixin.qq.com`` calls.

    httpx 的 ``verify=`` 参数接受 ``ssl.SSLContext`` 实例;传它会直接用它做
    握手,而不覆盖 CA bundle。
    """
    return _SSL_CONTEXT
