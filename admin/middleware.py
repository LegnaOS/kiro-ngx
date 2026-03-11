"""Admin API 认证中间件 - 参考 src/admin/middleware.rs"""

import hmac
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers

from common.auth import extract_api_key
from admin.types import AdminErrorResponse


class AdminAuthMiddleware:
    """Admin API Key 验证中间件（纯 ASGI）"""

    def __init__(self, app, admin_api_key: str):
        self.app = app
        self.admin_api_key = admin_api_key

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        api_key = extract_api_key(Headers(scope=scope))
        if api_key and hmac.compare_digest(api_key, self.admin_api_key):
            await self.app(scope, receive, send)
            return
        error = AdminErrorResponse.authentication_error()
        response = JSONResponse(status_code=401, content=error.to_dict())
        await response(scope, receive, send)
