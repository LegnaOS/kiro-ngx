"""认证工具函数 - 参考 src/common/auth.rs"""

import hashlib
from collections.abc import Mapping


def extract_api_key(headers: Mapping[str, str]) -> str | None:
    """从请求头中提取 API Key

    支持两种方式：
    1. x-api-key header
    2. Authorization: Bearer <token>
    """
    # 优先检查 x-api-key
    api_key = headers.get("x-api-key")
    if api_key:
        return api_key

    # 检查 Authorization: Bearer
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()

    return None


def sha256_hex(value: str) -> str:
    """返回字符串的 SHA-256 hex 摘要。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
