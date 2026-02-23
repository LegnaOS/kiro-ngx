"""Admin API 认证中间件 - 参考 src/admin/middleware.rs"""

import hmac
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from common.auth import extract_api_key
from admin.types import AdminErrorResponse


class AdminAuthMiddleware(BaseHTTPMiddleware):
    """Admin API Key 验证中间件"""

    def __init__(self, app, admin_api_key: str):
        super().__init__(app)
        self.admin_api_key = admin_api_key

    async def dispatch(self, request: Request, call_next):
        api_key = extract_api_key(dict(request.headers))
        if api_key and hmac.compare_digest(api_key, self.admin_api_key):
            return await call_next(request)
        error = AdminErrorResponse.authentication_error()
        return JSONResponse(status_code=401, content=error.to_dict())
