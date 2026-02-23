"""Anthropic API Handler 函数 - 参考 src/anthropic/handlers.rs"""

import asyncio
import json
import logging
import uuid
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

    # 非纯搜索请求时，剥离 web_search 工具（Kiro 不支持）
    websearch.strip_web_search_tools(payload)

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


async def count_tokens(payload: CountTokensRequest):
    logger.info("Received POST count_tokens: model=%s, msgs=%d", payload.model, len(payload.messages))
    total = token_module.count_all_tokens(payload.model, payload.system, payload.messages, payload.tools)
    return JSONResponse(content=CountTokensResponse(input_tokens=max(total, 1)).to_dict())
