"""Token 计算模块 - 参考 src/token.rs

计算规则：
- 非西文字符：每个计 4.0 字符单位
- 西文字符：每个计 1.0 字符单位
- 4 字符单位 = 1 token
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from http_client import ProxyConfig, build_sync_client

logger = logging.getLogger(__name__)

_config: Optional["CountTokensConfig"] = None

# 西文字符码点区间（已合并连续区间）
_WESTERN_RANGES = (
    (0x0000, 0x024F),
    (0x1E00, 0x1EFF),
    (0x2C60, 0x2C7F),
    (0xA720, 0xA7FF),
    (0xAB30, 0xAB6F),
)


@dataclass
class CountTokensConfig:
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    auth_type: str = "x-api-key"
    proxy: Optional[ProxyConfig] = None


def init_config(config: CountTokensConfig) -> None:
    global _config
    _config = config


def is_non_western_char(c: str) -> bool:
    """判断字符是否为非西文字符"""
    cp = ord(c)
    # 纯 ASCII 快速路径
    if cp <= 0x7F:
        return False
    for lo, hi in _WESTERN_RANGES:
        if cp <= hi:
            return cp < lo
    return True


def count_tokens(text: str) -> int:
    """计算文本的 token 数量"""
    # 整数累加：非西文 +4，西文 +1，最后整除 4
    units = 0
    for c in text:
        cp = ord(c)
        if cp <= 0x7F:
            units += 1
        else:
            western = False
            for lo, hi in _WESTERN_RANGES:
                if cp <= hi:
                    western = cp >= lo
                    break
            units += 1 if western else 4
    tokens = units // 4

    if tokens < 100:
        acc = tokens * 15 // 10
    elif tokens < 200:
        acc = tokens * 13 // 10
    elif tokens < 300:
        acc = tokens * 125 // 100
    elif tokens < 800:
        acc = tokens * 12 // 10
    else:
        acc = tokens

    return max(acc, 1)


def count_all_tokens(
    model: str,
    system: Optional[List[Dict[str, str]]],
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
) -> int:
    """估算请求的输入 tokens，优先远程 API，回退本地"""
    if _config and _config.api_url:
        try:
            return _call_remote_count_tokens(
                _config.api_url, _config, model, system, messages, tools,
            )
        except Exception as e:
            logger.warning("远程 count_tokens API 调用失败，回退到本地计算: %s", e)

    return _count_all_tokens_local(system, messages, tools)


def _call_remote_count_tokens(
    api_url: str,
    config: CountTokensConfig,
    model: str,
    system: Optional[List[Dict[str, str]]],
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
) -> int:
    client = build_sync_client(config.proxy, timeout_secs=300)
    body: Dict[str, Any] = {"model": model, "messages": messages}
    if system:
        body["system"] = system
    if tools:
        body["tools"] = tools

    headers = {"Content-Type": "application/json"}
    if config.api_key:
        if config.auth_type == "bearer":
            headers["Authorization"] = f"Bearer {config.api_key}"
        else:
            headers["x-api-key"] = config.api_key

    resp = client.post(api_url, json=body, headers=headers)
    resp.raise_for_status()
    return resp.json().get("input_tokens", 1)


def _estimate_schema_tokens(obj: Any) -> int:
    """递归估算 schema 对象的 token 数，避免 json.dumps 开销"""
    if isinstance(obj, str):
        return count_tokens(obj)
    if isinstance(obj, bool):
        return 1
    if isinstance(obj, (int, float)):
        return 1
    if isinstance(obj, dict):
        total = 0
        for k, v in obj.items():
            total += count_tokens(k) + _estimate_schema_tokens(v)
        return total
    if isinstance(obj, list):
        total = 0
        for item in obj:
            total += _estimate_schema_tokens(item)
        return total
    return 1


def _count_all_tokens_local(
    system: Optional[List[Dict[str, str]]],
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
) -> int:
    total = 0
    if system:
        for s in system:
            total += count_tokens(s.get("text", ""))

    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += count_tokens(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and "text" in item:
                    total += count_tokens(item["text"])

    if tools:
        for tool in tools:
            total += count_tokens(tool.get("name", ""))
            total += count_tokens(tool.get("description", ""))
            schema = tool.get("input_schema", {})
            total += _estimate_schema_tokens(schema)

    return max(total, 1)


def estimate_output_tokens(content: List[Dict[str, Any]]) -> int:
    """估算输出 tokens"""
    total = 0
    for block in content:
        text = block.get("text")
        if isinstance(text, str):
            total += count_tokens(text)
        if block.get("type") == "tool_use":
            inp = block.get("input")
            if inp is not None:
                total += count_tokens(json.dumps(inp, ensure_ascii=False))
    return max(total, 1)
