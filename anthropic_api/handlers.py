"""Anthropic API Handler 函数 - 参考 src/anthropic/handlers.rs"""

import asyncio
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

logger = logging.getLogger(__name__)

PING_INTERVAL_SECS = 25


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


async def get_models():
    logger.info("Received GET /v1/models request")
    return JSONResponse(content=ModelsResponse(data=MODELS).to_dict())


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

        for evt in initial_events:
            yield evt.to_sse_string()

        ping_event = 'event: ping\ndata: {"type": "ping"}\n\n'
        decoder = EventStreamDecoder()
        try:
            chunk_iter = response.aiter_bytes().__aiter__()
            while True:
                try:
                    chunk = await asyncio.wait_for(chunk_iter.__anext__(), timeout=PING_INTERVAL_SECS)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    yield ping_event
                    continue
                decoder.feed(chunk)
                for frame in decoder.decode_all():
                    event = _parse_event(frame)
                    if event is not None:
                        for sse in ctx.process_kiro_event(event):
                            yield sse.to_sse_string()
        except Exception as e:
            logger.error("读取响应流失败: %s", e)

        for sse in ctx.generate_final_events():
            yield sse.to_sse_string()

        # 记录流式响应日志
        msg_logger = get_message_logger()
        if msg_logger and msg_logger.enabled:
            final_input = ctx.context_input_tokens if ctx.context_input_tokens is not None else input_tokens
            msg_logger.log_stream_text(
                model=model, text=ctx.accumulated_text,
                stop_reason=ctx.state_manager.get_stop_reason(),
                usage={"input_tokens": final_input, "output_tokens": ctx.output_tokens},
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
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

        decoder = EventStreamDecoder()
        ping_event = 'event: ping\ndata: {"type": "ping"}\n\n'

        try:
            chunk_iter = response.aiter_bytes().__aiter__()
            while True:
                try:
                    chunk = await asyncio.wait_for(chunk_iter.__anext__(), timeout=PING_INTERVAL_SECS)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    yield ping_event
                    continue
                decoder.feed(chunk)
                for frame in decoder.decode_all():
                    event = _parse_event(frame)
                    if event is not None:
                        buf_ctx.process_and_buffer(event)
        except Exception as e:
            logger.error("读取响应流失败: %s", e)

        for sse in buf_ctx.finish_and_get_all_events():
            yield sse.to_sse_string()

        # 记录流式响应日志
        msg_logger = get_message_logger()
        if msg_logger and msg_logger.enabled:
            inner = buf_ctx.inner
            final_input = inner.context_input_tokens if inner.context_input_tokens is not None else estimated_input_tokens
            msg_logger.log_stream_text(
                model=model, text=inner.accumulated_text,
                stop_reason=inner.state_manager.get_stop_reason(),
                usage={"input_tokens": final_input, "output_tokens": inner.output_tokens},
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
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
        data = {}

    et = EventType.from_str(event_type)
    if et == EventType.ASSISTANT_RESPONSE:
        return AssistantResponseEvent.from_dict(data)
    elif et == EventType.TOOL_USE:
        return ToolUseEvent.from_dict(data)
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
    decoder = EventStreamDecoder()
    decoder.feed(body_bytes)

    text_content = ""
    tool_uses: List[Dict[str, Any]] = []
    has_tool_use = False
    stop_reason = "end_turn"
    context_input_tokens: Optional[int] = None
    tool_json_buffers: Dict[str, str] = {}

    for frame in decoder.decode_all():
        event = _parse_event(frame)
        if event is None:
            continue
        if isinstance(event, AssistantResponseEvent):
            text_content += event.content
        elif isinstance(event, ToolUseEvent):
            has_tool_use = True
            buf = tool_json_buffers.setdefault(event.tool_use_id, "")
            buf += event.input
            tool_json_buffers[event.tool_use_id] = buf
            if event.stop:
                try:
                    inp = json.loads(buf) if buf else {}
                except json.JSONDecodeError:
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

    # web_search 始终注入到工具列表，所有流式请求走 auto-continue
    has_ws = True

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
        # 有 web_search 时统一走 buffered auto-continue（不论端点）
        if has_ws:
            return await _handle_buffered_auto_continue(
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


async def _buffer_kiro_stream(provider, request_body: str, model: str, input_tokens: int, thinking_enabled: bool) -> BufferedStreamContext:
    """调用 Kiro 并缓冲完整响应"""
    from kiro.parser.decoder import EventStreamDecoder

    response = await provider.call_api_stream(request_body)
    buf_ctx = BufferedStreamContext(model, input_tokens, thinking_enabled)
    decoder = EventStreamDecoder()

    async for chunk in response.aiter_bytes():
        decoder.feed(chunk)
        for frame in decoder.decode_all():
            event = _parse_event(frame)
            if event is not None:
                buf_ctx.process_and_buffer(event)

    return buf_ctx


def _build_continuation_messages(
    original_messages: List[Dict[str, Any]],
    accumulated_text: str,
    ws_tool_uses: List[Dict[str, Any]],
    search_results_map: Dict[str, str],
) -> List[Dict[str, Any]]:
    """构建续接消息：原消息 + assistant(text+tool_use) + user(tool_result)"""
    messages = copy.deepcopy(original_messages)

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


def _merge_auto_continue_events(
    first_events: List[SseEvent],
    ws_tool_uses: List[Dict[str, Any]],
    search_results_map: Dict[str, "websearch.WebSearchResults"],
    second_events: List[SseEvent],
) -> List[SseEvent]:
    """合并两次响应的 SSE 事件为一个流

    第一次响应中的 tool_use(web_search) → 替换为 server_tool_use + web_search_tool_result
    第二次响应中的 content 块 → 重新编号 index 后追加
    """
    merged: List[SseEvent] = []
    ws_ids = {ws["tool_use_id"] for ws in ws_tool_uses}
    skip_tool_use_indices = set()  # 需要跳过的 tool_use block index
    max_index = -1

    # 第一遍：找出 web_search tool_use 的 block index
    for evt in first_events:
        if evt.event == "content_block_start":
            cb = evt.data.get("content_block", {})
            if cb.get("type") == "tool_use" and cb.get("id") in ws_ids:
                skip_tool_use_indices.add(evt.data.get("index"))

    # 第二遍：复制第一次响应事件，跳过 web_search tool_use 块和 message_delta/message_stop
    for evt in first_events:
        idx = evt.data.get("index")
        if idx in skip_tool_use_indices:
            continue
        if evt.event in ("message_delta", "message_stop"):
            continue
        merged.append(evt)
        if idx is not None and idx > max_index:
            max_index = idx

    # 插入 server_tool_use + web_search_tool_result 事件
    next_index = max_index + 1
    for ws in ws_tool_uses:
        try:
            query = json.loads(ws["input_json"]).get("query", "") if ws["input_json"] else ""
        except json.JSONDecodeError:
            query = ""
        results = search_results_map.get(ws["tool_use_id"])
        ws_events = websearch.generate_web_search_result_events(
            ws["tool_use_id"], query, results, next_index,
        )
        merged.extend(ws_events)
        next_index += 2  # server_tool_use + web_search_tool_result 各占一个 index

    # 追加第二次响应的 content 块（重新编号）
    second_index_offset = next_index
    second_min_index = None
    for evt in second_events:
        if evt.event == "message_start":
            continue  # 跳过第二次的 message_start
        idx = evt.data.get("index")
        if idx is not None:
            if second_min_index is None:
                second_min_index = idx
            evt.data["index"] = idx - (second_min_index or 0) + second_index_offset
        if evt.event in ("content_block_start",):
            cb = evt.data.get("content_block", {})
            if "index" in cb:
                cb["index"] = evt.data["index"]
        merged.append(evt)

    return merged


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


async def _handle_buffered_auto_continue(
    provider, state, payload: MessagesRequest, request_body: str,
    model: str, input_tokens: int, thinking_enabled: bool,
):
    """缓冲流 + web_search 自动续接（支持多轮）"""
    msg_logger = get_message_logger()

    try:
        buf_ctx = await _buffer_kiro_stream(provider, request_body, model, input_tokens, thinking_enabled)
    except Exception as e:
        return _map_provider_error(e)

    ws_tool_uses = buf_ctx.get_web_search_tool_uses()

    # 没有 web_search tool_use → 正常返回
    if not ws_tool_uses:
        first_events = buf_ctx.finish_and_get_all_events()

        async def normal_gen():
            for sse in first_events:
                yield sse.to_sse_string()
        return StreamingResponse(normal_gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})

    # 收集所有轮次的事件和搜索结果，用于最终合并
    all_rounds: List[dict] = []  # [{events, ws_tool_uses, search_results_map}]
    current_messages = payload.messages
    prev_buf = buf_ctx

    for round_idx in range(MAX_AUTO_CONTINUE_ROUNDS):
        cur_ws = prev_buf.get_web_search_tool_uses()
        if not cur_ws:
            break

        logger.info("auto-continue 第 %d 轮: 检测到 %d 个 web_search", round_idx + 1, len(cur_ws))

        # MCP 搜索
        search_results_map, search_text_map = await _do_mcp_searches(provider, cur_ws)

        # 记录本轮
        cur_events = prev_buf.finish_and_get_all_events()
        all_rounds.append({
            "events": cur_events,
            "ws_tool_uses": cur_ws,
            "search_results_map": search_results_map,
        })

        # 构建续接消息
        continuation_messages = _build_continuation_messages(
            current_messages, prev_buf.inner.accumulated_text,
            cur_ws, search_text_map,
        )
        continuation_payload = copy.deepcopy(payload)
        continuation_payload.messages = continuation_messages

        try:
            cont_result = convert_request(continuation_payload)
        except Exception as e:
            logger.error("续接请求转换失败 (第 %d 轮): %s", round_idx + 1, e)
            break

        cont_kiro_req = {"conversationState": cont_result.conversation_state.to_dict()}
        if state.profile_arn:
            cont_kiro_req["profileArn"] = state.profile_arn
        cont_body = json.dumps(cont_kiro_req, ensure_ascii=False)

        # 记录续接请求日志
        if msg_logger and msg_logger.enabled:
            msg_logger.log_request(
                model=model, messages=continuation_messages,
                system=payload.system, tools=payload.tools,
                stream=True,
            )

        try:
            prev_buf = await _buffer_kiro_stream(provider, cont_body, model, input_tokens, thinking_enabled)
        except Exception as e:
            logger.error("续接 Kiro 调用失败 (第 %d 轮): %s", round_idx + 1, e)
            break

        current_messages = continuation_messages

    # 最后一轮的事件（最终回答或最后一次 tool_use）
    final_events = prev_buf.finish_and_get_all_events()

    # 合并所有轮次
    if not all_rounds:
        # 没有任何续接（不应该到这里，但保险起见）
        merged = final_events
    else:
        # 逐轮合并：第一轮 events + search results + 第二轮 events + ... + final
        merged = _merge_multi_round_events(all_rounds, final_events)

    # 日志
    if msg_logger and msg_logger.enabled:
        msg_logger.log_stream_text(
            model=model, text=prev_buf.inner.accumulated_text,
            stop_reason=prev_buf.inner.state_manager.get_stop_reason(),
            usage={"input_tokens": input_tokens, "output_tokens": prev_buf.inner.output_tokens},
        )

    async def merged_gen():
        for sse in merged:
            yield sse.to_sse_string()

    return StreamingResponse(merged_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})


def _merge_multi_round_events(
    rounds: List[dict], final_events: List[SseEvent],
) -> List[SseEvent]:
    """合并多轮 auto-continue 事件为一个 SSE 流"""
    if len(rounds) == 1:
        return _merge_auto_continue_events(
            rounds[0]["events"], rounds[0]["ws_tool_uses"],
            rounds[0]["search_results_map"], final_events,
        )

    # 多轮：逐轮合并，每轮的 "下一轮事件" 是后续所有轮的递归合并
    # 简化实现：从最后一轮往前合并
    result_events = final_events
    for rnd in reversed(rounds):
        result_events = _merge_auto_continue_events(
            rnd["events"], rnd["ws_tool_uses"],
            rnd["search_results_map"], result_events,
        )
    return result_events


async def count_tokens(payload: CountTokensRequest):
    logger.info("Received POST count_tokens: model=%s, msgs=%d", payload.model, len(payload.messages))
    total = token_module.count_all_tokens(payload.model, payload.system, payload.messages, payload.tools)
    return JSONResponse(content=CountTokensResponse(input_tokens=max(total, 1)).to_dict())
