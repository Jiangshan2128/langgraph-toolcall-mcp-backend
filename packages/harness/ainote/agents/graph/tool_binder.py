"""Tool binding with deferred DingTalk MCP tool search.

Core tools are always bound. If DingTalk MCP tools are loaded, a
``tool_search`` tool is dynamically created and added to ``ALL_TOOLS``,
allowing the LLM to discover and promote MCP tools at runtime via
graph state (``promoted_tools``).
"""

import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_function

from ainote.agents.graph.model.model import get_model
from ainote.tools import ALL_TOOLS

logger = logging.getLogger(__name__)

# JSON Schema type values that are valid per the spec. DingTalk MCP sometimes
# emits Java-style generic type names (e.g. "Map<String, Any>") as the `type`
# of a property, which is NOT valid JSON Schema and makes DeepSeek reject the
# whole tool list with a 400. We sanitize such values to "object" at bind time
# so one bad schema can't take down the request.
_VALID_JSONSCHEMA_TYPES = frozenset({
    "string", "number", "integer", "boolean", "object", "array", "null",
})


def _sanitize_type(value):
    """Coerce a raw JSON Schema ``type`` value to a valid one.

    Strings that aren't valid JSON Schema types (e.g. "Map<String, Any>")
    become "object"; lists of types are validated element-wise; anything else
    (none) is left alone.
    """
    if isinstance(value, list):
        return [_sanitize_type(v) for v in value]
    if isinstance(value, str):
        return value if value in _VALID_JSONSCHEMA_TYPES else "object"
    return value


def _sanitize_schema(schema):
    """Recursively fix non-spec ``type`` values and misplaced ``required`` in a
    JSON Schema dict.

    Mutates nothing — returns a sanitized copy so the original tool object is
    untouched (the MCP tool keeps its original schema; only the bound copy is
    cleaned).

    Two problems are fixed:
      1. ``type`` values that aren't valid JSON Schema types (e.g. the DingTalk
         MCP server emitting "Map<String, Any>") are coerced to "object".
      2. ``required`` arrays that the server emitted INSIDE ``properties``
         (sibling to the field names, which is invalid JSON Schema) are moved
         up to the schema's top level. DeepSeek rejects the misplaced form
         with "not valid under any of the schemas listed in the 'anyOf'".
    """
    if isinstance(schema, dict):
        out = dict(schema)
        if "type" in out:
            out["type"] = _sanitize_type(out["type"])

        props = out.get("properties")
        if isinstance(props, dict):
            misplaced = props.get("required")
            if isinstance(misplaced, list):
                # Move the misplaced required array up to this schema's top
                # level (valid position), merging with any existing one.
                out["required"] = list(dict.fromkeys(
                    list(out.get("required", [])) + misplaced
                ))
                props = {k: v for k, v in props.items() if k != "required"}
                out["properties"] = props
            # Recurse into the (cleaned) properties.
            out["properties"] = {
                k: _sanitize_schema(v) for k, v in props.items()
            }

        for key, value in out.items():
            if key in ("type", "properties", "required"):
                continue
            if isinstance(value, dict):
                out[key] = _sanitize_schema(value)
            elif isinstance(value, list):
                out[key] = [
                    _sanitize_schema(v) if isinstance(v, dict) else v
                    for v in value
                ]
        return out
    return schema


def _to_openai_spec(tool: BaseTool) -> dict | None:
    """Convert a tool to a sanitized, fully-wrapped OpenAI tool dict.

    Returns ``{"type": "function", "function": {name, description, parameters}}``
    with any illegal JSON Schema ``type`` values (e.g. "Map<String, Any>")
    coerced to a valid one. Returns ``None`` when the tool can't be serialized
    (it is then skipped from binding).

    Why the full wrap matters: ``convert_to_openai_tool`` passes dicts whose
    ``type`` is a known OpenAI tool ("function") through unchanged, so the
    sanitized schema reaches DeepSeek exactly as built here. Binding the
    ``BaseTool`` directly would re-serialize from the original tool object and
    the illegal type would 400 the whole request.
    """
    try:
        spec = convert_to_openai_function(tool)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Tool '%s' has an un-parseable schema: %s", tool.name, exc)
        return None
    if not spec:
        return None
    params = spec.get("parameters")
    if params:
        spec["parameters"] = _sanitize_schema(params)
    return {"type": "function", "function": spec}

# Tools that are ALWAYS bound to the LLM.
_CORE_TOOL_NAMES: frozenset[str] = frozenset({
    "update_profile",
    "update_tasks",
    "update_instructions",
    "get_tasks",
    "mark_task_done",
    "update_task_priority",
    "delete_task_by_title",
    "web_search",
    "get_current_time",
})


def get_model_with_tools(
    *,
    promoted_names: list[str] | None = None,
    user_id: str = "default",
) -> BaseChatModel:
    """Return a model with the appropriate tools bound.

    Uses the ``deepseek-reasoning`` provider (thinking enabled) for the main
    chat path, whose bind_tools uses the default tool_choice=auto. TrustCall
    paths keep ``get_model()`` (the thinking-disabled ``deepseek-chat``)
    because TrustCall forces tool_choice="required" internally, which DeepSeek
    reasoning rejects while thinking is on.

    Binding strategy:
      1. Core tools are always bound.
      2. THIS user's ``tool_search`` (if their DingTalk is enabled) is bound.
      3. Promoted tool names (from ``state["promoted_tools"]``) have their
         full schemas bound so the LLM can call them — but ONLY if the name is
         still in the user's current tool set (stale names left over from a
         disable are filtered out).

    ``user_id`` selects the per-user DingTalk runtime. Users without DingTalk
    enabled simply get core tools (+ core ``tool_search`` if any).
    """
    model = get_model("deepseek-reasoning")

    # Merge the shared core tools with THIS user's enabled DingTalk tools.
    # Lazy import: tool_binder sits at a module-load boundary (imported from
    # middleware only at call time); dingtalk_runtime pulls in builder.
    from ainote.agents.graph.dingtalk_runtime import get_user_runtime

    tool_map = {t.name: t for t in ALL_TOOLS}
    rt = get_user_runtime(user_id)
    if rt is not None:
        for t in rt.tools:
            tool_map[t.name] = t
        if rt.deferred_setup is not None and rt.deferred_setup.tool_search_tool is not None:
            ts = rt.deferred_setup.tool_search_tool
            tool_map[ts.name] = ts

    # 1. Core tools are always bound.
    bind_list: list[BaseTool] = [
        tool_map[name] for name in _CORE_TOOL_NAMES if name in tool_map
    ]

    # 2. The user's tool_search is always bound if present.
    if "tool_search" in tool_map:
        bind_list.append(tool_map["tool_search"])

    # 3. Add promoted (previously searched) MCP tools — filtered against the
    #    user's CURRENT tool set so dead schemas from a disable never reach the model.
    if promoted_names:
        for name in promoted_names:
            if name in tool_map and name not in _CORE_TOOL_NAMES and name != "tool_search":
                bind_list.append(tool_map[name])

    # Serialize every tool to a sanitized OpenAI function spec. Binding dicts
    # (not BaseTool) ensures the schema that reaches DeepSeek is the sanitized
    # one — a single MCP tool with a non-spec type (e.g. "Map<String, Any>")
    # would otherwise 400 the whole request. Core/tool_search schemas are
    # clean; this is a cheap no-op for them.
    specs = [_to_openai_spec(t) for t in bind_list]
    specs = [s for s in specs if s is not None]
    skipped = len(bind_list) - len(specs)

    logger.info(
        "Binding %d tools: core=%d, tool_search=%s, promoted=%d, skipped_bad_schema=%d",
        len(specs),
        len([n for n in _CORE_TOOL_NAMES if n in tool_map]),
        "tool_search" in tool_map,
        len(promoted_names or []),
        skipped,
    )
    return model.bind_tools(specs)
