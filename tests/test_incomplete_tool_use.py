import unittest

from anthropic_api.stream import IncompleteToolUseError, StreamContext
from kiro.model.events.assistant import AssistantResponseEvent
from kiro.model.events.tool_use import ToolUseEvent


class IncompleteToolUseTest(unittest.TestCase):
    def test_incomplete_tool_use_raises_instead_of_silent_end(self):
        ctx = StreamContext("claude-sonnet-4-5-20250929", 10, False)
        ctx.generate_initial_events()
        ctx.process_kiro_event(AssistantResponseEvent(content="我先看看。"))
        ctx.process_kiro_event(ToolUseEvent(
            name="bash",
            tool_use_id="toolu_123",
            input='{"command":"ls"',
            stop=False,
        ))

        with self.assertRaises(IncompleteToolUseError):
            ctx.generate_final_events()


if __name__ == "__main__":
    unittest.main()
