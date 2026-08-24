"""Per-user runtime state + toggle operations for the DingTalk MCP connection.

DingTalk MCP is EXCLUDED from app startup (its ``npx`` subprocess is the
biggest cold-start cost). It is loaded on demand per user:

    POST /api/v1/dingtalk/enable   → load DingTalk tools with THIS user's creds
    POST /api/v1/dingtalk/disable  → unload THIS user's DingTalk tools

Unlike the old global toggle, this module NEVER mutates the shared
``ALL_TOOLS`` / ``MCP_TOOL_NAMES`` / the compiled graph. Each user's loaded
tools, deferred-tool setup, and cached ``ToolNode`` live in a per-user
runtime registry, so enabling DingTalk for user A cannot affect user B.

Key fact about ``MultiServerMCPClient`` (verified against source): it never
holds a long-lived session — ``get_tools()`` spins up a stdio subprocess,
lists tools, and closes it per call. The returned ``BaseTool`` embeds the
``connection`` *config*, not a live session; each real tool invocation starts
and stops its own subprocess. So enabling/disabling is purely a matter of
adding/removing per-user tool references — there is no connection to close.

Enabled flag + credentials are persisted to the store (``("dingtalk",
user_id)``) so a restart restores them lazily on the user's next request.

Lifecycle: ``DingTalkRuntime`` is a class that OWNS the store (injected at
construction) plus the in-memory per-user registry. The container constructs
one instance at startup and points the module-level ``configure_runtime``
accessor at it. Graph-internal call sites (nodes / middleware / binder /
ScopedToolNode) keep calling the module-level functions below, which delegate
to the configured instance — the same "documented indirection to a
lifecycle-managed object" pattern as DeerFlow's ``get_local_provider``. This
removes the old ``builder.store`` module-global reads entirely.

Single-worker deployment (``--workers 1``), so a per-instance ``asyncio.Lock``
plus a couple of per-instance dicts is enough.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from langgraph.prebuilt import ToolNode

from ainote.agents.graph.memory import get_dingtalk_config, put_dingtalk_config
from ainote.tools import ALL_TOOLS
from ainote.tools.core.mcp_loader import load_mcp_tools
from ainote.tools.core.tool_search import DeferredToolSetup, build_deferred_tool_setup

logger = logging.getLogger(__name__)

DINGTALK_SERVER = "dingtalk"

# Env keys MUST match mcp_servers.json EXACTLY (note the capital C/S in
# DINGTALK_Client_ID / DINGTALK_Client_Secret). Per-user overrides are applied
# last in the loader, so they win over os.environ and the config env block.
_CREDENTIAL_FIELDS = ("client_id", "client_secret", "agent_id", "robot_token", "active_profiles")

_DINGTALK_ENV_KEYS: dict[str, str] = {
    "client_id": "DINGTALK_Client_ID",
    "client_secret": "DINGTALK_Client_Secret",
    "agent_id": "DINGTALK_AGENT_ID",
    "robot_token": "ROBOT_ACCESS_TOKEN",
    "active_profiles": "ACTIVE_PROFILES",
}


def _normalize_creds(creds: dict) -> dict:
    """Keep only the credential fields we know how to map to env vars."""
    return {k: v for k, v in creds.items() if k in _CREDENTIAL_FIELDS}


def _env_overrides(creds: dict) -> dict[str, dict[str, str]]:
    """Build ``{server: {env_key: value}}`` overrides from user credentials.

    ``active_profiles`` (a list) is comma-joined for the ``ACTIVE_PROFILES``
    env var. Fields that are empty are skipped (fall back to os.environ).
    """
    out: dict[str, str] = {}
    for field_name, env_key in _DINGTALK_ENV_KEYS.items():
        v = creds.get(field_name)
        if v in (None, ""):
            continue
        if isinstance(v, list):
            v = ",".join(str(x) for x in v)
        out[env_key] = str(v)
    return {DINGTALK_SERVER: out}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Per-user runtime registry ───────────────────────────────────────────


@dataclass
class UserDingTalkRuntime:
    """In-memory runtime state for one user's DingTalk integration.

    ``tool_node`` is a cached ``ToolNode(core + user's tools)`` built at load
    time, so the ScopedToolNode can delegate per-invocation without rebuilding
    (construction is non-trivial: injected-arg introspection per tool).
    """

    user_id: str
    enabled: bool = False
    credentials: dict = field(default_factory=dict)
    tools: list = field(default_factory=list)
    tool_names: set[str] = field(default_factory=set)
    deferred_setup: DeferredToolSetup | None = None
    tool_node: ToolNode | None = None
    loading: bool = False
    load_failed: bool = False
    last_error: str | None = None


class DingTalkError(RuntimeError):
    """Raised when enabling DingTalk fails (kept as a distinct type)."""


class DingTalkConfigError(DingTalkError):
    """Raised when the user's DingTalk credentials are missing/incomplete.

    A client error (HTTP 400) rather than an operational failure (502).
    """


class DingTalkRuntime:
    """Per-user DingTalk MCP runtime registry, owning the injected store.

    The store is injected at construction (by the container) instead of being
    read from ``builder.store`` at call time, so this class is self-contained
    and unit-testable. The in-memory per-user registry lives on the instance;
    graph internals reach it through the module-level ``configure_runtime``
    pointer (see module docstring).
    """

    def __init__(self, *, store):
        self.store = store
        self._user_runtimes: dict[str, UserDingTalkRuntime] = {}
        # Dedup in-flight lazy loads per user (at most one subprocess spawn).
        self._loading_tasks: dict[str, asyncio.Task] = {}
        # Guards the registry + store writes. Holds only SHORT critical
        # sections — never across an ``await load_mcp_tools``, so concurrent
        # users don't serialize.
        self._lock: asyncio.Lock = asyncio.Lock()

    def close(self) -> None:
        """Release in-memory state at shutdown.

        MCP clients never hold long-lived sessions, so this only clears the
        registries and cancels any in-flight lazy loads.
        """
        for task in self._loading_tasks.values():
            task.cancel()
        self._loading_tasks.clear()
        self._user_runtimes.clear()

    # ── Sync accessors (used by binder / middleware / ScopedToolNode) ────

    def get_user_runtime(self, user_id: str) -> UserDingTalkRuntime | None:
        """Return the user's runtime when it is enabled AND has loaded tools."""
        rt = self._user_runtimes.get(user_id)
        if rt is None or not rt.enabled or not rt.tools:
            return None
        return rt

    def get_user_tool_node(self, user_id: str) -> ToolNode | None:
        """Return the user's cached ToolNode, or None when not enabled/loaded."""
        rt = self._user_runtimes.get(user_id)
        if rt is None or not rt.enabled or rt.tool_node is None:
            return None
        # 诊断:每次实际取用该用户的 ToolNode 时打印可用工具数。
        logger.info(
            "DingTalk ToolNode lookup user=%s → %d tool(s) available",
            user_id,
            len(rt.tool_node.tools_by_name),
        )
        return rt.tool_node

    def get_user_deferred_setup(self, user_id: str) -> DeferredToolSetup | None:
        """Return the user's deferred-tool setup, or None when not enabled."""
        rt = self._user_runtimes.get(user_id)
        if rt is None or not rt.enabled:
            return None
        return rt.deferred_setup

    # ── Load / toggle ───────────────────────────────────────────────────

    async def _do_load(self, user_id: str, creds: dict) -> UserDingTalkRuntime:
        """Load DingTalk MCP tools for one user and cache the runtime.

        No lock is held during the subprocess spawn. Mutates only this user's
        entry in the registry — never ``ALL_TOOLS`` / ``MCP_TOOL_NAMES``.
        """
        tools = await load_mcp_tools(
            include={DINGTALK_SERVER},
            env_overrides=_env_overrides(creds),
            register=False,
        )
        if not tools:
            raise DingTalkError(
                "No DingTalk tools loaded — check your DingTalk credentials"
            )

        setup = build_deferred_tool_setup(tools, force_deferred=True)
        node_tools = list(ALL_TOOLS)
        if setup.tool_search_tool is not None:
            node_tools.append(setup.tool_search_tool)
        node_tools.extend(tools)

        rt = UserDingTalkRuntime(
            user_id=user_id,
            enabled=True,
            credentials=_normalize_creds(creds),
            tools=tools,
            tool_names={t.name for t in tools},
            deferred_setup=setup,
            tool_node=ToolNode(node_tools),
        )
        # 诊断:记录该用户 ToolNode 的总工具数构成(核心 + 搜索 + 钉钉)。
        logger.info(
            "DingTalk ToolNode built for user=%s: %d tool(s) = %d core + %d search + %d dingtalk; dingtalk=%s",
            user_id,
            len(node_tools),
            len(ALL_TOOLS),
            1 if setup.tool_search_tool is not None else 0,
            len(tools),
            ", ".join(sorted(t.name for t in tools)),
        )
        async with self._lock:
            self._user_runtimes[user_id] = rt
        return rt

    async def enable(
        self,
        user_id: str,
        credentials: dict | None = None,
        *,
        persist: bool = True,
    ) -> dict:
        """Enable DingTalk for one user with their own credentials.

        Merges the new credentials over any previously stored ones, requires
        ``client_id`` + ``client_secret``, then loads the tools. Idempotent:
        already enabled with the same credentials → no reload.

        Returns ``{"enabled", "changed", "loaded_tools", "tool_names"}``.
        Raises ``DingTalkError`` on failure (state is rolled back).
        """
        existing = get_dingtalk_config(self.store, user_id) or {}
        base = _normalize_creds(existing)
        merged = {**base, **(_normalize_creds(credentials) if credentials else {})}

        if not merged.get("client_id") or not merged.get("client_secret"):
            raise DingTalkConfigError("DingTalk client_id and client_secret are required")

        cur = self._user_runtimes.get(user_id)
        if cur is not None and cur.enabled and cur.credentials == merged:
            return {
                "enabled": True,
                "changed": False,
                "loaded_tools": len(cur.tool_names),
                "tool_names": sorted(cur.tool_names),
            }

        try:
            rt = await self._do_load(user_id, merged)
        except Exception as exc:
            async with self._lock:
                rt = self._user_runtimes.setdefault(
                    user_id, UserDingTalkRuntime(user_id=user_id)
                )
                rt.enabled = False
                rt.load_failed = True
                rt.last_error = str(exc)
                rt.credentials = merged
            if persist:
                put_dingtalk_config(self.store, user_id, {**merged, "enabled": False})
            raise DingTalkError(str(exc)) from exc

        if persist:
            put_dingtalk_config(
                self.store,
                user_id,
                {**merged, "enabled": True, "updated_at": _utc_now()},
            )
        logger.info(
            "DingTalk MCP enabled for user=%s: %d dingtalk tool(s); ToolNode total=%d",
            user_id,
            len(rt.tool_names),
            len(rt.tool_node.tools_by_name) if rt.tool_node else 0,
        )
        return {
            "enabled": True,
            "changed": True,
            "loaded_tools": len(rt.tool_names),
            "tool_names": sorted(rt.tool_names),
        }

    async def disable(self, user_id: str, *, persist: bool = True) -> dict:
        """Unload DingTalk tools for one user. Idempotent.

        Credentials are KEPT in the store so the user can re-enable without
        re-entering them; only ``enabled`` is flipped to False.

        Returns ``{"enabled": False, "changed": bool, "loaded_tools": 0}``.
        """
        async with self._lock:
            rt = self._user_runtimes.setdefault(user_id, UserDingTalkRuntime(user_id=user_id))
            changed = rt.enabled
            rt.enabled = False
            rt.tools = []
            rt.tool_names = set()
            rt.deferred_setup = None
            rt.tool_node = None
            rt.last_error = None
            rt.load_failed = False
            if persist:
                cfg = get_dingtalk_config(self.store, user_id) or {}
                put_dingtalk_config(self.store, user_id, {**cfg, "enabled": False})
        if changed:
            logger.info("DingTalk MCP disabled for user=%s", user_id)
        return {"enabled": False, "changed": changed, "loaded_tools": 0}

    async def mark_user_connected(self, user_id: str) -> None:
        """OAuth 回调成功后调用:同步内存注册表 enabled=True。

        disable 会把内存 ``_user_runtimes[user_id].enabled`` 置 False,而 OAuth
        回调只写 store 的 config.enabled。``get_status`` 优先读内存 ``rt.enabled``,
        若不同步,disable 后再 connect 会出现「store enabled=True 但 status 返回
        False」的不一致。此函数把内存 + store 都置 True。
        """
        async with self._lock:
            rt = self._user_runtimes.setdefault(user_id, UserDingTalkRuntime(user_id=user_id))
            rt.enabled = True
        cfg = get_dingtalk_config(self.store, user_id) or {}
        put_dingtalk_config(self.store, user_id, {**cfg, "enabled": True})
        logger.info("DingTalk MCP marked connected for user=%s", user_id)

    def get_status(self, user_id: str) -> dict:
        """Return a JSON-safe snapshot of THIS user's DingTalk runtime state.

        Secrets are never echoed back — only booleans / counts / names.
        """
        rt = self._user_runtimes.get(user_id)
        cfg = get_dingtalk_config(self.store, user_id) or {}
        logger.info("dingtalk get_status user=%s enabled=%s", user_id, bool(rt.enabled) if rt else bool(cfg.get("enabled")))
        return {
            "user_id": user_id,
            "server": DINGTALK_SERVER,
            "enabled": bool(rt.enabled) if rt else bool(cfg.get("enabled")),
            "credentials_configured": bool(cfg.get("client_id")),
            "active_profiles": cfg.get("active_profiles", []),
            "loaded_tools": len(rt.tool_names) if rt else 0,
            "tool_names": sorted(rt.tool_names) if rt else [],
            "last_error": rt.last_error if rt else None,
        }

    async def ensure_user_tools(self, user_id: str) -> UserDingTalkRuntime:
        """Lazily restore a user's DingTalk runtime from the store.

        Called at the top of ``agent_node``. On the first request after a restart
        (or after the registry was dropped), if the user's stored config says
        ``enabled``, this spawns the MCP subprocess once (a few seconds), then
        caches the runtime. Subsequent turns are registry hits.

        Never raises into the graph: a failed load sets ``load_failed`` so it is
        NOT re-spawned every turn (the user must re-enable via ``/enable``).
        """
        while True:
            async with self._lock:
                rt = self._user_runtimes.get(user_id)
                # Registry hit only when the runtime is USABLE: exists, not
                # mid-load, and either has tools loaded, is disabled (nothing to
                # load), or its load already failed (don't re-spawn every turn).
                # A runtime that is enabled but has NO tools — e.g. OAuth
                # callback marked it connected via mark_user_connected(), which
                # sets enabled but does not load the tools — must fall through
                # and load, or the user's DingTalk tools never bind and AI
                # answers "cannot query".
                if rt is not None and not rt.loading and (rt.tools or not rt.enabled or rt.load_failed):
                    return rt
                task = self._loading_tasks.get(user_id)
                if task is None:
                    cfg = get_dingtalk_config(self.store, user_id)
                    if not cfg or not cfg.get("enabled"):
                        rt = self._user_runtimes.setdefault(
                            user_id, UserDingTalkRuntime(user_id=user_id)
                        )
                        rt.enabled = False
                        return rt
                    rt = self._user_runtimes.setdefault(
                        user_id, UserDingTalkRuntime(user_id=user_id)
                    )
                    rt.loading = True
                    rt.credentials = _normalize_creds(cfg)
                    task = asyncio.create_task(self._do_load(user_id, rt.credentials))
                    self._loading_tasks[user_id] = task
            # Lock released before awaiting the subprocess load.
            try:
                result = await task
                async with self._lock:
                    self._loading_tasks.pop(user_id, None)
                return result
            except Exception as exc:
                async with self._lock:
                    rt = self._user_runtimes.get(user_id)
                    if rt is not None:
                        rt.loading = False
                        rt.load_failed = True
                        rt.last_error = str(exc)
                    self._loading_tasks.pop(user_id, None)
                return self._user_runtimes.get(user_id) or UserDingTalkRuntime(user_id=user_id)


# ── Module-level accessor (configured once by the container) ────────────
#
# Graph-internal call sites (nodes.py, middleware/system_prompt.py,
# tool_binder.py, scoped_tool_node.py) execute OUTSIDE request scope where
# FastAPI ``Depends`` can't reach, so they keep using the module-level
# functions below. Each delegates to the single ``DingTalkRuntime`` instance
# the container installed via ``configure_runtime`` — a documented
# indirection to a lifecycle-managed object, not a self-constructing singleton.

_runtime: DingTalkRuntime | None = None


def configure_runtime(runtime: DingTalkRuntime | None) -> None:
    """Point the module-level accessor at the lifecycle-managed instance.

    Called by the app container at startup (and by tests with an isolated
    instance). Passing ``None`` unconfigures (used in test teardown).
    """
    global _runtime
    _runtime = runtime


def get_runtime() -> DingTalkRuntime:
    """Return the configured ``DingTalkRuntime``, raising if not configured."""
    if _runtime is None:
        raise RuntimeError(
            "DingTalk runtime not configured — call configure_runtime() at app startup"
        )
    return _runtime


# ── Public module-level API (unchanged signatures) ──────────────────────


def get_user_runtime(user_id: str) -> UserDingTalkRuntime | None:
    """Return the user's runtime when it is enabled AND has loaded tools."""
    return get_runtime().get_user_runtime(user_id)


def get_user_tool_node(user_id: str):
    """Return the user's cached ToolNode, or None when not enabled/loaded."""
    return get_runtime().get_user_tool_node(user_id)


def get_user_deferred_setup(user_id: str) -> DeferredToolSetup | None:
    """Return the user's deferred-tool setup, or None when not enabled."""
    return get_runtime().get_user_deferred_setup(user_id)


async def enable_dingtalk(
    user_id: str,
    credentials: dict | None = None,
    *,
    persist: bool = True,
) -> dict:
    """Enable DingTalk for one user with their own credentials. See ``DingTalkRuntime.enable``."""
    return await get_runtime().enable(user_id, credentials, persist=persist)


async def disable_dingtalk(user_id: str, *, persist: bool = True) -> dict:
    """Unload DingTalk tools for one user. Idempotent. See ``DingTalkRuntime.disable``."""
    return await get_runtime().disable(user_id, persist=persist)


async def mark_user_connected(user_id: str) -> None:
    """OAuth 回调成功后调用:同步内存注册表 + store 的 enabled=True。"""
    return await get_runtime().mark_user_connected(user_id)


def get_status(user_id: str) -> dict:
    """Return a JSON-safe snapshot of THIS user's DingTalk runtime state."""
    return get_runtime().get_status(user_id)


async def ensure_user_tools(user_id: str) -> UserDingTalkRuntime:
    """Lazily restore a user's DingTalk runtime from the store."""
    return await get_runtime().ensure_user_tools(user_id)
