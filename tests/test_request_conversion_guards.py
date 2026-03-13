import unittest

import token_counter
from anthropic_api.converter import convert_request
from anthropic_api.handlers import (
    LOCAL_REQUEST_MAX_CHARS,
    LocalRequestLimitError,
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
    def test_convert_request_truncates_large_tool_result(self):
        huge_result = "\n".join(f"line {idx} " + ("x" * 120) for idx in range(600))
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
        self.assertIn("tool_result truncated", tool_text)
        self.assertLess(len(tool_text), len(huge_result))

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


class HandlerPreflightTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
