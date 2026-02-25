"""Anthropic -> Kiro 协议转换器 - 参考 src/anthropic/converter.rs"""

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from kiro.model.requests.conversation import (
    AssistantMessage, ConversationState, CurrentMessage,
    HistoryAssistantMessage, HistoryUserMessage, KiroImage,
    Message, UserInputMessage, UserInputMessageContext, UserMessage,
)
from kiro.model.requests.tool import (
    InputSchema, Tool, ToolResult, ToolSpecification, ToolUseEntry,
)

from .types import (
    AnthropicMessage, ContentBlock, ImageSource, MessagesRequest,
    OutputConfig, Thinking,
)

logger = logging.getLogger(__name__)

# 追加到 Write/Edit 工具描述末尾的内容
WRITE_TOOL_DESCRIPTION_SUFFIX = (
    "- IMPORTANT: If the content to write exceeds 150 lines, you MUST only write the first 50 lines "
    "using this tool, then use `Edit` tool to append the remaining content in chunks of no more than "
    "50 lines each. If needed, leave a unique placeholder to help append content. "
    "Do NOT attempt to write all content at once."
)
EDIT_TOOL_DESCRIPTION_SUFFIX = (
    "- IMPORTANT: If the `new_string` content exceeds 50 lines, you MUST split it into multiple Edit "
    "calls, each replacing no more than 50 lines at a time. If used to append content, leave a unique "
    "placeholder to help append content. On the final chunk, do NOT include the placeholder."
)
SYSTEM_CHUNKED_POLICY = (
    "When the Write or Edit tool has content size limits, always comply silently. "
    "Never suggest bypassing these limits via alternative tools. "
    "Never ask the user whether to switch approaches. "
    "Complete all chunked operations without commentary."
)


class ConversionError(Exception):
    pass


class UnsupportedModelError(ConversionError):
    def __init__(self, model: str):
        self.model = model
        super().__init__(f"模型不支持: {model}")


class EmptyMessagesError(ConversionError):
    def __init__(self):
        super().__init__("消息列表为空")


@dataclass
class ConversionResult:
    conversation_state: ConversationState


def map_model(model: str) -> Optional[str]:
    """模型映射：Anthropic 模型名 -> Kiro 模型 ID"""
    m = model.lower()
    if "sonnet" in m:
        return "claude-sonnet-4.6" if ("4-6" in m or "4.6" in m) else "claude-sonnet-4.5"
    elif "opus" in m:
        return "claude-opus-4.5" if ("4-5" in m or "4.5" in m) else "claude-opus-4.6"
    elif "haiku" in m:
        return "claude-haiku-4.5"
    return model


def normalize_json_schema(schema: Any) -> dict:
    """递归规范化 JSON Schema，修复 MCP 工具定义中常见的类型问题"""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}, "required": [], "additionalProperties": True}
    obj = dict(schema)
    # type 缺失时默认 object
    if not isinstance(obj.get("type"), str) or not obj["type"]:
        obj["type"] = "object"
    is_obj = obj["type"] == "object" or "properties" in obj
    if is_obj:
        if not isinstance(obj.get("properties"), dict):
            obj["properties"] = {}
        else:
            obj["properties"] = {
                k: normalize_json_schema(v) if isinstance(v, dict) else v
                for k, v in obj["properties"].items()
            }
        req = obj.get("required")
        if isinstance(req, list):
            obj["required"] = [r for r in req if isinstance(r, str)]
        else:
            obj["required"] = []
        ap = obj.get("additionalProperties")
        if not isinstance(ap, (bool, dict)):
            obj["additionalProperties"] = True
    # 递归处理 items（数组元素的子 schema）
    items = obj.get("items")
    if isinstance(items, dict):
        obj["items"] = normalize_json_schema(items)
    return obj


def _extract_session_id(user_id: str) -> Optional[str]:
    idx = user_id.find("session_")
    if idx == -1:
        return None
    session_part = user_id[idx + 8:]
    if len(session_part) >= 36:
        uuid_str = session_part[:36]
        if uuid_str.count("-") == 4:
            return uuid_str
    return None


_IMAGE_FORMAT_MAP = {"image/jpeg": "jpeg", "image/png": "png", "image/gif": "gif", "image/webp": "webp"}


def _get_image_format(media_type: str) -> Optional[str]:
    return _IMAGE_FORMAT_MAP.get(media_type)


def _extract_tool_result_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [item.get("text", "") for item in content if isinstance(item, dict) and "text" in item]
        return "\n".join(parts)
    if content is not None:
        return str(content)
    return ""
# PLACEHOLDER_CONVERTER_PART2


def _process_message_content(content: Any) -> Tuple[str, List[KiroImage], List[ToolResult]]:
    """处理消息内容，提取文本、图片和工具结果"""
    text_parts: List[str] = []
    images: List[KiroImage] = []
    tool_results: List[ToolResult] = []

    if isinstance(content, str):
        text_parts.append(content)
    elif isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            block_type = item.get("type", "")
            if block_type == "text":
                t = item.get("text")
                if t:
                    text_parts.append(t)
            elif block_type == "image":
                source = item.get("source", {})
                fmt = _get_image_format(source.get("media_type", ""))
                if fmt:
                    images.append(KiroImage.from_base64(fmt, source.get("data", "")))
            elif block_type == "tool_result":
                tool_use_id = item.get("tool_use_id")
                if tool_use_id:
                    result_content = _extract_tool_result_content(item.get("content"))
                    is_error = item.get("is_error", False)
                    tr = ToolResult.error(tool_use_id, result_content) if is_error else ToolResult.success(tool_use_id, result_content)
                    tr.status = "error" if is_error else "success"
                    tool_results.append(tr)

    return "\n".join(text_parts), images, tool_results


def _make_web_search_tool() -> Tool:
    """将 web_search 转为普通 Kiro 工具定义"""
    return Tool(
        tool_specification=ToolSpecification(
            name="web_search",
            description="Search the web for current information. Use this when you need up-to-date information that may not be in your training data.",
            input_schema=InputSchema.from_json({
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "properties": {"query": {"type": "string", "description": "The search query"}},
                "required": ["query"],
            }),
        )
    )


def _convert_tools(tools: Optional[List[Dict[str, Any]]]) -> List[Tool]:
    """转换工具定义，仅在客户端发送 web_search 时转换"""
    result = []
    has_web_search = False
    for t in (tools or []):
        tool_type = t.get("type", "")
        if tool_type and tool_type.startswith("web_search"):
            if not has_web_search:
                result.append(_make_web_search_tool())
                has_web_search = True
            continue
        name = t.get("name", "")
        if name == "web_search":
            if not has_web_search:
                result.append(_make_web_search_tool())
                has_web_search = True
            continue
        desc = t.get("description", "")
        suffix = ""
        if name == "Write":
            suffix = WRITE_TOOL_DESCRIPTION_SUFFIX
        elif name == "Edit":
            suffix = EDIT_TOOL_DESCRIPTION_SUFFIX
        if suffix:
            desc = f"{desc}\n{suffix}"
        if len(desc) > 10000:
            desc = desc[:10000]
        schema = normalize_json_schema(t.get("input_schema", {}))
        result.append(Tool(
            tool_specification=ToolSpecification(
                name=name, description=desc,
                input_schema=InputSchema.from_json(schema),
            )
        ))
    return result


def _create_placeholder_tool(name: str) -> Tool:
    """为历史中使用但不在 tools 列表中的工具创建占位符定义"""
    return Tool(
        tool_specification=ToolSpecification(
            name=name,
            description="Tool used in conversation history",
            input_schema=InputSchema.from_json({
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object", "properties": {},
                "required": [], "additionalProperties": True,
            }),
        )
    )
# PLACEHOLDER_CONVERTER_PART3


def _generate_thinking_prefix(req: MessagesRequest) -> Optional[str]:
    thinking = req.get_thinking()
    if not thinking:
        return None
    if thinking.type == "enabled":
        return (f"<thinking_mode>enabled</thinking_mode>"
                f"<max_thinking_length>{thinking.budget_tokens}</max_thinking_length>")
    elif thinking.type == "adaptive":
        oc = req.get_output_config()
        effort = oc.effort if oc else "high"
        return (f"<thinking_mode>adaptive</thinking_mode>"
                f"<thinking_effort>{effort}</thinking_effort>")
    return None


def _has_thinking_tags(content: str) -> bool:
    return "<thinking_mode>" in content or "<max_thinking_length>" in content


def _convert_assistant_message(msg: AnthropicMessage) -> HistoryAssistantMessage:
    """转换 assistant 消息"""
    thinking_content = ""
    text_content = ""
    tool_uses: List[ToolUseEntry] = []

    content = msg.content
    if isinstance(content, str):
        text_content = content
    elif isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            bt = item.get("type", "")
            if bt == "thinking":
                thinking_content += item.get("thinking", "")
            elif bt == "text":
                text_content += item.get("text", "")
            elif bt == "tool_use":
                tid = item.get("id")
                name = item.get("name")
                if tid and name:
                    inp = item.get("input", {})
                    te = ToolUseEntry(tool_use_id=tid, name=name, input=inp)
                    tool_uses.append(te)

    if thinking_content:
        if text_content:
            final = f"<thinking>{thinking_content}</thinking>\n\n{text_content}"
        else:
            final = f"<thinking>{thinking_content}</thinking>"
    elif not text_content and tool_uses:
        final = " "
    else:
        final = text_content

    am = AssistantMessage.new(final)
    if tool_uses:
        am.tool_uses = tool_uses
    return HistoryAssistantMessage(assistant_response_message=am)


def _merge_assistant_messages(messages: List[AnthropicMessage]) -> HistoryAssistantMessage:
    if len(messages) == 1:
        return _convert_assistant_message(messages[0])
    all_tool_uses: List[ToolUseEntry] = []
    content_parts: List[str] = []
    for msg in messages:
        converted = _convert_assistant_message(msg)
        am = converted.assistant_response_message
        if am.content.strip():
            content_parts.append(am.content)
        if am.tool_uses:
            all_tool_uses.extend(am.tool_uses)
    content = " " if not content_parts and all_tool_uses else "\n\n".join(content_parts)
    result = AssistantMessage.new(content)
    if all_tool_uses:
        result.tool_uses = all_tool_uses
    return HistoryAssistantMessage(assistant_response_message=result)
# PLACEHOLDER_CONVERTER_PART4


def _merge_user_messages(messages: List[AnthropicMessage], model_id: str) -> HistoryUserMessage:
    """合并多条连续 user 消息"""
    content_parts: List[str] = []
    all_images: List[KiroImage] = []
    all_tool_results: List[ToolResult] = []

    for msg in messages:
        text, images, tool_results = _process_message_content(msg.content)
        if text:
            content_parts.append(text)
        all_images.extend(images)
        all_tool_results.extend(tool_results)

    content = "\n".join(content_parts)
    user_msg = UserMessage.new(content, model_id)
    if all_images:
        user_msg.images = all_images
    if all_tool_results:
        user_msg.user_input_message_context = UserInputMessageContext(
            tool_results=all_tool_results,
        )
    return HistoryUserMessage(user_input_message=user_msg)


def _process_history_tools(
    history: List[Message], current_tool_results: List[ToolResult],
) -> Tuple[List[str], List[ToolResult]]:
    """一次遍历完成：收集 tool_names、验证 tool_pairing、移除 orphaned tool_uses

    直接访问 _data 属性避免 to_dict() 全量序列化。
    返回 (history_tool_names, validated_tool_results)。
    """
    tool_names: List[str] = []
    tool_names_seen: Set[str] = set()
    all_tool_use_ids: Set[str] = set()
    history_tool_result_ids: Set[str] = set()

    for msg in history:
        inner = msg._data
        if isinstance(inner, HistoryAssistantMessage):
            tool_uses = inner.assistant_response_message.tool_uses
            if tool_uses:
                for tu in tool_uses:
                    if tu.name and tu.name not in tool_names_seen:
                        tool_names.append(tu.name)
                        tool_names_seen.add(tu.name)
                    if tu.tool_use_id:
                        all_tool_use_ids.add(tu.tool_use_id)
        elif isinstance(inner, HistoryUserMessage):
            ctx = inner.user_input_message.user_input_message_context
            if ctx.tool_results:
                for tr in ctx.tool_results:
                    if tr.tool_use_id:
                        history_tool_result_ids.add(tr.tool_use_id)

    # 验证 tool_use/tool_result 配对
    unpaired = all_tool_use_ids - history_tool_result_ids

    filtered: List[ToolResult] = []
    for result in current_tool_results:
        if result.tool_use_id in unpaired:
            filtered.append(result)
            unpaired.discard(result.tool_use_id)
        elif result.tool_use_id in all_tool_use_ids:
            logger.warning("跳过重复的 tool_result: tool_use_id=%s", result.tool_use_id)
        else:
            logger.warning("跳过孤立的 tool_result: tool_use_id=%s", result.tool_use_id)

    # 移除孤立的 tool_use
    if unpaired:
        for oid in unpaired:
            logger.warning("检测到孤立的 tool_use，将从历史中移除: tool_use_id=%s", oid)
        for msg in history:
            inner = msg._data
            if not isinstance(inner, HistoryAssistantMessage):
                continue
            arm = inner.assistant_response_message
            if not arm.tool_uses:
                continue
            arm.tool_uses = [tu for tu in arm.tool_uses if tu.tool_use_id not in unpaired]
            if not arm.tool_uses:
                arm.tool_uses = None

    return tool_names, filtered


def _build_history(
    req: MessagesRequest, messages: List[AnthropicMessage], model_id: str,
) -> List[Message]:
    """构建历史消息（system prompt 作为首对 user+assistant 注入）"""
    history: List[Message] = []
    thinking_prefix = _generate_thinking_prefix(req)

    # 1. 处理系统消息 → 注入为历史首对 user+assistant
    if req.system:
        system_content = "\n".join(
            s.get("text", "") for s in req.system if s.get("text")
        )
        if system_content:
            system_content = f"{system_content}\n{SYSTEM_CHUNKED_POLICY}"
            if thinking_prefix and not _has_thinking_tags(system_content):
                system_content = f"{thinking_prefix}\n{system_content}"
            history.append(Message(HistoryUserMessage(
                user_input_message=UserMessage.new(system_content, model_id),
            )))
            history.append(Message(HistoryAssistantMessage(
                assistant_response_message=AssistantMessage.new("I will follow these instructions."),
            )))
    elif thinking_prefix:
        # 没有 system 但有 thinking 配置
        history.append(Message(HistoryUserMessage(
            user_input_message=UserMessage.new(thinking_prefix, model_id),
        )))
        history.append(Message(HistoryAssistantMessage(
            assistant_response_message=AssistantMessage.new("I will follow these instructions."),
        )))
# PLACEHOLDER_CONVERTER_PART7

    # 2. 处理常规消息历史（最后一条作为 currentMessage，不加入历史）
    history_end = len(messages) - 1
    user_buffer: List[AnthropicMessage] = []
    assistant_buffer: List[AnthropicMessage] = []

    for i in range(history_end):
        msg = messages[i]
        if msg.role == "user":
            # 先 flush assistant buffer
            if assistant_buffer:
                history.append(Message(_merge_assistant_messages(assistant_buffer)))
                assistant_buffer = []
            user_buffer.append(msg)
        elif msg.role == "assistant":
            # 先 flush user buffer
            if user_buffer:
                history.append(Message(_merge_user_messages(user_buffer, model_id)))
                user_buffer = []
            assistant_buffer.append(msg)

    # flush 末尾 assistant buffer
    if assistant_buffer:
        history.append(Message(_merge_assistant_messages(assistant_buffer)))

    # flush 末尾孤立 user buffer → 自动配对 "OK"
    if user_buffer:
        history.append(Message(_merge_user_messages(user_buffer, model_id)))
        history.append(Message(HistoryAssistantMessage(
            assistant_response_message=AssistantMessage.new("OK"),
        )))

    return history


def convert_request(req: MessagesRequest) -> ConversionResult:
    """将 Anthropic MessagesRequest 转换为 Kiro ConversationState"""
    model_id = map_model(req.model)
    if model_id is None:
        raise UnsupportedModelError(req.model)

    messages = req.get_messages()
    if not messages:
        raise EmptyMessagesError()

    # prefill 预处理：末尾是 assistant 则截断到最后一条 user
    if messages[-1].role != "user":
        logger.info("检测到末尾 assistant 消息（prefill），静默丢弃")
        last_user_idx = None
        for idx in range(len(messages) - 1, -1, -1):
            if messages[idx].role == "user":
                last_user_idx = idx
                break
        if last_user_idx is None:
            raise EmptyMessagesError()
        messages = messages[:last_user_idx + 1]
# PLACEHOLDER_CONVERTER_PART8

    # 提取 session_id
    session_id = None
    meta = req.get_metadata()
    if meta and meta.user_id:
        session_id = _extract_session_id(meta.user_id)
    conversation_id = session_id or str(uuid.uuid4())

    # 处理最后一条消息作为 current_message
    last_msg = messages[-1]
    current_text, current_images, current_tool_results = _process_message_content(last_msg.content)

    # 转换工具定义
    tools = _convert_tools(req.tools)

    # 构建历史消息（system prompt 在此注入为首对 user+assistant）
    history = _build_history(req, messages, model_id)

    # 一次遍历：验证 tool pairing + 收集工具名 + 移除孤立 tool_use
    history_tool_names, validated_tool_results = _process_history_tools(history, current_tool_results)
    existing_names = {t.tool_specification.name.lower() for t in tools}
    for tn in history_tool_names:
        if tn.lower() not in existing_names:
            tools.append(_create_placeholder_tool(tn))

    # 构建 UserInputMessageContext
    context = UserInputMessageContext()
    if tools:
        context.tools = tools
    if validated_tool_results:
        context.tool_results = validated_tool_results

    # 构建当前消息
    current_msg = UserInputMessage.new(current_text, model_id)
    current_msg.origin = "AI_EDITOR"
    if current_images:
        current_msg.images = current_images
    if tools or validated_tool_results:
        current_msg.user_input_message_context = context

    state = ConversationState(
        conversation_id=conversation_id,
        current_message=CurrentMessage(user_input_message=current_msg),
        history=history,
        agent_continuation_id=str(uuid.uuid4()),
        agent_task_type="vibe",
        chat_trigger_type="MANUAL",
    )

    return ConversionResult(conversation_state=state)
