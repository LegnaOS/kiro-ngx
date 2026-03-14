import unittest

from kiro.provider import KiroProvider


class ProviderCapacityBackoffTest(unittest.TestCase):
    def test_extract_error_reason_from_top_level(self):
        body = '{"message":"I am experiencing high traffic","reason":"INSUFFICIENT_MODEL_CAPACITY"}'
        self.assertEqual(KiroProvider.extract_error_reason(body), "INSUFFICIENT_MODEL_CAPACITY")

    def test_extract_error_reason_from_nested_error(self):
        body = '{"error":{"message":"bad","reason":"INVALID_MODEL_ID"}}'
        self.assertEqual(KiroProvider.extract_error_reason(body), "INVALID_MODEL_ID")

    def test_extract_error_reason_handles_invalid_json(self):
        self.assertIsNone(KiroProvider.extract_error_reason("not-json"))

    def test_capacity_backoff_secs_has_reasonable_floor(self):
        value = KiroProvider.capacity_backoff_secs(0)
        self.assertGreaterEqual(value, 8)
        self.assertLessEqual(value, 27)


if __name__ == "__main__":
    unittest.main()

