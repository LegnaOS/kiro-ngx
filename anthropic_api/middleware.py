"""Anthropic API 中间件 - 参考 src/anthropic/middleware.rs"""

import hmac
import logging
from dataclasses import dataclass, field
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
        if not key:
            error = ErrorResponse.authentication_error()
            response = JSONResponse(status_code=401, content=error.to_dict())
            await response(scope, receive, send)
            return

        # 管理员 key — 无限制
        if hmac.compare_digest(key, self.state.api_key):
            scope.setdefault("state", {})["app_state"] = self.state
            scope["state"]["api_key_id"] = key  # 管理员也追踪用量
            await self.app(scope, receive, send)
            return

        # 多 key 查找
        from api_keys import get_api_key_manager
        mgr = get_api_key_manager()
        if mgr:
            entry = mgr.lookup(key)
            if entry:
                if not entry.get("enabled", True):
                    response = JSONResponse(status_code=403, content={
                        "type": "error",
                        "error": {"type": "forbidden", "message": "API key is disabled"},
                    })
                    await response(scope, receive, send)
                    return
                allowed, reason = mgr.check_quota(key)
                if not allowed:
                    info = mgr.lookup(key)
                    used = info.get("billedTokens", 0) if info else 0
                    quota = self._effective_quota(mgr, info) if info else 0
                    response = JSONResponse(status_code=429, content={
                        "type": "error",
                        "error": {
                            "type": "rate_limit_error",
                            "message": f"Your API key has exceeded its monthly token quota. Used: {self._fmt(used)}, Limit: {self._fmt(quota)}. Please contact your administrator to increase your quota or wait for the monthly reset.",
                        },
                    })
                    await response(scope, receive, send)
                    return
                scope.setdefault("state", {})["app_state"] = self.state
                scope["state"]["api_key_id"] = key
                await self.app(scope, receive, send)
                return

        error = ErrorResponse.authentication_error()
        response = JSONResponse(status_code=401, content=error.to_dict())
        await response(scope, receive, send)

    @staticmethod
    def _effective_quota(mgr, entry: dict) -> int:
        q = entry.get("monthlyQuota")
        if q is not None:
            return q
        g = mgr.get_groups().get(entry.get("group", ""))
        return g["monthlyQuota"] if g else -1

    @staticmethod
    def _fmt(n: float) -> str:
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}K"
        return str(int(n))


def add_cors_middleware(app):
    """添加 CORS 中间件"""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
