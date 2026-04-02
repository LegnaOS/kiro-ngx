"""Anthropic API Handler 函数 - 参考 src/anthropic/handlers.rs"""

import asyncio
import contextlib
import contextvars
import json
import logging
import re
import uuid
import copy
from typing import Any, Dict, List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

import token_counter as token_module
from .converter import ConversionError, UnsupportedModelError, EmptyMessagesError, convert_request, get_context_window_size
from .middleware import AppState
from .stream import BufferedStreamContext, SseEvent, StreamContext, CONTEXT_WINDOW_SIZE
from .types import (
    CountTokensRequest, CountTokensResponse, ErrorResponse,
    MessagesRequest, Model, ModelsResponse, OutputConfig, Thinking,
)
from . import websearch
from .message_log import get_message_logger
from token_usage import get_token_usage_tracker

logger = logging.getLogger(__name__)

# 当前请求的 API key ID（用于 per-key 额度追踪）
_current_api_key_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("_current_api_key_id", default=None)

PING_INTERVAL_SECS = 15
MAX_STREAM_IDLE_PINGS = 4
STREAM_IDLE_WARN_AFTER_PINGS = 2
LOCAL_CONTEXT_TOKEN_LIMIT = int(CONTEXT_WINDOW_SIZE * 0.92)
LOCAL_REQUEST_MAX_BYTES = 8 * 1024 * 1024
LOCAL_REQUEST_MAX_CHARS = 2_000_000

EMERGENCY_HISTORY_TARGET_TOKENS = 120_000
EMERGENCY_HISTORY_TARGET_BYTES = 360_000
EMERGENCY_HISTORY_TARGET_CHARS = 320_000
EMERGENCY_HISTORY_MIN_MESSAGES = 28
EMERGENCY_HISTORY_DROP_BATCH = 10

# 主动压缩阈值：超过上下文窗口的 60% 即开始主动压缩
PROACTIVE_COMPRESS_RATIO = 0.60
# 主动压缩中保留最近 N 条消息不动（用户体验：最近几轮对话保持完整）
PROACTIVE_RECENT_KEEP = 10
# 主动压缩各级别的 tool result 截断长度
PROACTIVE_TOOL_RESULT_MAX_CHARS = 1500
PROACTIVE_TOOL_RESULT_PLACEHOLDER = "[tool output compressed]"
# 主动压缩 assistant content 截断长度
PROACTIVE_ASSISTANT_MAX_CHARS = 800
FALLBACK_JSON_CANDIDATE_PREFIXES = (
    '{"content":',
    '{"name":',
    '{"followupPrompt":',
    '{"input":',
    '{"stop":',
    '{"contextUsagePercentage":',
)
FALLBACK_BUFFER_LIMIT_CHARS = 256_000
BRACKET_TOOL_CALL_NAME_RE = re.compile(r"\[Called\s+([A-Za-z0-9_\-]+)\s+with\s+args:", re.IGNORECASE)


class LocalRequestLimitError(RuntimeError):
    def __init__(self, message: str, *, error_type: str = "invalid_request_error"):
        super().__init__(message)
        self.error_type = error_type


def configure_request_limits(
    *,
    max_bytes: Optional[int] = None,
    max_chars: Optional[int] = None,
    context_token_limit: Optional[int] = None,
) -> None:
    global LOCAL_REQUEST_MAX_BYTES
    global LOCAL_REQUEST_MAX_CHARS
    global LOCAL_CONTEXT_TOKEN_LIMIT

    if isinstance(max_bytes, int) and max_bytes > 0:
        LOCAL_REQUEST_MAX_BYTES = max_bytes
    if isinstance(max_chars, int) and max_chars > 0:
        LOCAL_REQUEST_MAX_CHARS = max_chars
    if isinstance(context_token_limit, int) and context_token_limit > 0:
        LOCAL_CONTEXT_TOKEN_LIMIT = context_token_limit


def configure_stream_limits(
    *,
    ping_interval_secs: Optional[int] = None,
    max_idle_pings: Optional[int] = None,
    warn_after_idle_pings: Optional[int] = None,
) -> None:
    global PING_INTERVAL_SECS
    global MAX_STREAM_IDLE_PINGS
    global STREAM_IDLE_WARN_AFTER_PINGS

    if isinstance(ping_interval_secs, int) and ping_interval_secs > 0:
        PING_INTERVAL_SECS = ping_interval_secs
    if isinstance(max_idle_pings, int) and max_idle_pings > 0:
        MAX_STREAM_IDLE_PINGS = max_idle_pings
    if isinstance(warn_after_idle_pings, int) and warn_after_idle_pings > 0:
        STREAM_IDLE_WARN_AFTER_PINGS = warn_after_idle_pings


async def _aclose_response_quietly(response) -> None:
    if response is None:
        return
    with contextlib.suppress(Exception):
        await response.aclose()


async def _iter_stream_chunks_with_ping(
    response,
    ping_interval: float,
    max_idle_pings: int = MAX_STREAM_IDLE_PINGS,
    warn_after_idle_pings: int = STREAM_IDLE_WARN_AFTER_PINGS,
):
    """轮询流式分片，超时仅产出 ping 信号，不取消底层读取任务。"""
    chunk_iter = response.aiter_bytes().__aiter__()
    pending_task = asyncio.create_task(chunk_iter.__anext__())
    idle_pings = 0
    try:
        while True:
            done, _ = await asyncio.wait({pending_task}, timeout=ping_interval)
            if not done:
                idle_pings += 1
                if warn_after_idle_pings > 0 and idle_pings >= warn_after_idle_pings:
                    logger.warning(
                        "上游流空闲中：连续 %d 次 ping 周期未收到数据（interval=%ss, timeout=%ss）",
                        idle_pings,
                        ping_interval,
                        ping_interval * max_idle_pings if max_idle_pings > 0 else 0,
                    )
                if max_idle_pings > 0 and idle_pings >= max_idle_pings:
                    raise TimeoutError(
                        f"上游流空闲超时：连续 {idle_pings} 次 ping 周期未收到数据"
                    )
                yield None
                continue
            try:
                chunk = pending_task.result()
            except StopAsyncIteration:
                break
            idle_pings = 0
            yield chunk
            pending_task = asyncio.create_task(chunk_iter.__anext__())
    finally:
        if not pending_task.done():
            pending_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pending_task


def _map_provider_error(err: Exception):
    err_str = str(err)
    if '"reason":"INVALID_MODEL_ID"' in err_str or '"reason": "INVALID_MODEL_ID"' in err_str:
        logger.warning("上游拒绝请求：模型不受支持")
        return JSONResponse(status_code=400, content=ErrorResponse.new(
            "invalid_request_error",
            "模型不支持，请选择其他模型。",
        ).to_dict())
    if "CONTENT_LENGTH_EXCEEDS_THRESHOLD" in err_str:
        logger.warning("上游拒绝请求：请求体超过上游内容阈值")
        return JSONResponse(status_code=400, content=ErrorResponse.new(
            "invalid_request_error",
            "Request payload is too large for the upstream Kiro API. Reduce large tool results, history, images, or tools.",
        ).to_dict())
    if "Input is too long" in err_str:
        logger.warning("上游拒绝请求：上下文输入过长")
        return JSONResponse(status_code=400, content=ErrorResponse.new(
            "invalid_request_error",
            "Conversation context is too long. Reduce message history, system prompt, or tool definitions.",
        ).to_dict())
    logger.error("Kiro API 调用失败: %s", err)
    return JSONResponse(status_code=502, content=ErrorResponse.new(
        "api_error", f"上游 API 调用失败: {err}",
    ).to_dict())


def _validate_outbound_kiro_request(
    kiro_request: Dict[str, Any],
    request_body: str,
    context_token_limit: int = 0,
    *,
    precomputed_metrics: Optional[token_module.PayloadMetrics] = None,
) -> token_module.PayloadMetrics:
    limit = context_token_limit or LOCAL_CONTEXT_TOKEN_LIMIT
    body_chars = len(request_body)
    # 用 char 长度近似 byte 长度（CJK 字符 3 bytes, 但 JSON 结构是 ASCII）
    # 仅在 char 已接近上限时才做精确 encode，避免无意义的 O(n) 分配
    if body_chars > int(LOCAL_REQUEST_MAX_BYTES * 0.8):
        body_bytes = len(request_body.encode("utf-8"))
    else:
        body_bytes = body_chars  # ASCII-dominated JSON, safe lower bound
    metrics = precomputed_metrics or token_module.estimate_kiro_payload_metrics(kiro_request)

    if body_bytes > LOCAL_REQUEST_MAX_BYTES:
        raise LocalRequestLimitError(
            "Request payload is too large before sending. Reduce large tool results, history, images, or tools.",
        )
    if body_chars > LOCAL_REQUEST_MAX_CHARS:
        raise LocalRequestLimitError(
            "Request payload text is too large before sending. Reduce large tool results, history, or system prompt.",
        )
    if metrics.tokens > limit:
        raise LocalRequestLimitError(
            "Estimated conversation context is too large before sending. Reduce message history, system prompt, or tool definitions.",
        )
    return metrics


def _count_tool_results_in_message(message: Dict[str, Any]) -> int:
    ctx = message.get("userInputMessageContext", {})
    tool_results = ctx.get("toolResults", [])
    return len(tool_results) if isinstance(tool_results, list) else 0





def _metrics_still_too_heavy(metrics: token_module.PayloadMetrics, context_token_limit: int = 0) -> bool:
    # 向 A2 靠拢：不再在远低于本地硬上限时触发 emergency history prune。
    # 只保留“已超过本地硬上限”的兜底判断，避免长会话被静默裁断。
    limit = context_token_limit or LOCAL_CONTEXT_TOKEN_LIMIT
    return (
        metrics.tokens > limit
        or metrics.bytes > LOCAL_REQUEST_MAX_BYTES
        or metrics.chars > LOCAL_REQUEST_MAX_CHARS
    )


def _is_user_item(item: Dict[str, Any]) -> bool:
    """判断 history item 是否是 user 消息（含 userInputMessage key）"""
    return "userInputMessage" in item


def _is_assistant_item(item: Dict[str, Any]) -> bool:
    """判断 history item 是否是 assistant 消息（含 assistantResponseMessage key）"""
    return "assistantResponseMessage" in item


def _collect_all_tool_use_ids(history: List[Dict[str, Any]], up_to: int) -> set:
    """收集 history[0:up_to] 中所有 assistant 消息的 toolUseId"""
    ids: set = set()
    for i in range(min(up_to, len(history))):
        ids |= _collect_tool_use_ids_from_item(history[i])
    return ids


def _validate_history_structure(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """验证并修复 history 结构合法性。

    规则：
    1. 必须以 userInputMessage 开头
    2. 严格 user → assistant 交替（Kiro 要求）
    3. user 消息中的 toolResults 引用的 toolUseId 必须存在于前面的 assistant 消息中
    4. 不能有空的 history item（既无 user 也无 assistant key）

    修复策略：遇到违规直接丢弃违规项，而不是尝试修补（保守原则）。
    """
    if not history:
        return history

    validated: List[Dict[str, Any]] = []
    expect_user = True  # 起始必须是 user
    seen_tool_use_ids: set = set()

    for item in history:
        if not isinstance(item, dict):
            continue

        is_user = _is_user_item(item)
        is_asst = _is_assistant_item(item)

        # 跳过既不是 user 也不是 assistant 的垃圾项
        if not is_user and not is_asst:
            logger.warning("history 结构验证: 跳过无效项 (keys=%s)", list(item.keys()))
            continue

        # 检查交替性
        if expect_user and not is_user:
            # 期望 user 但来了 assistant → 跳过这个 assistant
            logger.warning("history 结构验证: 期望 user 但遇到 assistant，跳过")
            continue
        if not expect_user and not is_asst:
            # 期望 assistant 但来了 user → 上一个 user 缺少配对的 assistant
            # 插入一个空 assistant 保持配对
            logger.warning("history 结构验证: 期望 assistant 但遇到 user，插入空 assistant")
            validated.append({"assistantResponseMessage": {"content": " "}})
            seen_tool_use_ids.clear()  # reset for safety
            # 现在 expect_user=True，处理当前 user
            expect_user = True

        if is_user:
            # 清理 orphaned tool_results（引用不存在的 toolUseId）
            user_msg = item.get("userInputMessage", {})
            ctx = user_msg.get("userInputMessageContext") if isinstance(user_msg, dict) else None
            if isinstance(ctx, dict):
                tool_results = ctx.get("toolResults")
                if isinstance(tool_results, list) and tool_results and seen_tool_use_ids:
                    cleaned = [tr for tr in tool_results
                               if not isinstance(tr, dict) or tr.get("toolUseId") in seen_tool_use_ids]
                    if len(cleaned) != len(tool_results):
                        orphaned_count = len(tool_results) - len(cleaned)
                        logger.info("history 结构验证: 清理 %d 个 orphaned tool_results", orphaned_count)
                        ctx["toolResults"] = cleaned
            validated.append(item)
            expect_user = False
        elif is_asst:
            # 收集这个 assistant 的 tool_use_ids
            seen_tool_use_ids |= _collect_tool_use_ids_from_item(item)
            validated.append(item)
            expect_user = True

    # 末尾如果是 user 没有配对的 assistant，也要补一个空 assistant
    if validated and not expect_user:
        # 最后一条是 user，缺 assistant → 但这种情况在 history 中不太正常
        # 直接丢弃末尾这个孤立的 user
        logger.warning("history 结构验证: 末尾 user 缺少 assistant 配对，丢弃")
        validated.pop()

    if len(validated) != len(history):
        logger.warning("history 结构验证: 原始 %d 条 → 修复后 %d 条", len(history), len(validated))

    return validated


def _collect_tool_use_ids_from_item(item: Dict[str, Any]) -> set:
    """从单条 history item 的 assistantResponseMessage 中提取所有 toolUseId"""
    ids: set = set()
    assistant_msg = item.get("assistantResponseMessage")
    if not isinstance(assistant_msg, dict):
        return ids
    tool_uses = assistant_msg.get("toolUses")
    if not isinstance(tool_uses, list):
        return ids
    for tu in tool_uses:
        if isinstance(tu, dict):
            tid = tu.get("toolUseId")
            if tid:
                ids.add(tid)
    return ids


def _strip_orphaned_tool_results(history: List[Dict[str, Any]], orphaned_ids: set) -> int:
    """清理 history 第一条 user 消息中引用已删除 tool_use 的 orphaned tool_results。
    返回被清理的 tool_result 数量。"""
    if not history or not orphaned_ids:
        return 0
    first = history[0]
    user_msg = first.get("userInputMessage")
    if not isinstance(user_msg, dict):
        return 0
    ctx = user_msg.get("userInputMessageContext")
    if not isinstance(ctx, dict):
        return 0
    tool_results = ctx.get("toolResults")
    if not isinstance(tool_results, list) or not tool_results:
        return 0
    cleaned = [tr for tr in tool_results if tr.get("toolUseId") not in orphaned_ids]
    removed = len(tool_results) - len(cleaned)
    if removed > 0:
        ctx["toolResults"] = cleaned
    return removed


def _find_pair_boundary(history: List[Dict[str, Any]], count: int) -> int:
    """找到 history 开头 count 条附近的完整 user+assistant 配对边界。

    从 count 位置向前/后调整，确保切割点落在 user+assistant 配对之间：
    - 切割后 history[boundary:] 必须以 userInputMessage 开头
    - boundary 必须是偶数（完整配对）
    """
    if count <= 0:
        return 0
    boundary = min(count, len(history))
    # 向上对齐到偶数（完整 user+assistant 对）
    if boundary % 2 != 0:
        boundary -= 1
    # 如果切割点后面不是 user 消息，继续前进直到找到 user
    while boundary < len(history) and not _is_user_item(history[boundary]):
        boundary += 1
    return boundary


def _drop_history_head(
    history: List[Dict[str, Any]], drop_count: int,
) -> tuple[int, set]:
    """从 history 头部安全地丢弃 drop_count 条消息。

    返回 (实际丢弃数, orphaned_tool_use_ids)。
    保证丢弃后 history 以 userInputMessage 开头，且配对完整。
    """
    boundary = _find_pair_boundary(history, drop_count)
    if boundary <= 0:
        return 0, set()

    # 收集被丢弃区间内所有 assistant 的 tool_use_id
    orphaned_ids: set = set()
    for i in range(boundary):
        orphaned_ids |= _collect_tool_use_ids_from_item(history[i])

    del history[:boundary]

    # 清理残留的 orphaned tool_results
    _strip_orphaned_tool_results(history, orphaned_ids)

    return boundary, orphaned_ids


def _prune_history_for_capacity(
    kiro_request: Dict[str, Any],
    metrics: token_module.PayloadMetrics,
    context_token_limit: int = 0,
) -> tuple[int, str, token_module.PayloadMetrics]:
    conversation_state = kiro_request.get("conversationState", {})
    history = conversation_state.get("history")
    if not isinstance(history, list):
        body = json.dumps(kiro_request, ensure_ascii=False)
        return 0, body, token_module.estimate_kiro_payload_metrics(kiro_request)

    dropped = 0
    while _metrics_still_too_heavy(metrics, context_token_limit) and len(history) > EMERGENCY_HISTORY_MIN_MESSAGES:
        removable = len(history) - EMERGENCY_HISTORY_MIN_MESSAGES
        drop_now = min(EMERGENCY_HISTORY_DROP_BATCH, removable)
        if drop_now <= 0:
            break

        actual_dropped, _ = _drop_history_head(history, drop_now)
        if actual_dropped == 0:
            break
        dropped += actual_dropped
        metrics = token_module.estimate_kiro_payload_metrics(kiro_request)

    # 最终结构验证
    if dropped > 0:
        validated = _validate_history_structure(history)
        if len(validated) != len(history):
            history.clear()
            history.extend(validated)
            metrics = token_module.estimate_kiro_payload_metrics(kiro_request)
            logger.info("紧急裁剪后结构修复: %d → %d 条", dropped + len(validated), len(validated))

    body = json.dumps(kiro_request, ensure_ascii=False)
    return dropped, body, metrics


def _truncate_text_middle(text: str, max_chars: int, label: str = "content") -> str:
    """截断文本保留头尾，中间插入压缩标记。"""
    if not text or len(text) <= max_chars:
        return text
    half = max(1, max_chars // 2)
    return f"{text[:half]}\n[{label}: {len(text)} chars compressed to {max_chars}]\n{text[-half:]}"


def _compress_tool_results_in_item(item: Dict[str, Any], max_chars: int) -> int:
    """压缩单条 history item 中 user 消息的 tool results，返回压缩的条目数。"""
    user_msg = item.get("userInputMessage")
    if not isinstance(user_msg, dict):
        return 0
    ctx = user_msg.get("userInputMessageContext")
    if not isinstance(ctx, dict):
        return 0
    tool_results = ctx.get("toolResults")
    if not isinstance(tool_results, list):
        return 0
    compressed = 0
    for tr in tool_results:
        content = tr.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text", "")
            if isinstance(text, str) and len(text) > max_chars:
                block["text"] = _truncate_text_middle(text, max_chars, "tool_result")
                compressed += 1
    return compressed


def _compress_assistant_in_item(item: Dict[str, Any], max_chars: int) -> bool:
    """压缩单条 history item 中 assistant 消息内容，返回是否压缩。"""
    assistant_msg = item.get("assistantResponseMessage")
    if not isinstance(assistant_msg, dict):
        return False
    content = assistant_msg.get("content", "")
    if isinstance(content, str) and len(content) > max_chars:
        assistant_msg["content"] = _truncate_text_middle(content, max_chars, "assistant")
        return True
    return False


def _compress_history_proactive(
    kiro_request: Dict[str, Any],
    metrics: token_module.PayloadMetrics,
    context_token_limit: int = 0,
) -> tuple[bool, token_module.PayloadMetrics]:
    """主动压缩 history：在超过 60% 上下文窗口时逐级压缩，避免触发紧急裁剪。

    返回 (是否做了压缩, 更新后的 metrics)。
    """
    limit = context_token_limit or LOCAL_CONTEXT_TOKEN_LIMIT
    threshold = int(limit * PROACTIVE_COMPRESS_RATIO)
    if metrics.tokens <= threshold:
        return False, metrics

    conversation_state = kiro_request.get("conversationState", {})
    history = conversation_state.get("history")
    if not isinstance(history, list) or len(history) <= PROACTIVE_RECENT_KEEP:
        return False, metrics

    # 可压缩的旧消息范围（保留最近 PROACTIVE_RECENT_KEEP 条不动）
    compressible = len(history) - PROACTIVE_RECENT_KEEP
    did_compress = False

    # === Level 1: 压缩旧 history 中的 tool results ===
    tr_compressed = 0
    for i in range(compressible):
        tr_compressed += _compress_tool_results_in_item(history[i], PROACTIVE_TOOL_RESULT_MAX_CHARS)
    if tr_compressed > 0:
        did_compress = True
        metrics = token_module.estimate_kiro_payload_metrics(kiro_request)
        logger.info("主动压缩 Level 1: 压缩 %d 个旧 tool_results (threshold=%d, tokens=%d)",
                     tr_compressed, threshold, metrics.tokens)
        if metrics.tokens <= threshold:
            return True, metrics

    # === Level 2: 压缩旧 history 中的 assistant 内容 ===
    asst_compressed = 0
    for i in range(compressible):
        if _compress_assistant_in_item(history[i], PROACTIVE_ASSISTANT_MAX_CHARS):
            asst_compressed += 1
    if asst_compressed > 0:
        did_compress = True
        metrics = token_module.estimate_kiro_payload_metrics(kiro_request)
        logger.info("主动压缩 Level 2: 压缩 %d 条旧 assistant 消息 (threshold=%d, tokens=%d)",
                     asst_compressed, threshold, metrics.tokens)
        if metrics.tokens <= threshold:
            return True, metrics

    # === Level 3: 丢弃最旧的消息对，使用安全的配对丢弃 ===
    total_dropped = 0
    while metrics.tokens > threshold:
        compressible = len(history) - PROACTIVE_RECENT_KEEP
        if compressible <= 0:
            break
        drop_target = min(compressible, EMERGENCY_HISTORY_DROP_BATCH)
        actual_dropped, _ = _drop_history_head(history, drop_target)
        if actual_dropped == 0:
            break
        total_dropped += actual_dropped
        did_compress = True
        metrics = token_module.estimate_kiro_payload_metrics(kiro_request)

    if total_dropped > 0:
        # 最终结构验证
        validated = _validate_history_structure(history)
        if len(validated) != len(history):
            history.clear()
            history.extend(validated)
            metrics = token_module.estimate_kiro_payload_metrics(kiro_request)
        logger.info("主动压缩 Level 3: 丢弃 %d 条旧 history (threshold=%d, tokens=%d)",
                     total_dropped, threshold, metrics.tokens)

    return did_compress, metrics


def _log_outbound_request_stats(
    *,
    source: str,
    kiro_request: Dict[str, Any],
    metrics: token_module.PayloadMetrics,
    anthropic_message_count: int,
    anthropic_tool_count: int,
) -> None:
    conversation_state = kiro_request.get("conversationState", {})
    history = conversation_state.get("history", [])
    current = conversation_state.get("currentMessage", {}).get("userInputMessage", {})
    current_ctx = current.get("userInputMessageContext", {})
    current_tool_results = current_ctx.get("toolResults", [])
    current_tools = current_ctx.get("tools", [])

    history_tool_results = 0
    for item in history:
        user_message = item.get("userInputMessage", {})
        history_tool_results += _count_tool_results_in_message(user_message)

    logger.info(
        "Outbound Kiro request stats: source=%s anthropic_msgs=%d anthropic_tools=%d history=%d current_tool_results=%d history_tool_results=%d current_tools=%d est_tokens=%d chars=%d bytes=%d",
        source,
        anthropic_message_count,
        anthropic_tool_count,
        len(history),
        len(current_tool_results) if isinstance(current_tool_results, list) else 0,
        history_tool_results,
        len(current_tools) if isinstance(current_tools, list) else 0,
        metrics.tokens,
        metrics.chars,
        metrics.bytes,
    )


def _local_limit_error_response(err: LocalRequestLimitError) -> JSONResponse:
    logger.warning("本地请求预检拒绝发送: %s", err)
    return JSONResponse(
        status_code=400,
        content=ErrorResponse.new(err.error_type, str(err)).to_dict(),
    )


def _make_stream_error_sse(message: str, *, error_type: str = "api_error") -> str:
    return (
        "event: error\n"
        f"data: {json.dumps({'type': 'error', 'error': {'type': error_type, 'message': message}}, ensure_ascii=False)}\n\n"
    )


def _find_fallback_json_start(buffer: str, search_start: int = 0) -> Optional[int]:
    candidates = [
        buffer.find(prefix, search_start)
        for prefix in FALLBACK_JSON_CANDIDATE_PREFIXES
    ]
    candidates = [pos for pos in candidates if pos >= 0]
    if not candidates:
        return None
    return min(candidates)


def _find_fallback_json_end(buffer: str, start: int) -> Optional[int]:
    brace_count = 0
    in_string = False
    escape_next = False

    for i in range(start, len(buffer)):
        ch = buffer[i]

        if escape_next:
            escape_next = False
            continue

        if ch == "\\":
            escape_next = True
            continue

        if ch == '"':
            in_string = not in_string
            continue

        if not in_string:
            if ch == "{":
                brace_count += 1
            elif ch == "}":
                brace_count -= 1
                if brace_count == 0:
                    return i

    return None


def _parse_kiro_json_events_from_buffer(buffer: str) -> tuple[List[Dict[str, Any]], str]:
    events: List[Dict[str, Any]] = []
    search_start = 0
    last_consumed = 0

    while True:
        json_start = _find_fallback_json_start(buffer, search_start)
        if json_start is None:
            break

        json_end = _find_fallback_json_end(buffer, json_start)
        if json_end is None:
            remainder = buffer[json_start:]
            if len(remainder) > FALLBACK_BUFFER_LIMIT_CHARS:
                remainder = remainder[-(FALLBACK_BUFFER_LIMIT_CHARS // 2):]
            return events, remainder

        json_str = buffer[json_start:json_end + 1]
        try:
            parsed = json.loads(json_str)
        except Exception:
            search_start = json_start + 1
            continue

        if isinstance(parsed, dict):
            if parsed.get("content") is not None and not parsed.get("followupPrompt"):
                events.append({"kind": "content", "content": str(parsed.get("content", ""))})
            elif parsed.get("name") and parsed.get("toolUseId"):
                events.append({
                    "kind": "tool_use_start",
                    "name": parsed.get("name", ""),
                    "tool_use_id": parsed.get("toolUseId", ""),
                    "input": parsed.get("input", ""),
                    "stop": bool(parsed.get("stop", False)),
                })
            elif parsed.get("input") is not None and not parsed.get("name"):
                events.append({
                    "kind": "tool_use_input",
                    "input": parsed.get("input", ""),
                })
            elif parsed.get("stop") is not None and parsed.get("contextUsagePercentage") is None:
                events.append({
                    "kind": "tool_use_stop",
                    "stop": bool(parsed.get("stop", False)),
                })
            elif parsed.get("contextUsagePercentage") is not None:
                events.append({
                    "kind": "context_usage",
                    "context_usage_percentage": parsed.get("contextUsagePercentage", 0.0),
                })

        search_start = json_end + 1
        last_consumed = search_start

    remainder = buffer[last_consumed:] if last_consumed > 0 else buffer
    if len(remainder) > FALLBACK_BUFFER_LIMIT_CHARS:
        remainder = remainder[-(FALLBACK_BUFFER_LIMIT_CHARS // 2):]
    return events, remainder


def _find_matching_square_bracket(text: str, start: int) -> int:
    depth = 0
    in_string = False
    escape_next = False

    for idx in range(start, len(text)):
        ch = text[idx]

        if escape_next:
            escape_next = False
            continue

        if ch == "\\":
            escape_next = True
            continue

        if ch == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return idx

    return -1


def _parse_single_bracket_tool_call(tool_call_text: str) -> Optional[Dict[str, Any]]:
    name_match = BRACKET_TOOL_CALL_NAME_RE.search(tool_call_text)
    if not name_match:
        return None

    function_name = name_match.group(1).strip()
    args_marker = "with args:"
    marker_pos = tool_call_text.lower().find(args_marker)
    if marker_pos < 0:
        return None

    args_start = marker_pos + len(args_marker)
    args_end = tool_call_text.rfind("]")
    if args_end <= args_start:
        return None

    json_candidate = tool_call_text[args_start:args_end].strip()
    if not json_candidate:
        return None

    try:
        input_obj = json.loads(json_candidate)
        if not isinstance(input_obj, dict):
            input_obj = {"raw_arguments": json_candidate}
    except Exception:
        input_obj = {"raw_arguments": json_candidate}

    return {
        "type": "tool_use",
        "id": f"toolu_bracket_{uuid.uuid4().hex[:12]}",
        "name": function_name,
        "input": input_obj,
    }


def _normalize_tool_use_key(tool_use: Dict[str, Any]) -> str:
    input_obj = tool_use.get("input", {})
    try:
        normalized_input = json.dumps(input_obj, ensure_ascii=False, sort_keys=True)
    except Exception:
        normalized_input = str(input_obj)
    return f"{tool_use.get('name', '')}:{normalized_input}"


def _deduplicate_tool_uses(tool_uses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    unique: List[Dict[str, Any]] = []
    for tool_use in tool_uses:
        key = _normalize_tool_use_key(tool_use)
        if key in seen:
            continue
        seen.add(key)
        unique.append(tool_use)
    return unique


def _extract_bracket_tool_calls(response_text: str) -> tuple[List[Dict[str, Any]], str]:
    if not response_text or "[Called" not in response_text:
        return [], response_text

    parsed_calls: List[Dict[str, Any]] = []
    removal_ranges: List[tuple[int, int]] = []
    search_from = 0

    while True:
        start_pos = response_text.find("[Called", search_from)
        if start_pos < 0:
            break

        end_pos = _find_matching_square_bracket(response_text, start_pos)
        if end_pos < 0:
            break

        segment = response_text[start_pos:end_pos + 1]
        parsed = _parse_single_bracket_tool_call(segment)
        if parsed:
            parsed_calls.append(parsed)
            removal_ranges.append((start_pos, end_pos + 1))

        search_from = end_pos + 1

    if not removal_ranges:
        return [], response_text

    chunks: List[str] = []
    cursor = 0
    for start_pos, end_pos in removal_ranges:
        if cursor < start_pos:
            chunks.append(response_text[cursor:start_pos])
        cursor = end_pos
    if cursor < len(response_text):
        chunks.append(response_text[cursor:])

    cleaned = "".join(chunks)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return _deduplicate_tool_uses(parsed_calls), cleaned


class _KiroFallbackEventParser:
    def __init__(self):
        self._buffer = ""
        self._current_tool_use_id: Optional[str] = None
        self._current_tool_name: str = ""
        self._last_content_event: Optional[str] = None
        self._last_emitted_kind: Optional[str] = None

    def reset(self) -> None:
        self._buffer = ""
        self._current_tool_use_id = None
        self._current_tool_name = ""
        self._last_content_event = None
        self._last_emitted_kind = None

    def feed(self, chunk: bytes) -> List[Any]:
        from kiro.model.events.assistant import AssistantResponseEvent
        from kiro.model.events.context_usage import ContextUsageEvent
        from kiro.model.events.tool_use import ToolUseEvent

        text = chunk.decode("utf-8", errors="ignore")
        if not text:
            return []

        self._buffer += text
        if len(self._buffer) > FALLBACK_BUFFER_LIMIT_CHARS:
            self._buffer = self._buffer[-(FALLBACK_BUFFER_LIMIT_CHARS // 2):]

        raw_events, remaining = _parse_kiro_json_events_from_buffer(self._buffer)
        self._buffer = remaining

        parsed_events: List[Any] = []
        for raw in raw_events:
            kind = raw.get("kind")
            if kind == "content":
                content = raw.get("content", "")
                if (
                    self._last_emitted_kind == "content"
                    and self._last_content_event == content
                ):
                    continue
                self._last_content_event = content
                self._last_emitted_kind = "content"
                parsed_events.append(AssistantResponseEvent(content=content))
            elif kind == "tool_use_start":
                tool_use_id = raw.get("tool_use_id", "")
                name = raw.get("name", "")
                self._current_tool_use_id = tool_use_id or self._current_tool_use_id
                self._current_tool_name = name or self._current_tool_name
                self._last_emitted_kind = "tool_use"
                parsed_events.append(ToolUseEvent(
                    name=self._current_tool_name,
                    tool_use_id=self._current_tool_use_id or "",
                    input=raw.get("input", ""),
                    stop=bool(raw.get("stop", False)),
                ))
                if raw.get("stop"):
                    self._current_tool_use_id = None
                    self._current_tool_name = ""
            elif kind == "tool_use_input":
                if not self._current_tool_use_id:
                    continue
                self._last_emitted_kind = "tool_use"
                parsed_events.append(ToolUseEvent(
                    name=self._current_tool_name,
                    tool_use_id=self._current_tool_use_id,
                    input=raw.get("input", ""),
                    stop=False,
                ))
            elif kind == "tool_use_stop":
                if not self._current_tool_use_id:
                    continue
                self._last_emitted_kind = "tool_use"
                parsed_events.append(ToolUseEvent(
                    name=self._current_tool_name,
                    tool_use_id=self._current_tool_use_id,
                    input="",
                    stop=bool(raw.get("stop", False)),
                ))
                if raw.get("stop"):
                    self._current_tool_use_id = None
                    self._current_tool_name = ""
            elif kind == "context_usage":
                self._last_emitted_kind = "context_usage"
                parsed_events.append(ContextUsageEvent(
                    context_usage_percentage=float(raw.get("context_usage_percentage", 0.0)),
                ))

        return parsed_events


def _report_token_usage(model: str, input_tokens: int, output_tokens: int):
    """向 TokenUsageTracker 上报一次请求的 token 用量（模型名归一化为 Kiro ID），同时上报到 API Key 额度"""
    from anthropic_api.converter import map_model
    tracker = get_token_usage_tracker()
    if tracker:
        tracker.report(map_model(model) or model, input_tokens, output_tokens)
    api_key_id = _current_api_key_id.get()
    if api_key_id:
        from api_keys import get_api_key_manager
        mgr = get_api_key_manager()
        if mgr:
            mgr.report_usage(api_key_id, input_tokens, output_tokens, model=map_model(model) or model)


# === 模型列表 ===

MODELS = [
    Model(id="claude-sonnet-4-5-20250929", object="model", created=1727568000,
          owned_by="anthropic", display_name="Claude Sonnet 4.5", type="chat", max_tokens=32000),
    Model(id="claude-sonnet-4-5-20250929-thinking", object="model", created=1727568000,
          owned_by="anthropic", display_name="Claude Sonnet 4.5 (Thinking)", type="chat", max_tokens=32000),
    Model(id="claude-opus-4-5-20251101", object="model", created=1730419200,
          owned_by="anthropic", display_name="Claude Opus 4.5", type="chat", max_tokens=32000),
    Model(id="claude-opus-4-5-20251101-thinking", object="model", created=1730419200,
          owned_by="anthropic", display_name="Claude Opus 4.5 (Thinking)", type="chat", max_tokens=32000),
    Model(id="claude-sonnet-4-6", object="model", created=1770314400,
          owned_by="anthropic", display_name="Claude Sonnet 4.6", type="chat", max_tokens=32000),
    Model(id="claude-sonnet-4-6-thinking", object="model", created=1770314400,
          owned_by="anthropic", display_name="Claude Sonnet 4.6 (Thinking)", type="chat", max_tokens=32000),
    Model(id="claude-opus-4-6", object="model", created=1770314400,
          owned_by="anthropic", display_name="Claude Opus 4.6", type="chat", max_tokens=32000),
    Model(id="claude-opus-4-6-thinking", object="model", created=1770314400,
          owned_by="anthropic", display_name="Claude Opus 4.6 (Thinking)", type="chat", max_tokens=32000),
    Model(id="claude-haiku-4-5-20251001", object="model", created=1727740800,
          owned_by="anthropic", display_name="Claude Haiku 4.5", type="chat", max_tokens=32000),
    Model(id="claude-haiku-4-5-20251001-thinking", object="model", created=1727740800,
          owned_by="anthropic", display_name="Claude Haiku 4.5 (Thinking)", type="chat", max_tokens=32000),
]


async def get_models(request: Request):
    logger.info("Received GET /v1/models request")
    all_models = list(MODELS)
    # 动态追加自定义模型（来自 admin 路由配置）
    admin_svc = getattr(request.app.state, "admin_service", None)
    if admin_svc:
        builtin_ids = {m.id for m in MODELS}
        for mid in admin_svc.get_custom_models():
            if mid not in builtin_ids:
                all_models.append(Model(
                    id=mid, object="model", created=0,
                    owned_by="custom", display_name=mid,
                    type="chat", max_tokens=32000,
                ))
    return JSONResponse(content=ModelsResponse(data=all_models).to_dict())


def _override_thinking_from_model_name(payload: MessagesRequest) -> None:
    model_lower = payload.model.lower()
    if "thinking" not in model_lower:
        return
    # Claude Code 源码: modelSupportsAdaptiveThinking 对 opus-4-6 和 sonnet-4-6 都返回 true
    is_4_6 = "4-6" in model_lower or "4.6" in model_lower
    thinking_type = "adaptive" if is_4_6 else "enabled"
    logger.info("模型名包含 thinking 后缀，覆写 thinking 配置: type=%s, is_4_6=%s", thinking_type, is_4_6)
    payload.thinking = {"type": thinking_type, "budget_tokens": 20000}
    if is_4_6:
        payload.output_config = {"effort": "high"}


async def _handle_stream_request(provider, request_body: str, model: str, input_tokens: int, thinking_enabled: bool,
                                 tool_name_map: Optional[Dict[str, str]] = None):
    """处理流式请求"""
    try:
        response = await provider.call_api_stream(request_body)
    except Exception as e:
        return _map_provider_error(e)

    ctx = StreamContext(model, input_tokens, thinking_enabled, tool_name_map)
    initial_events = ctx.generate_initial_events()

    async def event_generator():
        from kiro.parser.decoder import EventStreamDecoder
        from kiro.parser.error import BufferOverflow

        current_response = response
        try:
            ping_event = 'event: ping\ndata: {"type": "ping"}\n\n'
            stream_started = False
            early_retry_attempts = 1
            while True:
                attempt_response = current_response
                decoder = EventStreamDecoder()
                fallback_parser = _KiroFallbackEventParser()
                fallback_mode = False
                strict_no_output_chunks = 0
                try:
                    async for chunk in _iter_stream_chunks_with_ping(attempt_response, PING_INTERVAL_SECS):
                        if chunk is None:
                            if stream_started:
                                yield ping_event
                            continue
                        parsed_events: List[Any] = []
                        if fallback_mode:
                            parsed_events = fallback_parser.feed(chunk)
                        else:
                            try:
                                decoder.feed(chunk)
                            except BufferOverflow as e:
                                logger.warning("缓冲区溢出: %s", e)
                                fallback_parser.reset()
                                strict_no_output_chunks = 0
                                continue
                            for frame in decoder.decode_all():
                                event = _parse_event(frame)
                                if event is not None:
                                    parsed_events.append(event)

                            if parsed_events:
                                strict_no_output_chunks = 0
                                fallback_parser.reset()
                            else:
                                strict_no_output_chunks += 1
                                fallback_events = fallback_parser.feed(chunk)
                                if fallback_events:
                                    fallback_mode = True
                                    parsed_events = fallback_events
                                    logger.warning(
                                        "严格事件解码连续 %d 个 chunk 无产出，切换到 Kiro JSON fallback 解析",
                                        strict_no_output_chunks,
                                    )

                        if parsed_events and not stream_started:
                            stream_started = True
                            for evt in initial_events:
                                yield evt.to_sse_string()

                        for event in parsed_events:
                            for sse in ctx.process_kiro_event(event):
                                yield sse.to_sse_string()
                    break
                except Exception as e:
                    await _aclose_response_quietly(current_response)
                    if not stream_started and early_retry_attempts > 0:
                        early_retry_attempts -= 1
                        logger.warning("首个有效事件前读取响应流失败，尝试重新建立流: %s", e)
                        try:
                            current_response = await provider.call_api_stream(request_body)
                        except Exception as retry_error:
                            logger.error("重新建立上游流失败: %s", retry_error)
                            yield _make_stream_error_sse(f"重新建立上游流失败: {retry_error}")
                            return
                        continue
                    logger.error("读取响应流失败: %s", e)
                    yield _make_stream_error_sse(f"读取上游流失败: {e}")
                    return

            if not stream_started:
                for evt in initial_events:
                    yield evt.to_sse_string()
            for sse in ctx.generate_final_events():
                yield sse.to_sse_string()

            msg_logger = get_message_logger()
            if msg_logger and msg_logger.enabled:
                final_input = ctx.resolve_input_tokens()
                msg_logger.log_stream_text(
                    model=model, text=ctx.accumulated_text,
                    stop_reason=ctx.state_manager.get_stop_reason(),
                    usage={"input_tokens": final_input, "output_tokens": ctx.output_tokens},
                )

            _report_token_usage(model, ctx.resolve_input_tokens(), ctx.output_tokens)
        finally:
            await _aclose_response_quietly(current_response)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


async def _handle_stream_request_buffered(provider, request_body: str, model: str, estimated_input_tokens: int, thinking_enabled: bool,
                                          tool_name_map: Optional[Dict[str, str]] = None):
    """处理缓冲流式请求（/cc/v1/messages）"""
    try:
        response = await provider.call_api_stream(request_body)
    except Exception as e:
        return _map_provider_error(e)

    buf_ctx = BufferedStreamContext(model, estimated_input_tokens, thinking_enabled, tool_name_map)

    async def event_generator():
        from kiro.parser.decoder import EventStreamDecoder
        from kiro.parser.error import BufferOverflow

        current_response = response
        try:
            ping_event = 'event: ping\ndata: {"type": "ping"}\n\n'
            early_retry_attempts = 1
            processed_any_events = False
            while True:
                decoder = EventStreamDecoder()
                fallback_parser = _KiroFallbackEventParser()
                fallback_mode = False
                strict_no_output_chunks = 0
                try:
                    async for chunk in _iter_stream_chunks_with_ping(current_response, PING_INTERVAL_SECS):
                        if chunk is None:
                            yield ping_event
                            continue
                        parsed_events: List[Any] = []
                        if fallback_mode:
                            parsed_events = fallback_parser.feed(chunk)
                        else:
                            try:
                                decoder.feed(chunk)
                            except BufferOverflow as e:
                                logger.warning("缓冲区溢出: %s", e)
                                fallback_parser.reset()
                                strict_no_output_chunks = 0
                                continue
                            for frame in decoder.decode_all():
                                event = _parse_event(frame)
                                if event is not None:
                                    parsed_events.append(event)

                            if parsed_events:
                                strict_no_output_chunks = 0
                                fallback_parser.reset()
                            else:
                                strict_no_output_chunks += 1
                                fallback_events = fallback_parser.feed(chunk)
                                if fallback_events:
                                    fallback_mode = True
                                    parsed_events = fallback_events
                                    logger.warning(
                                        "严格事件解码连续 %d 个 chunk 无产出，切换到 Kiro JSON fallback 解析",
                                        strict_no_output_chunks,
                                    )

                        if parsed_events:
                            processed_any_events = True
                        for event in parsed_events:
                            buf_ctx.process_and_buffer(event)
                    break
                except Exception as e:
                    await _aclose_response_quietly(current_response)
                    if not processed_any_events and early_retry_attempts > 0:
                        early_retry_attempts -= 1
                        logger.warning("缓冲流在首个有效事件前读取失败，尝试重新建立流: %s", e)
                        try:
                            current_response = await provider.call_api_stream(request_body)
                        except Exception as retry_error:
                            logger.error("重新建立缓冲上游流失败: %s", retry_error)
                            yield _make_stream_error_sse(f"重新建立上游流失败: {retry_error}")
                            return
                        continue
                    logger.error("读取响应流失败: %s", e)
                    yield _make_stream_error_sse(f"读取上游流失败: {e}")
                    return

            for sse in buf_ctx.finish_and_get_all_events():
                yield sse.to_sse_string()

            msg_logger = get_message_logger()
            if msg_logger and msg_logger.enabled:
                inner = buf_ctx.inner
                final_input = inner.resolve_input_tokens()
                msg_logger.log_stream_text(
                    model=model, text=inner.accumulated_text,
                    stop_reason=inner.state_manager.get_stop_reason(),
                    usage={"input_tokens": final_input, "output_tokens": inner.output_tokens},
                )

            inner = buf_ctx.inner
            _report_token_usage(model, inner.resolve_input_tokens(), inner.output_tokens)
        finally:
            await _aclose_response_quietly(current_response)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


def _parse_event(frame):
    """从解码帧解析事件对象"""
    from kiro.parser.frame import Frame
    from kiro.model.events.base import EventType
    from kiro.model.events.assistant import AssistantResponseEvent
    from kiro.model.events.tool_use import ToolUseEvent
    from kiro.model.events.context_usage import ContextUsageEvent

    if not isinstance(frame, Frame):
        return None

    event_type = frame.event_type() or ""
    try:
        data = frame.payload_as_json() if frame.payload else {}
    except Exception:
        # Rust 版在 payload 解析失败时会跳过整个事件，Python 版也应如此
        logger.warning("帧 payload JSON 解析失败 (event_type=%s), 跳过", event_type)
        return None

    if not isinstance(data, dict):
        logger.warning("帧 payload 不是 dict (event_type=%s, type=%s), 跳过",
                       event_type, type(data).__name__)
        return None

    et = EventType.from_str(event_type)
    if et == EventType.ASSISTANT_RESPONSE:
        return AssistantResponseEvent.from_dict(data)
    elif et == EventType.TOOL_USE:
        evt = ToolUseEvent.from_dict(data)
        # 有效的 ToolUseEvent 必须有 tool_use_id
        if not evt.tool_use_id:
            logger.warning("ToolUseEvent 缺少 toolUseId, 跳过: %s", repr(data)[:300])
            return None
        return evt
    elif et == EventType.CONTEXT_USAGE:
        return ContextUsageEvent.from_dict(data)
    elif event_type == "exception":
        return {"type": "exception", "exception_type": data.get("exceptionType", "")}
    return None


async def _handle_non_stream_request(provider, request_body: str, model: str, input_tokens: int):
    """处理非流式请求"""
    from kiro.parser.decoder import EventStreamDecoder
    from kiro.model.events.assistant import AssistantResponseEvent
    from kiro.model.events.tool_use import ToolUseEvent
    from kiro.model.events.context_usage import ContextUsageEvent

    try:
        response = await provider.call_api(request_body)
    except Exception as e:
        return _map_provider_error(e)

    try:
        body_bytes = await response.aread()
    except Exception as e:
        logger.error("读取响应失败: %s", e)
        return JSONResponse(status_code=502, content=ErrorResponse.new(
            "api_error", f"读取上游响应失败: {e}",
        ).to_dict())
    finally:
        await _aclose_response_quietly(response)
    decoder = EventStreamDecoder()
    decoder.feed(body_bytes)

    text_parts: List[str] = []
    tool_uses: List[Dict[str, Any]] = []
    has_tool_use = False
    stop_reason = "end_turn"
    context_total_tokens: Optional[int] = None
    tool_json_parts: Dict[str, List[str]] = {}

    parsed_events: List[Any] = []
    for frame in decoder.decode_all():
        event = _parse_event(frame)
        if event is not None:
            parsed_events.append(event)

    if not parsed_events:
        fallback_parser = _KiroFallbackEventParser()
        parsed_events = fallback_parser.feed(body_bytes)
        if parsed_events:
            logger.warning("非流式严格事件解码无产出，切换到 Kiro JSON fallback 解析")

    for event in parsed_events:
        if isinstance(event, AssistantResponseEvent):
            text_parts.append(event.content)
        elif isinstance(event, ToolUseEvent):
            has_tool_use = True
            if not isinstance(event.input, str):
                logger.warning("非流式 ToolUseEvent.input 类型异常: id=%s, type=%s",
                               event.tool_use_id, type(event.input).__name__)
                continue
            tool_json_parts.setdefault(event.tool_use_id, []).append(event.input)
            if event.stop:
                buf = "".join(tool_json_parts.get(event.tool_use_id, []))
                if not buf:
                    logger.warning("ToolUseEvent JSON 缓冲区为空: id=%s, name=%s",
                                   event.tool_use_id, event.name)
                try:
                    inp = json.loads(buf) if buf else {}
                except json.JSONDecodeError:
                    logger.error("ToolUseEvent JSON 解析失败: id=%s, name=%s, buf=%s",
                                 event.tool_use_id, event.name, repr(buf)[:500])
                    inp = {"raw_arguments": buf} if buf else {}
                tool_uses.append({"type": "tool_use", "id": event.tool_use_id, "name": event.name, "input": inp})
        elif isinstance(event, ContextUsageEvent):
            actual = int(event.context_usage_percentage * get_context_window_size(model) / 100.0)
            context_total_tokens = actual
            if event.context_usage_percentage >= 100.0:
                stop_reason = "model_context_window_exceeded"
        elif isinstance(event, dict) and event.get("type") == "exception":
            if event.get("exception_type") == "ContentLengthExceededException":
                stop_reason = "max_tokens"

    if has_tool_use and stop_reason == "end_turn":
        stop_reason = "tool_use"

    content: List[Dict[str, Any]] = []
    text_content = "".join(text_parts)
    bracket_tool_uses, text_content = _extract_bracket_tool_calls(text_content)
    if bracket_tool_uses:
        tool_uses.extend(bracket_tool_uses)
        tool_uses = _deduplicate_tool_uses(tool_uses)
        has_tool_use = True
        if stop_reason == "end_turn":
            stop_reason = "tool_use"
    if text_content:
        content.append({"type": "text", "text": text_content})
    content.extend(tool_uses)

    output_tokens = token_module.estimate_output_tokens(content)
    final_input = max(context_total_tokens - output_tokens, 0) if context_total_tokens is not None else input_tokens

    # 记录响应日志
    msg_logger = get_message_logger()
    if msg_logger and msg_logger.enabled:
        msg_logger.log_response(
            model=model, content=content,
            stop_reason=stop_reason,
            usage={"input_tokens": final_input, "output_tokens": output_tokens},
        )

    # 上报 token 用量
    _report_token_usage(model, final_input, output_tokens)

    return JSONResponse(content={
        "id": f"msg_{uuid.uuid4().hex}", "type": "message", "role": "assistant",
        "content": content, "model": model,
        "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": final_input, "output_tokens": output_tokens,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    })


async def _process_messages_common(state: AppState, payload: MessagesRequest, use_buffered: bool):
    """post_messages 和 post_messages_cc 的公共逻辑"""
    provider = state.kiro_provider
    if not provider:
        logger.error("KiroProvider 未配置")
        return JSONResponse(status_code=503, content=ErrorResponse.new(
            "service_unavailable", "Kiro API provider not configured",
        ).to_dict())

    _override_thinking_from_model_name(payload)

    # 记录请求日志
    msg_logger = get_message_logger()
    if msg_logger and msg_logger.enabled:
        msg_logger.log_request(
            model=payload.model,
            messages=payload.messages,
            system=payload.system,
            tools=payload.tools,
            stream=payload.stream,
        )

    if websearch.is_pure_websearch_request(payload):
        logger.info("检测到纯 WebSearch 请求，路由到 WebSearch 处理")
        input_tokens = token_module.estimate_anthropic_request_metrics(
            payload.system, payload.messages, payload.tools,
            payload.thinking, payload.output_config,
        ).tokens
        return await websearch.handle_websearch_request(provider, payload, input_tokens)

    # 仅在客户端发送 web_search 时走 auto-continue
    has_ws = websearch.has_web_search_tool(payload)

    try:
        result = convert_request(payload)
    except UnsupportedModelError as e:
        return JSONResponse(status_code=400, content=ErrorResponse.new(
            "invalid_request_error", f"模型不支持: {e.model}",
        ).to_dict())
    except EmptyMessagesError:
        return JSONResponse(status_code=400, content=ErrorResponse.new(
            "invalid_request_error", "消息列表为空",
        ).to_dict())

    kiro_request = {"conversationState": result.conversation_state.to_dict()}
    if state.profile_arn:
        kiro_request["profileArn"] = state.profile_arn

    # 根据模型动态计算上下文 token 上限
    model_ctx_limit = int(get_context_window_size(payload.model) * 0.92)

    # 先计算 metrics，主动压缩 → 紧急裁剪 → 最终校验
    outbound_metrics = token_module.estimate_kiro_payload_metrics(kiro_request)
    source_tag = "initial"

    # Step 1: 主动压缩（每次请求都检查，超过 60% 即开始压缩旧消息）
    compressed, outbound_metrics = _compress_history_proactive(kiro_request, outbound_metrics, model_ctx_limit)
    if compressed:
        source_tag = "initial_compressed"

    # Step 2: 紧急裁剪（仍然超过硬上限时才触发）
    if _metrics_still_too_heavy(outbound_metrics, model_ctx_limit):
        dropped, request_body, outbound_metrics = _prune_history_for_capacity(kiro_request, outbound_metrics, model_ctx_limit)
        if dropped > 0:
            logger.warning(
                "请求超限，自动裁剪旧 history %d 条: tokens=%d chars=%d bytes=%d",
                dropped, outbound_metrics.tokens, outbound_metrics.chars, outbound_metrics.bytes,
            )
            source_tag = "initial_pruned"
    else:
        request_body = json.dumps(kiro_request, ensure_ascii=False)
    try:
        outbound_metrics = _validate_outbound_kiro_request(
            kiro_request, request_body, model_ctx_limit,
            precomputed_metrics=outbound_metrics,
        )
    except LocalRequestLimitError as e:
        return _local_limit_error_response(e)
    _log_outbound_request_stats(
        source=source_tag,
        kiro_request=kiro_request,
        metrics=outbound_metrics,
        anthropic_message_count=len(payload.messages),
        anthropic_tool_count=len(payload.tools or []),
    )
    # 直接用本地 Kiro payload 估算的 tokens 作为 input_tokens，
    # 避免阻塞在远程 count_tokens API（300s 超时）上拖垮 TTFB。
    input_tokens = outbound_metrics.tokens
    thinking = payload.get_thinking()
    thinking_enabled = thinking.is_enabled() if thinking else False

    tool_name_map = result.tool_name_map or None

    if payload.stream:
        # 有 web_search 时走流式 auto-continue
        if has_ws:
            return await _handle_stream_auto_continue(
                provider, state, payload, request_body, payload.model,
                input_tokens, thinking_enabled,
            )
        if use_buffered:
            return await _handle_stream_request_buffered(provider, request_body, payload.model, input_tokens, thinking_enabled, tool_name_map)
        return await _handle_stream_request(provider, request_body, payload.model, input_tokens, thinking_enabled, tool_name_map)
    return await _handle_non_stream_request(provider, request_body, payload.model, input_tokens)


async def post_messages(request: Request, payload: MessagesRequest):
    logger.info("Received POST /v1/messages: model=%s, stream=%s, msgs=%d",
                payload.model, payload.stream, len(payload.messages))
    state: AppState = request.state.app_state
    _current_api_key_id.set(getattr(request.state, "api_key_id", None))
    return await _process_messages_common(state, payload, use_buffered=False)


async def post_messages_cc(request: Request, payload: MessagesRequest):
    logger.info("Received POST /cc/v1/messages: model=%s, stream=%s, msgs=%d",
                payload.model, payload.stream, len(payload.messages))
    state: AppState = request.state.app_state
    _current_api_key_id.set(getattr(request.state, "api_key_id", None))
    return await _process_messages_common(state, payload, use_buffered=True)


def _build_continuation_messages(
    original_messages: List[Dict[str, Any]],
    accumulated_text: str,
    ws_tool_uses: List[Dict[str, Any]],
    search_results_map: Dict[str, str],
) -> List[Dict[str, Any]]:
    """构建续接消息：原消息 + assistant(text+tool_use) + user(tool_result)"""
    messages = list(original_messages)

    # assistant 消息：文本 + tool_use
    assistant_content: List[Dict[str, Any]] = []
    if accumulated_text:
        assistant_content.append({"type": "text", "text": accumulated_text})
    for ws in ws_tool_uses:
        try:
            inp = json.loads(ws["input_json"]) if ws["input_json"] else {}
        except json.JSONDecodeError:
            inp = {}
        assistant_content.append({
            "type": "tool_use", "id": ws["tool_use_id"],
            "name": ws["name"], "input": inp,
        })
    messages.append({"role": "assistant", "content": assistant_content})

    # user 消息：tool_result
    user_content: List[Dict[str, Any]] = []
    for ws in ws_tool_uses:
        result_text = search_results_map.get(ws["tool_use_id"], "No results found.")
        user_content.append({
            "type": "tool_result", "tool_use_id": ws["tool_use_id"],
            "content": result_text,
        })
    messages.append({"role": "user", "content": user_content})

    return messages


MAX_AUTO_CONTINUE_ROUNDS = 5  # web_search 最大续接轮数


async def _do_mcp_searches(provider, ws_tool_uses: List[Dict[str, Any]]):
    """对所有 web_search tool_use 执行 MCP 搜索，返回 (results_map, text_map)"""
    search_results_map: Dict[str, Any] = {}
    search_text_map: Dict[str, str] = {}
    for ws in ws_tool_uses:
        try:
            query = json.loads(ws["input_json"]).get("query", "") if ws["input_json"] else ""
        except json.JSONDecodeError:
            query = ""
        if query:
            results = await websearch.call_mcp_search(provider, query)
            search_results_map[ws["tool_use_id"]] = results
            search_text_map[ws["tool_use_id"]] = websearch.format_search_results_text(query, results)
        else:
            search_results_map[ws["tool_use_id"]] = None
            search_text_map[ws["tool_use_id"]] = "No query provided."
    return search_results_map, search_text_map


def _offset_event_index(evt: SseEvent, offset: int) -> None:
    """将 SSE 事件的 index 字段加上偏移量（原地修改）"""
    if offset == 0:
        return
    idx = evt.data.get("index")
    if idx is not None:
        evt.data["index"] = idx + offset
        if evt.event == "content_block_start":
            cb = evt.data.get("content_block", {})
            if "index" in cb:
                cb["index"] = evt.data["index"]


def _make_final_delta_sse(input_tokens: int, output_tokens: int, stop_reason: str) -> SseEvent:
    """构建 message_delta 事件"""
    return SseEvent("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    })


async def _handle_stream_auto_continue(
    provider, state, payload: MessagesRequest, request_body: str,
    model: str, input_tokens: int, thinking_enabled: bool,
):
    """流式 web_search 自动续接：实时输出文本，拦截 tool_use 做搜索后继续流式"""
    try:
        first_response = await provider.call_api_stream(request_body)
    except Exception as e:
        return _map_provider_error(e)

    msg_logger = get_message_logger()

    async def event_generator():
        from kiro.parser.decoder import EventStreamDecoder
        from kiro.parser.error import BufferOverflow
        ping_event = 'event: ping\ndata: {"type": "ping"}\n\n'

        current_response = first_response
        current_messages = payload.messages
        index_offset = 0  # 每轮 content block 的 index 偏移
        is_first_round = True

        for round_idx in range(MAX_AUTO_CONTINUE_ROUNDS + 1):
            ctx = StreamContext(model, input_tokens, thinking_enabled)
            init_events = ctx.generate_initial_events()
            round_started = False
            round_retry_attempts = 1

            # 流式读取 Kiro 响应
            ws_block_indices = set()  # web_search tool_use 的原始 index（需抑制）
            max_yielded_index = index_offset - 1

            while True:
                decoder = EventStreamDecoder()
                fallback_parser = _KiroFallbackEventParser()
                fallback_mode = False
                strict_no_output_chunks = 0
                try:
                    async for chunk in _iter_stream_chunks_with_ping(current_response, PING_INTERVAL_SECS):
                        if chunk is None:
                            if round_started:
                                yield ping_event
                            continue
                        parsed_events: List[Any] = []
                        if fallback_mode:
                            parsed_events = fallback_parser.feed(chunk)
                        else:
                            try:
                                decoder.feed(chunk)
                            except BufferOverflow as e:
                                logger.warning("缓冲区溢出: %s", e)
                                fallback_parser.reset()
                                strict_no_output_chunks = 0
                                continue
                            for frame in decoder.decode_all():
                                event = _parse_event(frame)
                                if event is not None:
                                    parsed_events.append(event)

                            if parsed_events:
                                strict_no_output_chunks = 0
                                fallback_parser.reset()
                            else:
                                strict_no_output_chunks += 1
                                fallback_events = fallback_parser.feed(chunk)
                                if fallback_events:
                                    fallback_mode = True
                                    parsed_events = fallback_events
                                    logger.warning(
                                        "严格事件解码连续 %d 个 chunk 无产出，切换到 Kiro JSON fallback 解析 (第 %d 轮)",
                                        strict_no_output_chunks,
                                        round_idx + 1,
                                    )

                        if parsed_events and not round_started:
                            round_started = True
                            for evt in init_events:
                                if not is_first_round and evt.event == "message_start":
                                    continue
                                _offset_event_index(evt, index_offset)
                                yield evt.to_sse_string()

                        for event in parsed_events:
                            for sse in ctx.process_kiro_event(event):
                                # 抑制 web_search tool_use 块
                                if sse.event == "content_block_start":
                                    cb = sse.data.get("content_block", {})
                                    if cb.get("type") == "tool_use" and cb.get("name") == "web_search":
                                        ws_block_indices.add(sse.data.get("index"))
                                        continue
                                raw_idx = sse.data.get("index")
                                if raw_idx in ws_block_indices:
                                    continue
                                # 暂扣 message_delta/message_stop
                                if sse.event in ("message_delta", "message_stop"):
                                    continue
                                _offset_event_index(sse, index_offset)
                                yield sse.to_sse_string()
                                actual_idx = sse.data.get("index")
                                if actual_idx is not None and actual_idx > max_yielded_index:
                                    max_yielded_index = actual_idx
                    break
                except Exception as e:
                    await _aclose_response_quietly(current_response)
                    if not round_started and round_retry_attempts > 0:
                        round_retry_attempts -= 1
                        logger.warning("auto-continue 第 %d 轮在首个有效事件前读取失败，尝试重新建立流: %s", round_idx + 1, e)
                        try:
                            current_response = await provider.call_api_stream(request_body if round_idx == 0 else cont_body)
                        except Exception as retry_error:
                            logger.error("auto-continue 第 %d 轮重建流失败: %s", round_idx + 1, retry_error)
                            yield _make_stream_error_sse(f"读取上游流失败: {retry_error}")
                            return
                        continue
                    logger.error("读取响应流失败 (第 %d 轮): %s", round_idx + 1, e)
                    yield _make_stream_error_sse(f"读取上游流失败: {e}")
                    return
                finally:
                    await _aclose_response_quietly(current_response)

            if not round_started:
                for evt in init_events:
                    if not is_first_round and evt.event == "message_start":
                        continue
                    _offset_event_index(evt, index_offset)
                    yield evt.to_sse_string()

            ws_tool_uses = ctx.web_search_tool_uses
            if not ws_tool_uses:
                # 无 web_search → 输出结束事件，完成
                for sse in ctx.generate_final_events():
                    _offset_event_index(sse, index_offset)
                    yield sse.to_sse_string()
                if msg_logger and msg_logger.enabled:
                    msg_logger.log_stream_text(
                        model=model, text=ctx.accumulated_text,
                        stop_reason=ctx.state_manager.get_stop_reason(),
                        usage={"input_tokens": ctx.resolve_input_tokens(), "output_tokens": ctx.output_tokens},
                    )
                # 上报 token 用量
                _report_token_usage(model, ctx.resolve_input_tokens(), ctx.output_tokens)
                return

            # === 检测到 web_search，执行搜索并续接 ===
            logger.info("auto-continue 第 %d 轮: 检测到 %d 个 web_search", round_idx + 1, len(ws_tool_uses))

            # 关闭已输出的未关闭块（text 等）
            for idx, block in list(ctx.state_manager.active_blocks.items()):
                if block.started and not block.stopped and idx not in ws_block_indices:
                    actual_idx = idx + index_offset
                    yield SseEvent("content_block_stop", {"type": "content_block_stop", "index": actual_idx}).to_sse_string()
                    block.stopped = True

            # MCP 搜索 + 输出 server_tool_use / web_search_tool_result 事件
            next_index = max_yielded_index + 1
            search_text_map: Dict[str, str] = {}
            for ws in ws_tool_uses:
                try:
                    query = json.loads(ws["input_json"]).get("query", "") if ws["input_json"] else ""
                except json.JSONDecodeError:
                    query = ""
                results = await websearch.call_mcp_search(provider, query) if query else None
                search_text_map[ws["tool_use_id"]] = websearch.format_search_results_text(query, results) if query else "No query."
                ws_events = websearch.generate_web_search_result_events(ws["tool_use_id"], query, results, next_index)
                for evt in ws_events:
                    yield evt.to_sse_string()
                next_index += 2

            # 构建续接请求
            continuation_messages = _build_continuation_messages(
                current_messages, ctx.accumulated_text, ws_tool_uses, search_text_map,
            )
            cont_payload = copy.deepcopy(payload)
            cont_payload.messages = continuation_messages
            try:
                cont_result = convert_request(cont_payload)
            except Exception as e:
                logger.error("续接转换失败 (第 %d 轮): %s", round_idx + 1, e)
                # 回退：直接结束
                yield _make_final_delta_sse(input_tokens, ctx.output_tokens, "end_turn").to_sse_string()
                yield SseEvent("message_stop", {"type": "message_stop"}).to_sse_string()
                return

            cont_kiro_req = {"conversationState": cont_result.conversation_state.to_dict()}
            if state.profile_arn:
                cont_kiro_req["profileArn"] = state.profile_arn
            cont_body = json.dumps(cont_kiro_req, ensure_ascii=False)

            if msg_logger and msg_logger.enabled:
                msg_logger.log_request(
                    model=model, messages=continuation_messages,
                    system=payload.system, tools=payload.tools, stream=True,
                )

            ac_ctx_limit = int(get_context_window_size(model) * 0.92)
            try:
                continuation_metrics = token_module.estimate_kiro_payload_metrics(cont_kiro_req)
                # 主动压缩（auto-continue 每轮也触发）
                _ac_compressed, continuation_metrics = _compress_history_proactive(
                    cont_kiro_req, continuation_metrics, ac_ctx_limit,
                )
                # 超限时先裁剪历史，裁剪后再做最终校验
                if _metrics_still_too_heavy(continuation_metrics, ac_ctx_limit):
                    dropped, cont_body, continuation_metrics = _prune_history_for_capacity(
                        cont_kiro_req, continuation_metrics, ac_ctx_limit,
                    )
                    if dropped > 0:
                        logger.warning(
                            "auto-continue 第 %d 轮超限，裁剪旧 history %d 条: tokens=%d chars=%d bytes=%d",
                            round_idx + 1, dropped,
                            continuation_metrics.tokens, continuation_metrics.chars, continuation_metrics.bytes,
                        )
                continuation_metrics = _validate_outbound_kiro_request(
                    cont_kiro_req, cont_body, ac_ctx_limit,
                    precomputed_metrics=continuation_metrics,
                )
                _log_outbound_request_stats(
                    source=f"auto_continue_{round_idx + 1}",
                    kiro_request=cont_kiro_req,
                    metrics=continuation_metrics,
                    anthropic_message_count=len(continuation_messages),
                    anthropic_tool_count=len(cont_payload.tools or []),
                )
                input_tokens = max(input_tokens, continuation_metrics.tokens)
            except LocalRequestLimitError as e:
                logger.warning("auto-continue 第 %d 轮本地预检拒绝发送: %s", round_idx + 1, e)
                yield _make_final_delta_sse(input_tokens, ctx.output_tokens, "end_turn").to_sse_string()
                yield SseEvent("message_stop", {"type": "message_stop"}).to_sse_string()
                return

            try:
                current_response = await provider.call_api_stream(cont_body)
            except Exception as e:
                logger.error("续接 Kiro 调用失败 (第 %d 轮): %s", round_idx + 1, e)
                yield _make_final_delta_sse(input_tokens, ctx.output_tokens, "end_turn").to_sse_string()
                yield SseEvent("message_stop", {"type": "message_stop"}).to_sse_string()
                return

            current_messages = continuation_messages
            index_offset = next_index
            is_first_round = False

    return StreamingResponse(event_generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


async def count_tokens(payload: CountTokensRequest):
    logger.info("Received POST count_tokens: model=%s, msgs=%d", payload.model, len(payload.messages))
    total = await asyncio.to_thread(token_module.count_all_tokens, payload.model, payload.system, payload.messages, payload.tools)
    return JSONResponse(content=CountTokensResponse(input_tokens=max(total, 1)).to_dict())
