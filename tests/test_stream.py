"""Tests for anthropic_api/stream.py - tool name reverse mapping"""
import json
import pytest
from anthropic_api.stream import StreamContext, BufferedStreamContext
from kiro.model.events.tool_use import ToolUseEvent


class TestStreamToolNameReverseMap:
    def test_reverse_map_on_tool_use_stop(self):
        """Short tool name in Kiro response gets restored to original in output"""
        name_map = {"short_abc12345": "very_long_original_tool_name_that_exceeds_63_characters_limit_here"}
        ctx = StreamContext("claude-sonnet-4.6", 100, False, name_map)
        ctx.generate_initial_events()

        # Send input fragment first
        ctx._process_tool_use(ToolUseEvent(
            name="short_abc12345", tool_use_id="toolu_123",
            input='{"key": "value"}', stop=False,
        ))
        # Then stop=True triggers output
        result = ctx._process_tool_use(ToolUseEvent(
            name="short_abc12345", tool_use_id="toolu_123",
            input="", stop=True,
        ))
        assert result is not None and len(result) > 0
        # The content_block_start SSE should contain the original long name
        found = any(
            "very_long_original_tool_name" in str(getattr(r, 'data', r))
            for r in result
        )
        assert found, f"Original tool name not found in output: {result}"

    def test_no_map_passthrough(self):
        """Without map, tool names pass through unchanged"""
        ctx = StreamContext("claude-sonnet-4.6", 100, False, None)
        ctx.generate_initial_events()
        ctx._process_tool_use(ToolUseEvent(
            name="read_file", tool_use_id="toolu_456",
            input='{"path": "/tmp"}', stop=False,
        ))
        result = ctx._process_tool_use(ToolUseEvent(
            name="read_file", tool_use_id="toolu_456",
            input="", stop=True,
        ))
        assert result is not None and len(result) > 0
        found = any("read_file" in str(getattr(r, 'data', r)) for r in result)
        assert found

    def test_buffered_accepts_tool_name_map(self):
        """BufferedStreamContext correctly passes tool_name_map to inner"""
        name_map = {"short_x": "original_x"}
        buf_ctx = BufferedStreamContext("claude-sonnet-4.6", 100, False, name_map)
        # tool_name_map is short→original, stored as-is for reverse lookup
        assert buf_ctx.inner._tool_name_reverse_map == {"short_x": "original_x"}


class TestStreamContextInit:
    def test_initial_events_structure(self):
        ctx = StreamContext("claude-sonnet-4.6", 100, False)
        events = ctx.generate_initial_events()
        assert len(events) >= 1
        assert any("message_start" in str(e) for e in events)

    def test_empty_tool_name_map(self):
        ctx = StreamContext("claude-sonnet-4.6", 100, False, {})
        assert ctx._tool_name_reverse_map == {}

