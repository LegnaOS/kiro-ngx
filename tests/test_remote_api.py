import unittest

from admin.service import AdminService
from admin.types import AddCredentialResponse
from common.auth import sha256_hex
from plugins.remote_api.handlers import _parse_batch_import_request


class _SnapshotEntry:
    def __init__(self, refresh_token_hash=None):
        self.refresh_token_hash = refresh_token_hash


class _Snapshot:
    def __init__(self, total=0, available=0, entries=None):
        self.total = total
        self.available = available
        self.entries = entries or []


class _FakeTokenManager:
    def snapshot(self):
        return _Snapshot(total=3, available=2, entries=[])

    def cache_dir(self):
        return None

    def update_groups(self, groups):
        return None


class _BatchImportService(AdminService):
    def __init__(self):
        super().__init__(_FakeTokenManager())
        self._existing_hashes = {sha256_hex("dup-token")}
        self.added = []
        self.deleted = []
        self.disabled = []
        self.balance_fail_ids = set()

    async def add_credential(self, req):
        if sha256_hex(req.refresh_token) in self._existing_hashes:
            raise AssertionError("duplicate should be filtered before add_credential")
        new_id = len(self.added) + 1
        self.added.append((new_id, req))
        self._existing_hashes.add(sha256_hex(req.refresh_token))
        return AddCredentialResponse(
            success=True,
            message="ok",
            credential_id=new_id,
            email=req.email,
        )

    async def get_balance(self, id, force_refresh=False):
        if id in self.balance_fail_ids:
            raise RuntimeError("balance failed")
        return type("Balance", (), {"current_usage": 1, "usage_limit": 100})()

    def set_disabled(self, id, disabled):
        self.disabled.append((id, disabled))

    def delete_credential(self, id):
        self.deleted.append(id)

    def get_available_credential_counts(self):
        return {"total": 3, "available": 2}

    def _rollback_credential(self, cid):
        self.set_disabled(cid, True)
        self.delete_credential(cid)
        return "success", None

    async def batch_import_credentials(self, req):
        self.token_manager.snapshot = lambda: _Snapshot(
            total=3,
            available=2,
            entries=[_SnapshotEntry(refresh_token_hash=h) for h in self._existing_hashes],
        )
        return await super().batch_import_credentials(req)


class RemoteApiTest(unittest.TestCase):
    def test_sha256_hex(self):
        self.assertEqual(
            sha256_hex("admin"),
            "8c6976e5b5410415bde908bd4dee15dfb167a9c873fc4bb8a81f6f2ab448a918",
        )

    def test_parse_batch_import_request(self):
        payload = _parse_batch_import_request({
            "credentials": [{"refreshToken": "abc"}],
            "skipVerify": False,
            "regions": ["us-east-1"],
        })
        self.assertEqual(len(payload.credentials), 1)
        self.assertEqual(payload.credentials[0].refresh_token, "abc")
        self.assertFalse(payload.skip_verify)
        self.assertEqual(payload.regions, ["us-east-1"])

    def test_parse_batch_import_request_normalizes_single_region_string(self):
        payload = _parse_batch_import_request({
            "credentials": [{"refreshToken": "abc"}],
            "regions": " us-east-1 ",
        })
        self.assertEqual(payload.regions, ["us-east-1"])

    def test_batch_import_skips_duplicates_and_rolls_back_on_verify_failure(self):
        service = _BatchImportService()

        async def run_test():
            req = _parse_batch_import_request({
                "credentials": [
                    {"refreshToken": "dup-token"},
                    {"refreshToken": "new-token-1", "email": "a@test.com"},
                    {"refreshToken": "new-token-2", "email": "b@test.com"},
                ],
                "skipVerify": False,
            })
            service.balance_fail_ids.add(2)
            return await service.batch_import_credentials(req)

        import asyncio
        result = asyncio.run(run_test())

        self.assertEqual(result.duplicate_count, 1)
        self.assertEqual(result.success_count, 1)
        self.assertEqual(result.fail_count, 1)
        self.assertEqual(result.rollback_success_count, 1)
        self.assertEqual(service.disabled, [(2, True)])
        self.assertEqual(service.deleted, [2])


if __name__ == "__main__":
    unittest.main()
