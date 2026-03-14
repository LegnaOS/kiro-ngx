import unittest
import asyncio
import json

import token_counter
from anthropic_api.converter import convert_request, normalize_json_schema
from anthropic_api.handlers import (
    LOCAL_REQUEST_MAX_CHARS,
    LocalRequestLimitError,
    _apply_capacity_compaction,
    _map_provider_error,
    _handle_non_stream_request,
    _needs_capacity_compaction,
    _prune_history_for_capacity,
    _validate_outbound_kiro_request,
)
from anthropic_api.types import MessagesRequest


class TokenCounterCoverageTest(unittest.TestCase):
    def test_count_all_tokens_covers_thinking_tool_use_and_tool_result(self):
        base = token_counter.count_all_tokens(
            "claude-sonnet-4-6",
            system=None,
            messages=[{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
            tools=None,
        )

        richer = token_counter.count_all_tokens(
            "claude-sonnet-4-6",
            system=[{"text": "system prompt"}],
            messages=[
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "plan " * 120},
                        {"type": "tool_use", "id": "tu_1", "name": "Edit", "input": {"value": "x" * 800}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_1",
                            "content": [{"type": "text", "text": "result " * 400}],
                        }
                    ],
                },
            ],
            tools=[
                {
                    "name": "Edit",
                    "description": "Modify a file",
                    "input_schema": {"type": "object", "properties": {"value": {"type": "string"}}},
                }
            ],
            thinking={"type": "enabled", "budget_tokens": 4096},
            output_config={"effort": "high"},
        )

        self.assertGreater(richer, base * 20)


class ConverterGuardsTest(unittest.TestCase):
    def test_normalize_json_schema_repairs_null_properties_required_and_additional_properties(self):
        normalized = normalize_json_schema({
            "type": "object",
            "properties": None,
            "required": None,
            "additionalProperties": None,
            "items": None,
        })

        self.assertEqual(normalized["type"], "object")
        self.assertEqual(normalized["properties"], {})
        self.assertEqual(normalized["required"], [])
        self.assertTrue(normalized["additionalProperties"])
        self.assertEqual(normalized["items"]["type"], "object")
        self.assertEqual(normalized["items"]["properties"], {})

    def test_convert_request_preserves_large_tool_result_like_a2(self):
        huge_result = "<task-notification>\n" + "\n".join(f"line {idx} " + ("x" * 120) for idx in range(600))
        payload = MessagesRequest(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "toolu_1", "name": "Task", "input": {"arg": "v"}}],
                },
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": huge_result}],
                },
            ],
        )

        result = convert_request(payload)
        tool_results = (
            result.conversation_state.current_message.user_input_message.user_input_message_context.tool_results
        )

        self.assertEqual(len(tool_results), 1)
        tool_text = tool_results[0].content[0]["text"]
        self.assertEqual(tool_text, huge_result)

    def test_convert_request_moves_last_assistant_into_history(self):
        payload = MessagesRequest(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[
                {"role": "user", "content": [{"type": "text", "text": "first"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "second"}]},
            ],
        )

        result = convert_request(payload)
        history = [msg.to_dict() for msg in result.conversation_state.history]

        self.assertEqual(result.conversation_state.current_message.user_input_message.content, "Continue")
        self.assertEqual(history[-1]["assistantResponseMessage"]["content"], "second")

    def test_convert_request_repairs_invalid_tool_schema_before_serialization(self):
        payload = MessagesRequest(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[
                {"role": "user", "content": [{"type": "text", "text": "run tool"}]},
            ],
            tools=[
                {
                    "name": "broken_tool",
                    "description": "tool with invalid schema fields",
                    "input_schema": {
                        "type": "object",
                        "properties": None,
                        "required": None,
                        "additionalProperties": None,
                    },
                }
            ],
        )

        result = convert_request(payload)
        tools = result.conversation_state.current_message.user_input_message.user_input_message_context.tools
        schema = tools[0].tool_specification.input_schema.json

        self.assertEqual(schema["properties"], {})
        self.assertEqual(schema["required"], [])
        self.assertTrue(schema["additionalProperties"])

    def test_convert_request_filters_web_search_tool_like_a2(self):
        payload = MessagesRequest(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[
                {"role": "user", "content": [{"type": "text", "text": "search"}]},
            ],
            tools=[
                {
                    "name": "web_search",
                    "type": "web_search_20250305",
                    "description": "search the web",
                    "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
                }
            ],
        )

        result = convert_request(payload)
        tools = result.conversation_state.current_message.user_input_message.user_input_message_context.tools

        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].tool_specification.name, "no_tool_available")
        self.assertIn("placeholder", tools[0].tool_specification.description)


class HandlerPreflightTest(unittest.TestCase):
    def test_capacity_compaction_trigger_uses_kiro_payload_metrics(self):
        self.assertFalse(_needs_capacity_compaction(token_counter.PayloadMetrics(tokens=80_000, chars=100_000, bytes=110_000)))
        self.assertFalse(_needs_capacity_compaction(token_counter.PayloadMetrics(tokens=10_000, chars=200_000, bytes=100_000)))
        self.assertFalse(_needs_capacity_compaction(token_counter.PayloadMetrics(tokens=10_000, chars=100_000, bytes=250_000)))
        self.assertFalse(_needs_capacity_compaction(token_counter.PayloadMetrics(tokens=30_000, chars=80_000, bytes=90_000)))
        self.assertTrue(_needs_capacity_compaction(token_counter.PayloadMetrics(tokens=181_000, chars=100_000, bytes=110_000)))

    def test_apply_capacity_compaction_is_noop_to_match_a2(self):
        long_text = "x" * 5000
        kiro_request = {
            "conversationState": {
                "history": [
                    {
                        "userInputMessage": {
                            "content": "history " + ("y" * 4000),
                            "userInputMessageContext": {
                                "toolResults": [
                                    {"toolUseId": "tu_hist", "content": [{"text": long_text}]}
                                ]
                            },
                        }
                    }
                ],
                "currentMessage": {
                    "userInputMessage": {
                        "content": "current",
                        "userInputMessageContext": {
                            "toolResults": [
                                {"toolUseId": "tu_cur", "content": [{"text": long_text}]}
                            ],
                            "tools": [
                                {
                                    "toolSpecification": {
                                        "name": "big_tool",
                                        "description": "d" * 6000,
                                        "inputSchema": {"json": {"type": "object", "properties": {}}},
                                    }
                                }
                            ],
                        },
                    }
                },
            }
        }

        stats = _apply_capacity_compaction(kiro_request)
        self.assertEqual(stats["history_tool_results"], 0)
        self.assertEqual(stats["current_tool_results"], 0)
        self.assertEqual(stats["tools"], 0)
        self.assertEqual(stats["history_contents"], 0)

        hist_text = (
            kiro_request["conversationState"]["history"][0]["userInputMessage"]["userInputMessageContext"]
            ["toolResults"][0]["content"][0]["text"]
        )
        cur_text = (
            kiro_request["conversationState"]["currentMessage"]["userInputMessage"]["userInputMessageContext"]
            ["toolResults"][0]["content"][0]["text"]
        )
        tool_desc = (
            kiro_request["conversationState"]["currentMessage"]["userInputMessage"]["userInputMessageContext"]
            ["tools"][0]["toolSpecification"]["description"]
        )
        self.assertEqual(len(hist_text), len(long_text))
        self.assertEqual(len(cur_text), len(long_text))
        self.assertEqual(len(tool_desc), 6000)

    def test_prune_history_for_capacity_is_effectively_disabled_below_hard_limit(self):
        history = []
        for idx in range(80):
            history.append({
                "userInputMessage": {
                    "content": f"entry-{idx}-" + ("z" * 3000),
                    "modelId": "claude-sonnet-4-5",
                }
            })
        kiro_request = {
            "conversationState": {
                "history": history,
                "currentMessage": {
                    "userInputMessage": {
                        "content": "current",
                        "modelId": "claude-sonnet-4-5",
                    }
                },
            }
        }
        start_len = len(history)
        dropped, body, metrics = _prune_history_for_capacity(
            kiro_request,
            token_counter.PayloadMetrics(tokens=150_000, chars=600_000, bytes=700_000),
        )
        self.assertEqual(dropped, 0)
        self.assertEqual(len(kiro_request["conversationState"]["history"]), start_len)
        self.assertIsInstance(body, str)
        self.assertGreater(metrics.tokens, 0)

    def test_map_provider_error_returns_model_not_supported_for_invalid_model_id(self):
        response = _map_provider_error(
            RuntimeError('流式 API 请求失败: 400 {"message":"Invalid model. Please select a different model to continue.","reason":"INVALID_MODEL_ID"}')
        )

        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.body)
        self.assertEqual(payload["error"]["type"], "invalid_request_error")
        self.assertEqual(payload["error"]["message"], "模型不支持，请选择其他模型。")

    def test_validate_outbound_kiro_request_rejects_huge_body(self):
        oversized_content = "x" * (LOCAL_REQUEST_MAX_CHARS + 10)
        kiro_request = {
            "conversationState": {
                "conversationId": "cid",
                "currentMessage": {
                    "userInputMessage": {
                        "content": oversized_content,
                        "modelId": "claude-sonnet-4-6",
                    }
                },
            }
        }
        request_body = '{"conversationState":{"currentMessage":{"userInputMessage":{"content":"%s"}}}}' % oversized_content

        with self.assertRaises(LocalRequestLimitError):
            _validate_outbound_kiro_request(kiro_request, request_body)

    def test_handle_non_stream_request_falls_back_to_json_parser_and_preserves_raw_tool_args(self):
        class _FakeResponse:
            def __init__(self, body: bytes):
                self._body = body

            async def aread(self):
                return self._body

            async def aclose(self):
                return None

        class _FakeProvider:
            async def call_api(self, request_body: str):
                raw = (
                    b'garbage{"content":"hello"}'
                    b'{"name":"bash","toolUseId":"toolu_1"}'
                    b'{"input":"{\\"cmd\\":\\"echo hi\\""}'
                    b'{"stop":true}'
                    b'{"contextUsagePercentage":42.5}'
                )
                return _FakeResponse(raw)

        response = asyncio.run(
            _handle_non_stream_request(_FakeProvider(), "{}", "claude-sonnet-4-5-20250929", 123)
        )
        payload = json.loads(response.body)

        self.assertEqual(payload["content"][0]["text"], "hello")
        self.assertEqual(payload["content"][1]["type"], "tool_use")
        self.assertEqual(
            payload["content"][1]["input"],
            {"raw_arguments": '{"cmd":"echo hi"'},
        )
        self.assertEqual(payload["usage"]["input_tokens"], max(int(42.5 * 200_000 / 100.0) - payload["usage"]["output_tokens"], 0))

    def test_handle_non_stream_request_extracts_bracket_tool_calls_and_deduplicates(self):
        class _FakeResponse:
            def __init__(self, body: bytes):
                self._body = body

            async def aread(self):
                return self._body

            async def aclose(self):
                return None

        class _FakeProvider:
            async def call_api(self, request_body: str):
                text = (
                    '{"content":"before [Called bash with args: {\\"cmd\\": \\"echo hi\\"}] '
                    '[Called bash with args: {\\"cmd\\": \\"echo hi\\"}] after"}'
                ).encode("utf-8")
                return _FakeResponse(text)

        response = asyncio.run(
            _handle_non_stream_request(_FakeProvider(), "{}", "claude-sonnet-4-5-20250929", 55)
        )
        payload = json.loads(response.body)

        self.assertEqual(payload["stop_reason"], "tool_use")
        self.assertEqual(payload["content"][0], {"type": "text", "text": "before after"})
        self.assertEqual(payload["content"][1]["type"], "tool_use")
        self.assertEqual(payload["content"][1]["name"], "bash")
        self.assertEqual(payload["content"][1]["input"], {"cmd": "echo hi"})
        self.assertEqual(len(payload["content"]), 2)


if __name__ == "__main__":
    unittest.main()
