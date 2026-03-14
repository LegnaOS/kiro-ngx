import asyncio
import unittest

from config import Config
from kiro.model.credentials import KiroCredentials
from kiro.token_manager import MultiTokenManager


class TokenManagerModelErrorTest(unittest.TestCase):
    def test_acquire_context_no_longer_hardcodes_model_subscription_compatibility(self):
        manager = MultiTokenManager(
            Config(),
            credentials=[
                KiroCredentials(
                    id=1,
                    refresh_token="r" * 120,
                    access_token="a",
                    expires_at="2099-01-01T00:00:00+00:00",
                    subscription_title="KIRO FREE",
                    disabled=False,
                ),
                KiroCredentials(
                    id=2,
                    refresh_token="r" * 120,
                    access_token="a",
                    expires_at="2099-01-01T00:00:00+00:00",
                    subscription_title="KIRO FREE",
                    disabled=False,
                ),
            ],
        )

        ctx = asyncio.run(manager.acquire_context("claude-opus-4-6"))

        self.assertIn(ctx.id, (1, 2))
        self.assertEqual(ctx.credentials.subscription_title, "KIRO FREE")

    def test_acquire_context_preserves_disabled_error_when_all_disabled(self):
        manager = MultiTokenManager(
            Config(),
            credentials=[
                KiroCredentials(
                    id=1,
                    refresh_token="r" * 120,
                    disabled=True,
                ),
                KiroCredentials(
                    id=2,
                    refresh_token="r" * 120,
                    disabled=True,
                ),
            ],
        )

        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(manager.acquire_context("claude-sonnet-4-5"))

        self.assertEqual(str(ctx.exception), "所有凭据均已禁用（0/2）")

    def test_acquire_context_reports_transient_cooldown_when_all_enabled_credentials_temporarily_unavailable(self):
        manager = MultiTokenManager(
            Config(),
            credentials=[
                KiroCredentials(
                    id=1,
                    refresh_token="r" * 120,
                    access_token="a",
                    expires_at="2099-01-01T00:00:00+00:00",
                    disabled=False,
                ),
            ],
        )

        manager.report_transient_failure(1, cooldown_secs=30)

        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(manager.acquire_context("claude-sonnet-4-5"))

        self.assertIn("当前暂无可用凭据", str(ctx.exception))
        self.assertIn("临时冷却中", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
