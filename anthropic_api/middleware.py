"""Anthropic API 中间件 - 参考 src/anthropic/middleware.rs"""

import hmac
import logging
from dataclasses import dataclass, field
from typing import Optional

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
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


class AuthMiddleware(BaseHTTPMiddleware):
    """API Key 认证中间件"""

    def __init__(self, app, state: AppState):
        super().__init__(app)
        self.state = state

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # 只拦截 Anthropic API 路径，其他路径直接放行
        if not (path.startswith("/v1/") or path.startswith("/cc/")):
            return await call_next(request)
        key = extract_api_key(dict(request.headers))
        if key and hmac.compare_digest(key, self.state.api_key):
            request.state.app_state = self.state
            return await call_next(request)
        error = ErrorResponse.authentication_error()
        return JSONResponse(status_code=401, content=error.to_dict())


def add_cors_middleware(app):
    """添加 CORS 中间件"""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
