"""本地 token 计算模块。

目标：
- 递归覆盖 text / thinking / tool_result / tool_use.input / tools / schema
- 提供比纯字符长度更稳定的本地近似 tokenizer
- 为发送前预检提供 tokens/chars/bytes 统计
"""

import json
import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

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

_CJK_RANGES = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x3040, 0x309F),  # Hiragana
    (0x30A0, 0x30FF),  # Katakana
    (0xAC00, 0xD7AF),  # Hangul
)


@dataclass
class CountTokensConfig:
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    auth_type: str = "x-api-key"
    proxy: Optional[ProxyConfig] = None


@dataclass
class PayloadMetrics:
    tokens: int
    chars: int
    bytes: int


def init_config(config: CountTokensConfig) -> None:
    global _config
    _config = config


def is_non_western_char(c: str) -> bool:
    """判断字符是否为非西文字符。"""
    cp = ord(c)
    if cp <= 0x7F:
        return False
    for lo, hi in _WESTERN_RANGES:
        if cp <= hi:
            return cp < lo
    return True


def _is_cjk_char(c: str) -> bool:
    cp = ord(c)
    for lo, hi in _CJK_RANGES:
        if lo <= cp <= hi:
            return True
    return False


def count_tokens(text: str) -> int:
    """本地近似 tokenizer。

    不是 Claude 精确 tokenizer，但比简单 length/4 更稳：
    - CJK 按更高权重计入
    - 额外参考 UTF-8 字节长度，避免低估大量非 ASCII 内容
    - 对短文本保守上调，减少明显漏算
    """
    if not text:
        return 0

    ascii_chars = 0
    western_chars = 0
    cjk_chars = 0
    other_chars = 0

    for c in text:
        cp = ord(c)
        if cp <= 0x7F:
            ascii_chars += 1
        elif _is_cjk_char(c):
            cjk_chars += 1
        elif is_non_western_char(c):
            other_chars += 1
        else:
            western_chars += 1

    char_based = (ascii_chars / 4.0) + (western_chars / 2.8) + (cjk_chars * 0.95) + (other_chars / 1.8)
    byte_based = len(text.encode("utf-8")) / 3.4
    dense_cjk_based = (ascii_chars + western_chars) / 4.5 + (cjk_chars * 0.85) + (other_chars / 2.0)

    estimate = math.ceil(max(char_based, byte_based, dense_cjk_based))
    if estimate < 32:
        estimate = math.ceil(estimate * 1.15)
    elif estimate < 256:
        estimate = math.ceil(estimate * 1.08)

    return max(estimate, 1)


def count_all_tokens(
    model: str,
    system: Optional[List[Dict[str, Any]]],
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    thinking: Optional[Dict[str, Any]] = None,
    output_config: Optional[Dict[str, Any]] = None,
) -> int:
    """估算 Anthropic 请求的输入 tokens，优先远程 API，回退本地。"""
    if _config and _config.api_url:
        try:
            return _call_remote_count_tokens(
                _config.api_url, _config, model, system, messages, tools,
            )
        except Exception as e:
            logger.warning("远程 count_tokens API 调用失败，回退到本地计算: %s", e)

    return estimate_anthropic_request_metrics(system, messages, tools, thinking, output_config).tokens


def estimate_anthropic_request_metrics(
    system: Optional[List[Dict[str, Any]]],
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    thinking: Optional[Dict[str, Any]] = None,
    output_config: Optional[Dict[str, Any]] = None,
) -> PayloadMetrics:
    """本地估算 Anthropic 风格请求的 tokens/chars/bytes。"""
    segments: List[str] = []
    extra_tokens = 0

    if system:
        for entry in system:
            segments.append(_flatten_content(entry))

    if thinking:
        segments.extend(_render_thinking_segments(thinking, output_config))

    for msg in messages:
        segments.append(_flatten_content(msg.get("content")))

        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "image":
                    extra_tokens += 1600
                elif block_type == "document":
                    source = block.get("source", {})
                    data = source.get("data", "")
                    if isinstance(data, str) and data:
                        extra_tokens += math.ceil((len(data) * 0.75) / 4.0)

    if tools:
        for tool in tools:
            segments.extend(_collect_text_segments(tool))

    base_metrics = estimate_text_metrics(segments)
    return PayloadMetrics(
        tokens=max(base_metrics.tokens + extra_tokens, 1),
        chars=base_metrics.chars,
        bytes=base_metrics.bytes,
    )


def estimate_kiro_payload_metrics(payload: Any) -> PayloadMetrics:
    """估算 Kiro conversationState 请求体的 tokens/chars/bytes。"""
    segments = list(_collect_text_segments(payload))
    metrics = estimate_text_metrics(segments)
    return PayloadMetrics(
        tokens=max(metrics.tokens, 1),
        chars=metrics.chars,
        bytes=metrics.bytes,
    )


def estimate_text_metrics(texts: Iterable[str]) -> PayloadMetrics:
    token_total = 0
    char_total = 0
    byte_total = 0

    for text in texts:
        if not text:
            continue
        token_total += count_tokens(text)
        char_total += len(text)
        byte_total += len(text.encode("utf-8"))

    return PayloadMetrics(
        tokens=max(token_total, 1),
        chars=char_total,
        bytes=byte_total,
    )


def _call_remote_count_tokens(
    api_url: str,
    config: CountTokensConfig,
    model: str,
    system: Optional[List[Dict[str, Any]]],
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
) -> int:
    client = build_sync_client(config.proxy, timeout_secs=300)
    try:
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
    finally:
        client.close()


def _render_thinking_segments(
    thinking: Optional[Dict[str, Any]],
    output_config: Optional[Dict[str, Any]],
) -> List[str]:
    if not thinking:
        return []

    thinking_type = str(thinking.get("type", "")).strip().lower()
    if thinking_type == "enabled":
        budget_tokens = thinking.get("budget_tokens", 20000)
        try:
            budget = int(budget_tokens)
        except (TypeError, ValueError):
            budget = 20000
        budget = max(1024, min(budget, 24576))
        return [
            "<thinking_mode>enabled</thinking_mode>",
            f"<max_thinking_length>{budget}</max_thinking_length>",
        ]

    if thinking_type == "adaptive":
        effort = "high"
        if isinstance(output_config, dict):
            raw_effort = str(output_config.get("effort", "")).strip().lower()
            if raw_effort in {"low", "medium", "high"}:
                effort = raw_effort
        return [
            "<thinking_mode>adaptive</thinking_mode>",
            f"<thinking_effort>{effort}</thinking_effort>",
        ]

    return []


def _flatten_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            parts.append(_flatten_content_block(part))
        return "".join(parts)
    if isinstance(content, dict):
        if "text" in content and isinstance(content.get("text"), str):
            return content.get("text", "")
        if "content" in content:
            return _flatten_content(content.get("content"))
        if "thinking" in content and isinstance(content.get("thinking"), str):
            return content.get("thinking", "")
        return json.dumps(content, ensure_ascii=False, sort_keys=True)
    return str(content)


def _flatten_content_block(block: Any) -> str:
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return str(block) if block is not None else ""

    block_type = block.get("type")
    if block_type == "text":
        return block.get("text", "") or ""
    if block_type == "thinking":
        return block.get("thinking") or block.get("text", "") or ""
    if block_type == "tool_result":
        return _flatten_content(block.get("content"))
    if block_type == "tool_use":
        payload = block.get("input")
        if payload is None:
            return ""
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if block_type == "image":
        return ""
    if block_type == "document":
        source = block.get("source", {})
        if isinstance(source, dict):
            data = source.get("data", "")
            return data if isinstance(data, str) else ""
        return ""
    if "text" in block:
        return block.get("text", "") or ""
    if "content" in block:
        return _flatten_content(block.get("content"))
    return json.dumps(block, ensure_ascii=False, sort_keys=True)


def _collect_text_segments(obj: Any) -> Iterable[str]:
    if obj is None:
        return
    if isinstance(obj, str):
        yield obj
        return
    if isinstance(obj, bool):
        yield "true" if obj else "false"
        return
    if isinstance(obj, (int, float)):
        yield str(obj)
        return
    if isinstance(obj, list):
        for item in obj:
            yield from _collect_text_segments(item)
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield str(key)
            if key == "bytes" and isinstance(value, str):
                # Kiro 图片 bytes 是 base64，按体积给保守 token 估算，避免把原始内容当自然语言逐字分词。
                byte_len = len(value)
                if byte_len:
                    pseudo_tokens = math.ceil((byte_len * 0.75) / 4.0)
                    if pseudo_tokens > 0:
                        yield f"<binary:{pseudo_tokens}>"
                continue
            yield from _collect_text_segments(value)
        return

    yield str(obj)


def estimate_output_tokens(content: List[Dict[str, Any]]) -> int:
    """估算输出 tokens。"""
    metrics = estimate_anthropic_request_metrics(
        system=None,
        messages=[{"role": "assistant", "content": content}],
        tools=None,
    )
    return max(metrics.tokens, 1)
