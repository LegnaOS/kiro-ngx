import asyncio
import unittest

from anthropic_api.handlers import _iter_stream_chunks_with_ping


class _FakeChunkIter:
    def __init__(self):
        self._step = 0
        self.cancelled = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            if self._step == 0:
                self._step += 1
                await asyncio.sleep(0.05)
                return b"chunk-1"
            raise StopAsyncIteration
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class _FakeResponse:
    def __init__(self, iterator):
        self._iterator = iterator

    def aiter_bytes(self):
        return self._iterator


class StreamPingWaitTest(unittest.IsolatedAsyncioTestCase):
    async def test_ping_timeout_does_not_cancel_underlying_read(self):
        iterator = _FakeChunkIter()
        response = _FakeResponse(iterator)

        outputs = []
        async for chunk in _iter_stream_chunks_with_ping(response, ping_interval=0.01):
            outputs.append(chunk)

        self.assertIn(None, outputs)
        self.assertIn(b"chunk-1", outputs)
        self.assertFalse(iterator.cancelled)


if __name__ == "__main__":
    unittest.main()
