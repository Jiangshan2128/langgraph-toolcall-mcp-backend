"""Deferred tool search — DingTalk MCP tools are not bound directly.

Instead, only core tools + a ``tool_search`` tool are bound to the LLM.
DingTalk MCP tools are "deferred": the LLM sees only their names in
``<available-deferred-tools>`` and must call ``tool_search`` to fetch
their full schemas before they can be invoked.

Once promoted, the tool schemas are included in subsequent LLM bindings
via graph state (``promoted_tools``).

Reference: DeerFlow's ``deerflow.tools.builtins.tool_search``
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from functools import cached_property
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langchain_core.utils.function_calling import convert_to_openai_function
from langgraph.types import Command

logger = logging.getLogger(__name__)

MAX_RESULTS = 5  # Max tools returned per search

# ── MCP tool name tracking ──

MCP_TOOL_NAMES: set[str] = set()
"""Names of DingTalk MCP tools. Populated by ``register_mcp_tools()``."""


def register_mcp_tools(tools: list[BaseTool]) -> None:
    """Register tool names as MCP tools so they get deferred."""
    for t in tools:
        MCP_TOOL_NAMES.add(t.name)


def unregister_mcp_tools(names: set[str]) -> None:
    """Remove tool names from the deferred set (e.g. when disabling DingTalk).

    ``build_deferred_tool_setup`` filters deferred tools by ``is_mcp_tool``
    (= name in ``MCP_TOOL_NAMES``), so names must be removed BEFORE rebuilding
    the deferred setup, otherwise the unloaded tools stay in the catalog.
    """
    MCP_TOOL_NAMES.difference_update(names)


def is_mcp_tool(t: BaseTool) -> bool:
    """Check whether a tool is a DingTalk MCP tool by name."""
    return t.name in MCP_TOOL_NAMES


# ── Catalog ──


@dataclass(frozen=True)
class DeferredToolCatalog:
    """Immutable catalog of deferred (MCP) tools. Pure search, no mutation."""

    tools: tuple[BaseTool, ...]

    @cached_property
    def names(self) -> frozenset[str]:
        return frozenset(t.name for t in self.tools)

    @cached_property
    def hash(self) -> str:
        canon = [
            {"name": t.name, "schema": convert_to_openai_function(t)}
            for t in sorted(self.tools, key=lambda t: t.name)
        ]
        blob = json.dumps(canon, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def search(self, query: str) -> list[BaseTool]:
        query = query.strip()
        if not query:
            return []

        if query.startswith("select:"):
            wanted = {n.strip() for n in query[7:].split(",")}
            return [t for t in self.tools if t.name in wanted][:MAX_RESULTS]

        if query.startswith("+"):
            parts = query[1:].split(None, 1)
            if not parts:
                return []
            required = parts[0].lower()
            candidates = [t for t in self.tools if required in t.name.lower()]
            if len(parts) > 1:
                candidates.sort(
                    key=lambda t: _catalog_regex_score(parts[1], t),
                    reverse=True,
                )
            return candidates[:MAX_RESULTS]

        # Default: split into tokens, rank by match count.
        # Name matches score double; tools with no match are excluded.
        tokens = query.lower().split()
        if not tokens:
            return []
        scored: list[tuple[int, BaseTool]] = []
        for t in self.tools:
            name_lower = t.name.lower()
            desc_lower = (t.description or "").lower()
            score = 0
            for token in tokens:
                if token in name_lower:
                    score += 2
                elif token in desc_lower:
                    score += 1
            if score > 0:
                scored.append((score, t))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scored][:MAX_RESULTS]


def _catalog_regex_score(pattern: str, t: BaseTool) -> int:
    regex = _compile_catalog_regex(pattern)
    return len(regex.findall(f"{t.name} {t.description or ''}"))


def _compile_catalog_regex(pattern: str) -> re.Pattern[str]:
    """Compile ``pattern`` case-insensitively, falling back to literal match."""
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return re.compile(re.escape(pattern), re.IGNORECASE)


# ── Build tool_search tool ──


def build_tool_search_tool(catalog: DeferredToolCatalog) -> BaseTool:
    """Build the ``tool_search`` tool that searches deferred tools.

    The tool returns a ``Command`` that updates ``promoted_tools`` in graph
    state so the next ``agent_node`` invocation binds the promoted schemas.
    """

    @tool
    def tool_search(
        query: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        """Search for DingTalk office tools by keyword or name.

        DingTalk office tools (contacts, calendar, tasks, AI tables, robot
        messages, work notifications, checkin, honor, reports, projects, etc.)
        are not all loaded by default. Use this tool to find and activate the
        specific tool you need.

        Query forms:
          - ``"calendar"`` — keyword search (name + description), returns best matches
          - ``"select:dingtalk_create_event,dingtalk_send_message"`` — fetch exact tools by name
          - ``"+dingtalk cal"`` — require "dingtalk" in the name, rank by "cal"

        Returns the full JSON schema of matched tools so they become callable.
        """
        matched = catalog.search(query)[:MAX_RESULTS]
        if not matched:
            return Command(
                update={
                    "promoted_tools": [],
                    "messages": [
                        ToolMessage(
                            content=f"No DingTalk tools found matching: {query}",
                            tool_call_id=tool_call_id,
                            name="tool_search",
                        )
                    ],
                }
            )

        schemas_json = json.dumps(
            [convert_to_openai_function(t) for t in matched],
            indent=2,
            ensure_ascii=False,
        )
        promoted_names = [t.name for t in matched]
        return Command(
            update={
                "promoted_tools": promoted_names,
                "messages": [
                    ToolMessage(
                        content=schemas_json,
                        tool_call_id=tool_call_id,
                        name="tool_search",
                    )
                ],
            }
        )

    return tool_search


# ── Deferred setup assembly ──


@dataclass(frozen=True)
class DeferredToolSetup:
    """Result of assembling deferred-tool support.

    ``tool_search_tool`` is the tool to bind to the LLM / add to ALL_TOOLS.
    ``deferred_names`` are shown in ``<available-deferred-tools>``.
    ``catalog_hash`` identifies the catalog version for state scoping.

    Invariant: ``tool_search_tool is None`` ⟺ ``deferred_names`` is empty.
    """

    tool_search_tool: BaseTool | None
    deferred_names: frozenset[str]
    catalog_hash: str | None


def build_deferred_tool_setup(
    all_tools: list[BaseTool],
    *,
    enabled: bool = True,
    force_deferred: bool = False,
) -> DeferredToolSetup:
    """Build deferred-tool setup from the full tool list.

    MCP-tagged tools are deferred; everything else is bound directly.
    ``force_deferred`` treats ALL passed tools as deferred, bypassing the
    global ``is_mcp_tool`` filter — used to build a per-user setup from a
    per-user tool list without touching the shared ``MCP_TOOL_NAMES``.
    """
    if not enabled:
        return DeferredToolSetup(None, frozenset(), None)

    deferred = all_tools if force_deferred else [t for t in all_tools if is_mcp_tool(t)]
    if not deferred:
        return DeferredToolSetup(None, frozenset(), None)

    catalog = DeferredToolCatalog(tuple(deferred))
    return DeferredToolSetup(
        tool_search_tool=build_tool_search_tool(catalog),
        deferred_names=catalog.names,
        catalog_hash=catalog.hash,
    )


# ── Prompt rendering ──


def get_deferred_tools_prompt_section(
    deferred_names: frozenset[str] = frozenset(),
) -> str:
    """Generate ``<available-deferred-tools>`` section for the system prompt."""
    if not deferred_names:
        return ""
    names = "\n".join(sorted(deferred_names))
    # DingTalk todo tools that users ask about most. When the user wants to
    # view/create their DingTalk todos, promote the exact tool with tool_search
    # "select:..." instead of guessing from the name list. (Only listed if the
    # server actually exposes them.)
    common = [n for n in (
        "dingtalk_queryTasks",
        "dingtalk_createTask",
        "dingtalk_updateTask",
        "dingtalk_deleteTask",
    ) if n in deferred_names]
    tips = "\n".join(f"  - {n}" for n in common)
    quick_guide = ""
    if tips:
        quick_guide = (
            "\n"
            "Common DingTalk todo operations — promote the exact tool first:\n"
            f"{tips}\n"
            "  Example: tool_search(query=\"select:dingtalk_queryTasks\"), then call it."
        )
    return (
        "\n<available-deferred-tools>\n"
        f"{names}\n"
        "</available-deferred-tools>\n"
        "\n"
        "These tools are available but not pre-loaded. To use one, call "
        "``tool_search`` with a keyword or exact name. "
        "Once fetched, the tool becomes callable for the rest of the conversation."
        f"{quick_guide}"
    )
