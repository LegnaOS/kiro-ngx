"""Anthropic API 中间件 - 参考 src/anthropic/middleware.rs"""

import hmac
import logging
from dataclasses import dataclass
from typing import Optional

from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware

from common.auth import extract_api_key
from .types import ErrorResponse

logger = logging.getLogger(__name__)


@dataclass
class AppState:
    """应用共享状态"""
    api_key: str
    kiro_provider: Optional[object] = None
    profile_arn: Optional[str] = None


class AuthMiddleware:
    """API Key 认证中间件（纯 ASGI，避免 BaseHTTPMiddleware 开销）"""

    def __init__(self, app, state: AppState):
        self.app = app
        self.state = state

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not (path.startswith("/v1/") or path.startswith("/cc/")):
            await self.app(scope, receive, send)
            return
        key = extract_api_key(Headers(scope=scope))
        if key and hmac.compare_digest(key, self.state.api_key):
            scope.setdefault("state", {})["app_state"] = self.state
            await self.app(scope, receive, send)
            return
        error = ErrorResponse.authentication_error()
        response = JSONResponse(status_code=401, content=error.to_dict())
        await response(scope, receive, send)


def add_cors_middleware(app):
    """添加 CORS 中间件"""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
