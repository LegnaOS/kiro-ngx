"""Anthropic API Handler 函数 - 参考 src/anthropic/handlers.rs"""

import asyncio
import contextlib
import json
import logging
import uuid
import copy
from typing import Any, Dict, List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

import token_counter as token_module
from .converter import ConversionError, UnsupportedModelError, EmptyMessagesError, convert_request
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

PING_INTERVAL_SECS = 25
MAX_STREAM_IDLE_PINGS = 8


async def _aclose_response_quietly(response) -> None:
    if response is None:
        return
    with contextlib.suppress(Exception):
        await response.aclose()


async def _iter_stream_chunks_with_ping(
    response,
    ping_interval: float,
    max_idle_pings: int = MAX_STREAM_IDLE_PINGS,
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
    if "CONTENT_LENGTH_EXCEEDS_THRESHOLD" in err_str:
        logger.warning("上游拒绝请求：上下文窗口已满")
        return JSONResponse(status_code=400, content=ErrorResponse.new(
            "invalid_request_error",
            "Context window is full. Reduce conversation history, system prompt, or tools.",
        ).to_dict())
    if "Input is too long" in err_str:
        logger.warning("上游拒绝请求：输入过长")
        return JSONResponse(status_code=400, content=ErrorResponse.new(
            "invalid_request_error",
            "Input is too long. Reduce the size of your messages.",
        ).to_dict())
    logger.error("Kiro API 调用失败: %s", err)
    return JSONResponse(status_code=502, content=ErrorResponse.new(
        "api_error", f"上游 API 调用失败: {err}",
    ).to_dict())


def _report_token_usage(model: str, input_tokens: int, output_tokens: int):
    """向 TokenUsageTracker 上报一次请求的 token 用量（模型名归一化为 Kiro ID）"""
    from anthropic_api.converter import map_model
    tracker = get_token_usage_tracker()
    if tracker:
        tracker.report(map_model(model) or model, input_tokens, output_tokens)


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
    is_opus_4_6 = "opus" in model_lower and ("4-6" in model_lower or "4.6" in model_lower)
    thinking_type = "adaptive" if is_opus_4_6 else "enabled"
    logger.info("模型名包含 thinking 后缀，覆写 thinking 配置: type=%s", thinking_type)
    payload.thinking = {"type": thinking_type, "budget_tokens": 20000}
    if is_opus_4_6:
        payload.output_config = {"effort": "high"}


async def _handle_stream_request(provider, request_body: str, model: str, input_tokens: int, thinking_enabled: bool):
    """处理流式请求"""
    try:
        response = await provider.call_api_stream(request_body)
    except Exception as e:
        return _map_provider_error(e)

    ctx = StreamContext(model, input_tokens, thinking_enabled)
    initial_events = ctx.generate_initial_events()

    async def event_generator():
        from kiro.parser.decoder import EventStreamDecoder
        from kiro.parser.error import BufferOverflow

        try:
            for evt in initial_events:
                yield evt.to_sse_string()

            ping_event = 'event: ping\ndata: {"type": "ping"}\n\n'
            decoder = EventStreamDecoder()
            try:
                async for chunk in _iter_stream_chunks_with_ping(response, PING_INTERVAL_SECS):
                    if chunk is None:
                        yield ping_event
                        continue
                    try:
                        decoder.feed(chunk)
                    except BufferOverflow as e:
                        logger.warning("缓冲区溢出: %s", e)
                        continue
                    for frame in decoder.decode_all():
                        event = _parse_event(frame)
                        if event is not None:
                            for sse in ctx.process_kiro_event(event):
                                yield sse.to_sse_string()
            except Exception as e:
                logger.error("读取响应流失败: %s", e)

            for sse in ctx.generate_final_events():
                yield sse.to_sse_string()

            msg_logger = get_message_logger()
            if msg_logger and msg_logger.enabled:
                final_input = ctx.context_input_tokens if ctx.context_input_tokens is not None else input_tokens
                msg_logger.log_stream_text(
                    model=model, text=ctx.accumulated_text,
                    stop_reason=ctx.state_manager.get_stop_reason(),
                    usage={"input_tokens": final_input, "output_tokens": ctx.output_tokens},
                )

            _report_token_usage(model, ctx.context_input_tokens or input_tokens, ctx.output_tokens)
        finally:
            await _aclose_response_quietly(response)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


async def _handle_stream_request_buffered(provider, request_body: str, model: str, estimated_input_tokens: int, thinking_enabled: bool):
    """处理缓冲流式请求（/cc/v1/messages）"""
    try:
        response = await provider.call_api_stream(request_body)
    except Exception as e:
        return _map_provider_error(e)

    buf_ctx = BufferedStreamContext(model, estimated_input_tokens, thinking_enabled)

    async def event_generator():
        from kiro.parser.decoder import EventStreamDecoder
        from kiro.parser.error import BufferOverflow

        try:
            decoder = EventStreamDecoder()
            ping_event = 'event: ping\ndata: {"type": "ping"}\n\n'

            try:
                async for chunk in _iter_stream_chunks_with_ping(response, PING_INTERVAL_SECS):
                    if chunk is None:
                        yield ping_event
                        continue
                    try:
                        decoder.feed(chunk)
                    except BufferOverflow as e:
                        logger.warning("缓冲区溢出: %s", e)
                        continue
                    for frame in decoder.decode_all():
                        event = _parse_event(frame)
                        if event is not None:
                            buf_ctx.process_and_buffer(event)
            except Exception as e:
                logger.error("读取响应流失败: %s", e)

            for sse in buf_ctx.finish_and_get_all_events():
                yield sse.to_sse_string()

            msg_logger = get_message_logger()
            if msg_logger and msg_logger.enabled:
                inner = buf_ctx.inner
                final_input = inner.context_input_tokens if inner.context_input_tokens is not None else estimated_input_tokens
                msg_logger.log_stream_text(
                    model=model, text=inner.accumulated_text,
                    stop_reason=inner.state_manager.get_stop_reason(),
                    usage={"input_tokens": final_input, "output_tokens": inner.output_tokens},
                )

            inner = buf_ctx.inner
            _report_token_usage(model, inner.context_input_tokens or estimated_input_tokens, inner.output_tokens)
        finally:
            await _aclose_response_quietly(response)

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
    context_input_tokens: Optional[int] = None
    tool_json_parts: Dict[str, List[str]] = {}

    for frame in decoder.decode_all():
        event = _parse_event(frame)
        if event is None:
            continue
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
                    inp = {}
                tool_uses.append({"type": "tool_use", "id": event.tool_use_id, "name": event.name, "input": inp})
        elif isinstance(event, ContextUsageEvent):
            actual = int(event.context_usage_percentage * CONTEXT_WINDOW_SIZE / 100.0)
            context_input_tokens = actual
            if event.context_usage_percentage >= 100.0:
                stop_reason = "model_context_window_exceeded"
        elif isinstance(event, dict) and event.get("type") == "exception":
            if event.get("exception_type") == "ContentLengthExceededException":
                stop_reason = "max_tokens"

    if has_tool_use and stop_reason == "end_turn":
        stop_reason = "tool_use"

    content: List[Dict[str, Any]] = []
    text_content = "".join(text_parts)
    if text_content:
        content.append({"type": "text", "text": text_content})
    content.extend(tool_uses)

    output_tokens = token_module.estimate_output_tokens(content)
    final_input = context_input_tokens if context_input_tokens is not None else input_tokens

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
        "usage": {"input_tokens": final_input, "output_tokens": output_tokens},
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
        input_tokens = token_module.count_all_tokens(
            payload.model, payload.system, payload.messages, payload.tools,
        )
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
    request_body = json.dumps(kiro_request, ensure_ascii=False)

    input_tokens = token_module.count_all_tokens(
        payload.model, payload.system, payload.messages, payload.tools,
    )
    thinking = payload.get_thinking()
    thinking_enabled = thinking.is_enabled() if thinking else False

    if payload.stream:
        # 有 web_search 时走流式 auto-continue
        if has_ws:
            return await _handle_stream_auto_continue(
                provider, state, payload, request_body, payload.model,
                input_tokens, thinking_enabled,
            )
        if use_buffered:
            return await _handle_stream_request_buffered(provider, request_body, payload.model, input_tokens, thinking_enabled)
        return await _handle_stream_request(provider, request_body, payload.model, input_tokens, thinking_enabled)
    return await _handle_non_stream_request(provider, request_body, payload.model, input_tokens)


async def post_messages(request: Request, payload: MessagesRequest):
    logger.info("Received POST /v1/messages: model=%s, stream=%s, msgs=%d",
                payload.model, payload.stream, len(payload.messages))
    state: AppState = request.state.app_state
    return await _process_messages_common(state, payload, use_buffered=False)


async def post_messages_cc(request: Request, payload: MessagesRequest):
    logger.info("Received POST /cc/v1/messages: model=%s, stream=%s, msgs=%d",
                payload.model, payload.stream, len(payload.messages))
    state: AppState = request.state.app_state
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
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
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

            # 输出初始事件（后续轮跳过 message_start）
            for evt in init_events:
                if not is_first_round and evt.event == "message_start":
                    continue
                _offset_event_index(evt, index_offset)
                yield evt.to_sse_string()

            # 流式读取 Kiro 响应
            ws_block_indices = set()  # web_search tool_use 的原始 index（需抑制）
            max_yielded_index = index_offset - 1
            decoder = EventStreamDecoder()

            try:
                async for chunk in _iter_stream_chunks_with_ping(current_response, PING_INTERVAL_SECS):
                    if chunk is None:
                        yield ping_event
                        continue
                    try:
                        decoder.feed(chunk)
                    except BufferOverflow as e:
                        logger.warning("缓冲区溢出: %s", e)
                        continue
                    for frame in decoder.decode_all():
                        event = _parse_event(frame)
                        if event is None:
                            continue
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
            except Exception as e:
                logger.error("读取响应流失败 (第 %d 轮): %s", round_idx + 1, e)
            finally:
                await _aclose_response_quietly(current_response)

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
                        usage={"input_tokens": input_tokens, "output_tokens": ctx.output_tokens},
                    )
                # 上报 token 用量
                _report_token_usage(model, ctx.context_input_tokens or input_tokens, ctx.output_tokens)
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
    total = token_module.count_all_tokens(payload.model, payload.system, payload.messages, payload.tools)
    return JSONResponse(content=CountTokensResponse(input_tokens=max(total, 1)).to_dict())
