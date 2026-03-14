import unittest

from anthropic_api.handlers import _KiroFallbackEventParser
from anthropic_api.stream import StreamContext
from kiro.model.events.assistant import AssistantResponseEvent


class StreamFallbackParserTest(unittest.TestCase):
    def test_fallback_parser_parses_content_tool_use_and_context_usage(self):
        parser = _KiroFallbackEventParser()
        ctx = StreamContext("claude-sonnet-4-5-20250929", 123, thinking_enabled=False)

        initial = ctx.generate_initial_events()
        self.assertEqual(initial[0].event, "message_start")

        chunks = [
            b'\x00\x01{"content":"hello "}',
            b'garbage{"content":"world"}{"name":"bash","toolUseId":"toolu_1"}',
            b'{"input":"{\\"cmd\\":\\"echo hi\\""}',
            b'{"stop":true}{"contextUsagePercentage":42.5}',
        ]

        all_sse = []
        for chunk in chunks:
            for event in parser.feed(chunk):
                all_sse.extend(ctx.process_kiro_event(event))

        final_events = ctx.generate_final_events()
        all_sse.extend(final_events)

        names = [evt.event for evt in all_sse]
        self.assertIn("content_block_delta", names)
        self.assertIn("message_delta", names)
        self.assertIn("message_stop", names)

        text_deltas = [
            evt.data["delta"]["text"]
            for evt in all_sse
            if evt.event == "content_block_delta"
            and evt.data.get("delta", {}).get("type") == "text_delta"
        ]
        self.assertEqual("".join(text_deltas), "hello world")

        input_deltas = [
            evt.data["delta"]["partial_json"]
            for evt in all_sse
            if evt.event == "content_block_delta"
            and evt.data.get("delta", {}).get("type") == "input_json_delta"
        ]
        self.assertEqual("".join(input_deltas), '{"cmd":"echo hi"')
        self.assertEqual(ctx.context_total_tokens, int(42.5 * 200_000 / 100.0))
        self.assertEqual(ctx.resolve_input_tokens(), max(ctx.context_total_tokens - ctx.output_tokens, 0))

    def test_fallback_parser_deduplicates_consecutive_content(self):
        parser = _KiroFallbackEventParser()

        events = parser.feed(b'{"content":"same"}{"content":"same"}{"content":"next"}')
        contents = [getattr(event, "content", "") for event in events if hasattr(event, "content")]
        self.assertEqual(contents, ["same", "next"])

    def test_stream_context_deduplicates_consecutive_assistant_content(self):
        ctx = StreamContext("claude-sonnet-4-5-20250929", 12, thinking_enabled=False)
        ctx.generate_initial_events()

        events = []
        events.extend(ctx.process_kiro_event(AssistantResponseEvent(content="same")))
        events.extend(ctx.process_kiro_event(AssistantResponseEvent(content="same")))
        events.extend(ctx.process_kiro_event(AssistantResponseEvent(content="next")))

        text_deltas = [
            evt.data["delta"]["text"]
            for evt in events
            if evt.event == "content_block_delta"
            and evt.data.get("delta", {}).get("type") == "text_delta"
        ]
        self.assertEqual(text_deltas, ["same", "next"])


if __name__ == "__main__":
    unittest.main()
