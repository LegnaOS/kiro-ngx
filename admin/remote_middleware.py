"""Remote API 认证中间件。"""

import hmac

from fastapi.responses import JSONResponse
from starlette.datastructures import Headers

from admin.types import AdminErrorResponse
from common.auth import extract_api_key


class RemoteAuthMiddleware:
    """Remote API 使用登录密码的 SHA-256 hash 作为认证 token。"""

    def __init__(self, app, remote_api_token: str):
        self.app = app
        self.remote_api_token = remote_api_token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        api_token = extract_api_key(Headers(scope=scope))
        if api_token and hmac.compare_digest(api_token, self.remote_api_token):
            await self.app(scope, receive, send)
            return

        error = AdminErrorResponse.authentication_error()
        response = JSONResponse(
            status_code=401,
            content=error.to_dict(),
        )
        await response(scope, receive, send)
