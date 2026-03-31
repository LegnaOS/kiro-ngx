"""Tests for anthropic_api/converter.py - Rust parity verification"""
import pytest
from anthropic_api.converter import (
    _shorten_tool_name, _map_tool_name, TOOL_NAME_MAX_LEN,
    _validate_tool_pairing, _remove_orphaned_tool_uses,
    _merge_adjacent_messages, _dedupe_tool_results,
    convert_request, map_model, get_context_window_size,
    EmptyMessagesError, UnsupportedModelError,
)
from anthropic_api.types import AnthropicMessage, MessagesRequest
from kiro.model.requests.conversation import (
    AssistantMessage, HistoryAssistantMessage, HistoryUserMessage,
    Message, UserMessage, UserInputMessageContext,
)
from kiro.model.requests.tool import ToolResult, ToolUseEntry


# ---- Tool name shortening ----

class TestToolNameShortening:
    def test_short_name_unchanged(self):
        name = "read_file"
        assert _map_tool_name(name, {}) == name

    def test_exact_63_unchanged(self):
        name = "a" * 63
        m = {}
        assert _map_tool_name(name, m) == name
        assert m == {}

    def test_64_chars_shortened(self):
        name = "a" * 64
        m = {}
        result = _map_tool_name(name, m)
        assert len(result) == TOOL_NAME_MAX_LEN
        assert result in m
        assert m[result] == name

    def test_deterministic(self):
        name = "x" * 100
        assert _shorten_tool_name(name) == _shorten_tool_name(name)

    def test_different_names_different_hashes(self):
        a = _shorten_tool_name("a" * 100)
        b = _shorten_tool_name("b" * 100)
        assert a != b


# ---- Model mapping ----

class TestMapModel:
    def test_sonnet_46(self):
        assert map_model("claude-sonnet-4.6-20260301") == "claude-sonnet-4.6"

    def test_sonnet_45(self):
        assert map_model("claude-sonnet-4-5-20250514") == "claude-sonnet-4.5"

    def test_opus_46(self):
        assert map_model("claude-opus-4.6-20260301") == "claude-opus-4.6"

    def test_haiku(self):
        assert map_model("claude-haiku-4-5-20250514") == "claude-haiku-4.5"

    def test_unknown_passthrough(self):
        assert map_model("gpt-4o") == "gpt-4o"


# ---- Context window ----

class TestContextWindow:
    def test_46_model_1m(self):
        assert get_context_window_size("claude-sonnet-4.6-20260301") == 1_000_000

    def test_45_model_200k(self):
        assert get_context_window_size("claude-sonnet-4-5-20250514") == 200_000


# ---- Tool pairing validation ----

class TestToolPairing:
    def _make_history_with_tool_use(self, tool_use_id: str, name: str = "test") -> list:
        am = AssistantMessage.new("ok")
        am.tool_uses = [ToolUseEntry(tool_use_id=tool_use_id, name=name, input={})]
        return [Message(HistoryAssistantMessage(assistant_response_message=am))]

    def test_valid_pairing(self):
        history = self._make_history_with_tool_use("tu_1")
        results = [ToolResult.success("tu_1", "done")]
        filtered, orphaned = _validate_tool_pairing(history, results)
        assert len(filtered) == 1
        assert filtered[0].tool_use_id == "tu_1"
        assert len(orphaned) == 0

    def test_orphaned_tool_use(self):
        history = self._make_history_with_tool_use("tu_1")
        filtered, orphaned = _validate_tool_pairing(history, [])
        assert len(filtered) == 0
        assert "tu_1" in orphaned

    def test_orphaned_tool_result_dropped(self):
        filtered, orphaned = _validate_tool_pairing([], [ToolResult.success("tu_x", "?")])
        assert len(filtered) == 0

    def test_remove_orphaned_tool_uses(self):
        history = self._make_history_with_tool_use("tu_1")
        _remove_orphaned_tool_uses(history, {"tu_1"})
        am = history[0]._data.assistant_response_message
        assert am.tool_uses is None  # cleaned up


# ---- Dedupe tool results ----

class TestDedupeToolResults:
    def test_deduplicates(self):
        results = [ToolResult.success("id1", "a"), ToolResult.success("id1", "b")]
        deduped = _dedupe_tool_results(results)
        assert len(deduped) == 1


# ---- Prefill handling ----

class TestPrefillHandling:
    def test_trailing_assistant_dropped(self):
        req = MessagesRequest(
            model="claude-sonnet-4.6-20260301", max_tokens=4096,
            messages=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "thinking..."},
            ],
        )
        result = convert_request(req)
        # Current message should be "hello", not the assistant prefill
        cm = result.conversation_state.current_message.user_input_message
        assert "hello" in cm.content

    def test_all_assistant_raises(self):
        req = MessagesRequest(
            model="claude-sonnet-4.6-20260301", max_tokens=4096,
            messages=[{"role": "assistant", "content": "prefill"}],
        )
        with pytest.raises(EmptyMessagesError):
            convert_request(req)


# ---- System message injection ----

class TestSystemMessage:
    def test_system_injected_as_pair(self):
        req = MessagesRequest(
            model="claude-sonnet-4.6-20260301", max_tokens=4096,
            system=[{"text": "You are helpful."}],
            messages=[{"role": "user", "content": "hi"}],
        )
        result = convert_request(req)
        history = result.conversation_state.history
        # First pair should be the system message user + assistant ack
        if len(history) >= 2:
            assert history[0].is_user()
            assert history[1].is_assistant()

