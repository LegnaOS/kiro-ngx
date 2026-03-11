import unittest

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from admin.middleware import AdminAuthMiddleware
from anthropic_api.handlers import _handle_non_stream_request, _handle_stream_request
from anthropic_api.middleware import AppState, AuthMiddleware


class _EmptyAsyncIterator:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _FakeStreamResponse:
    def __init__(self):
        self.closed = False

    def aiter_bytes(self):
        return _EmptyAsyncIterator()

    async def aclose(self):
        self.closed = True


class _FakeNonStreamResponse:
    def __init__(self, body: bytes = b""):
        self._body = body
        self.closed = False

    async def aread(self):
        return self._body

    async def aclose(self):
        self.closed = True


class _FakeProvider:
    def __init__(self, response):
        self.response = response

    async def call_api_stream(self, request_body: str):
        return self.response

    async def call_api(self, request_body: str):
        return self.response


class ProxyPerformanceGuardsTest(unittest.IsolatedAsyncioTestCase):
    async def test_stream_handler_closes_upstream_response(self):
        upstream = _FakeStreamResponse()
        response = await _handle_stream_request(_FakeProvider(upstream), "{}", "claude-sonnet-4-6", 1, False)

        async for _ in response.body_iterator:
            pass

        self.assertTrue(upstream.closed)

    async def test_non_stream_handler_closes_upstream_response(self):
        upstream = _FakeNonStreamResponse()
        response = await _handle_non_stream_request(_FakeProvider(upstream), "{}", "claude-sonnet-4-6", 1)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(upstream.closed)


class LightweightAuthMiddlewareTest(unittest.TestCase):
    def test_anthropic_auth_middleware_accepts_valid_key_and_sets_state(self):
        app = FastAPI()
        app.add_middleware(AuthMiddleware, state=AppState(api_key="secret", profile_arn="arn:test"))

        @app.get("/v1/ping")
        async def ping(request: Request):
            return {"profileArn": request.state.app_state.profile_arn}

        client = TestClient(app)
        response = client.get("/v1/ping", headers={"x-api-key": "secret"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["profileArn"], "arn:test")

    def test_admin_auth_middleware_rejects_invalid_key(self):
        app = FastAPI()
        app.add_middleware(AdminAuthMiddleware, admin_api_key="secret")

        @app.get("/ping")
        async def ping():
            return {"ok": True}

        client = TestClient(app)
        response = client.get("/ping", headers={"x-api-key": "wrong"})

        self.assertEqual(response.status_code, 401)
