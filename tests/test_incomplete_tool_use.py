import unittest

from anthropic_api.stream import StreamContext
from kiro.model.events.assistant import AssistantResponseEvent
from kiro.model.events.tool_use import ToolUseEvent


class IncompleteToolUseTest(unittest.TestCase):
    def test_incomplete_tool_use_is_flushed_like_a2_instead_of_raising(self):
        ctx = StreamContext("claude-sonnet-4-5-20250929", 10, False)
        ctx.generate_initial_events()
        ctx.process_kiro_event(AssistantResponseEvent(content="我先看看。"))
        ctx.process_kiro_event(ToolUseEvent(
            name="bash",
            tool_use_id="toolu_123",
            input='{"command":"ls"',
            stop=False,
        ))

        events = ctx.generate_final_events()
        event_names = [evt.event for evt in events]

        self.assertIn("content_block_start", event_names)
        self.assertIn("content_block_delta", event_names)
        self.assertIn("content_block_stop", event_names)
        self.assertIn("message_stop", event_names)


if __name__ == "__main__":
    unittest.main()
