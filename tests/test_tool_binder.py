"""Tests for tool binding with schema sanitization (tool_binder.py).

The DingTalk MCP server emits Java-style generic type names (e.g.
"Map<String, Any>") as JSON Schema ``type`` values for some tools
(dingtalk_createNotableSheet). DeepSeek rejects the whole tool list with a 400
when it sees such a schema, so we sanitize every bound tool's schema to valid
JSON Schema types before it reaches the model.
"""

from langchain_core.tools import BaseTool, StructuredTool

from ainote.agents.graph.tool_binder import _sanitize_schema, _sanitize_type, _to_openai_spec


def _make_tool_with_bad_type(name: str = "dingtalk_createNotableSheet") -> BaseTool:
    """Build a tool whose args schema contains an illegal type value."""

    def impl(**kwargs):  # pragma: no cover - never invoked
        return None

    return StructuredTool(
        name=name,
        description="create notable sheet",
        func=impl,
        args_schema=None,
        args={
            "name": {"type": "string", "description": "字段名称"},
            "type": {"type": "string", "description": "字段类型"},
            "property": {"type": "Map<String, Any>", "description": "字段属性"},
        },
    )


# ── _sanitize_type ────────────────────────────────────────────────────────


def test_sanitize_type_coerces_bad_to_object():
    assert _sanitize_type("Map<String, Any>") == "object"
    assert _sanitize_type("string") == "string"
    assert _sanitize_type(None) is None


def test_sanitize_type_handles_lists():
    assert _sanitize_type(["string", "Map<String, Any>"]) == ["string", "object"]


# ── _sanitize_schema ──────────────────────────────────────────────────────


def test_sanitize_schema_recurses_and_does_not_mutate():
    schema = {
        "type": "object",
        "properties": {
            "property": {"type": "Map<String, Any>", "description": "字段属性"},
            "nested": {
                "type": "object",
                "properties": {"x": {"type": "java.util.List<String>"}},
            },
        },
    }
    clean = _sanitize_schema(schema)
    assert clean["properties"]["property"]["type"] == "object"
    assert clean["properties"]["nested"]["properties"]["x"]["type"] == "object"
    # original untouched (we copy on write)
    assert schema["properties"]["property"]["type"] == "Map<String, Any>"


# ── _to_openai_spec ───────────────────────────────────────────────────────


def test_to_openai_spec_wraps_and_sanitizes():
    tool = _make_tool_with_bad_type()
    spec = _to_openai_spec(tool)
    assert spec is not None
    assert spec["type"] == "function"
    fn = spec["function"]
    assert fn["name"] == "dingtalk_createNotableSheet"
    params = fn["parameters"]
    # The sanitizer descends into the nested property, so wherever the bad
    # type sits it ends up coerced to "object".
    props = params.get("properties", {})
    assert "Map<String, Any>" not in str(spec)


def test_to_openai_spec_returns_none_for_unparseable():
    """A tool that can't be serialized is skipped (returns None), not fatal."""

    class _Broken:
        name = "broken"

    assert _to_openai_spec(_Broken()) is None  # type: ignore[arg-type]


# ── misplaced `required` (dingtalk_listNotableRecords) ───────────────────


def test_sanitize_moves_required_out_of_properties():
    """DeepSeek rejects `required` nested inside `properties`; we hoist it.

    The DingTalk MCP server emits:
        {"type":"object","properties":{"field":{...},"required":["field",...]}}
    which is invalid JSON Schema (required must be a top-level key).
    """
    schema = {
        "type": "object",
        "properties": {
            "field": {"type": "string"},
            "operator": {"type": "string"},
            "value": {"type": "array", "items": {"type": "object"}},
            "required": ["field", "operator", "value"],  # misplaced
        },
    }
    clean = _sanitize_schema(schema)
    # `required` now top-level, no longer inside properties.
    assert clean["required"] == ["field", "operator", "value"]
    assert "required" not in clean["properties"]
    assert set(clean["properties"]) == {"field", "operator", "value"}
    # Original untouched.
    assert "required" in schema["properties"]


def test_sanitize_merges_existing_top_required():
    schema = {
        "type": "object",
        "required": ["base"],
        "properties": {
            "base": {"type": "string"},
            "extra": {"type": "string"},
            "required": ["extra"],  # misplaced
        },
    }
    clean = _sanitize_schema(schema)
    # Both merged, deduped, top-level.
    assert sorted(clean["required"]) == ["base", "extra"]
    assert "required" not in clean["properties"]
