"""Diagnostic endpoints (read-only).

These exist to debug infrastructure issues from a browser when the container
has no remote-terminal access. They are deliberately small and do not touch
application state, the database, or any secret. Remove this router once the
WeChat SSL issue is resolved.
"""

import logging

from fastapi import APIRouter

from app.diag import wechat_ssl as wechat_diag

logger = logging.getLogger(__name__)

diagRouter = APIRouter(prefix="/diag", tags=["diag"])


@diagRouter.get("/wechat")
async def diag_wechat():
    """Probe outbound TLS to api.weixin.qq.com (failing) and api.dingtalk.com (control).

    Use this when the WeChat one-tap login returns 502 "WeChat API unreachable"
    to determine whether the failure is a certificate-chain problem on the
    weixin endpoint specifically, a DNS resolution failure, or a poisoned
    proxy/CA environment.
    """
    return wechat_diag.run_diag()
