import logging
import unittest

from admin.runtime_log import RuntimeLogBuffer


class RuntimeLogBufferTest(unittest.TestCase):
    def test_tail_and_since_are_bounded(self):
        buf = RuntimeLogBuffer(max_lines=5)
        buf.setFormatter(logging.Formatter("%(message)s"))

        for i in range(8):
            record = logging.LogRecord(
                name="test.runtime",
                level=logging.INFO,
                pathname=__file__,
                lineno=10,
                msg=f"line-{i}",
                args=(),
                exc_info=None,
            )
            buf.emit(record)

        tail = buf.tail(limit=3)
        self.assertEqual([e["message"] for e in tail["entries"]], ["line-5", "line-6", "line-7"])
        self.assertEqual(tail["bufferSize"], 5)

        since = buf.since(cursor=6, limit=10)
        self.assertEqual([e["message"] for e in since["entries"]], ["line-6", "line-7"])
        self.assertEqual(since["nextCursor"], 8)

    def test_filters_apply_without_loading_full_history_to_client(self):
        buf = RuntimeLogBuffer(max_lines=10)
        buf.setFormatter(logging.Formatter("%(message)s"))

        for idx, level in enumerate((logging.INFO, logging.ERROR, logging.WARNING), start=1):
            record = logging.LogRecord(
                name="test.runtime",
                level=level,
                pathname=__file__,
                lineno=idx,
                msg=f"keyword-{idx}",
                args=(),
                exc_info=None,
            )
            buf.emit(record)

        tail = buf.tail(limit=10, level="ERROR", keyword="keyword")
        self.assertEqual(len(tail["entries"]), 1)
        self.assertEqual(tail["entries"][0]["level"], "ERROR")


if __name__ == "__main__":
    unittest.main()
