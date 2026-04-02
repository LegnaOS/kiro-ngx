"""Anthropic -> Kiro 协议转换器 - 参考 src/anthropic/converter.rs"""

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
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

MAX_TOOL_DESCRIPTION_LENGTH = 9216
RECENT_HISTORY_WINDOW = 5
CURRENT_TOOL_RESULT_MAX_CHARS = 16_000
CURRENT_TOOL_RESULT_MAX_LINES = 300
HISTORY_TOOL_RESULT_MAX_CHARS = 6_000
HISTORY_TOOL_RESULT_MAX_LINES = 120
CURRENT_MESSAGE_PLACEHOLDER = "Tool results provided."
CONTINUE_PLACEHOLDER = "Continue"
EMPTY_ASSISTANT_PLACEHOLDER = "OK"


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
    tool_name_map: Dict[str, str] = field(default_factory=dict)  # short → original


def configure_converter_limits(
    *,
    current_tool_result_max_chars: Optional[int] = None,
    current_tool_result_max_lines: Optional[int] = None,
    history_tool_result_max_chars: Optional[int] = None,
    history_tool_result_max_lines: Optional[int] = None,
) -> None:
    global CURRENT_TOOL_RESULT_MAX_CHARS
    global CURRENT_TOOL_RESULT_MAX_LINES
    global HISTORY_TOOL_RESULT_MAX_CHARS
    global HISTORY_TOOL_RESULT_MAX_LINES

    if isinstance(current_tool_result_max_chars, int) and current_tool_result_max_chars > 0:
        CURRENT_TOOL_RESULT_MAX_CHARS = current_tool_result_max_chars
    if isinstance(current_tool_result_max_lines, int) and current_tool_result_max_lines > 0:
        CURRENT_TOOL_RESULT_MAX_LINES = current_tool_result_max_lines
    if isinstance(history_tool_result_max_chars, int) and history_tool_result_max_chars > 0:
        HISTORY_TOOL_RESULT_MAX_CHARS = history_tool_result_max_chars
    if isinstance(history_tool_result_max_lines, int) and history_tool_result_max_lines > 0:
        HISTORY_TOOL_RESULT_MAX_LINES = history_tool_result_max_lines


def map_model(model: str) -> Optional[str]:
    """模型映射：Anthropic 模型名 -> Kiro 模型 ID

    Claude Code 发送的模型名格式（来自 normalizeModelStringForAPI）：
    - claude-opus-4-6-20260301, claude-sonnet-4-6-20260301
    - claude-opus-4-5-20251101, claude-sonnet-4-5-20250929
    - claude-haiku-4-5-20251001
    - Claude-Opus-4-6-Agentic (SDK 场景)
    - 用户自定义模型名（直接透传）
    """
    m = model.lower()
    # 去掉 -thinking 后缀再匹配（proxy 的 model list 有 -thinking 变体）
    m_base = m.replace("-thinking", "")
    if "sonnet" in m_base:
        return "claude-sonnet-4.6" if ("4-6" in m_base or "4.6" in m_base) else "claude-sonnet-4.5"
    elif "opus" in m_base:
        return "claude-opus-4.6" if ("4-6" in m_base or "4.6" in m_base) else "claude-opus-4.5"
    elif "haiku" in m_base:
        return "claude-haiku-4.5"
    return model


def get_context_window_size(model: str) -> int:
    """根据模型返回上下文窗口大小（token 数）。4.6 模型使用 1M，其他使用 200K。"""
    m = model.lower()
    if "4-6" in m or "4.6" in m:
        return 1_000_000
    return 200_000


def _flatten_anyof_oneof(schema: dict) -> dict:
    """将 anyOf/oneOf 降级为 Kiro 可接受的简单 schema。

    Claude Code 的 Zod→JSON Schema 转换会为 union 类型生成 anyOf/oneOf，
    例如 `string | null` → `{"anyOf": [{"type":"string"}, {"type":"null"}]}`。
    Kiro 不支持这些关键字，所以这里选取第一个非 null 分支作为主类型，
    并在有 null 分支时设置 nullable=true。
    """
    variants = schema.get("anyOf") or schema.get("oneOf")
    if not isinstance(variants, list) or not variants:
        return schema

    has_null = any(
        isinstance(v, dict) and v.get("type") == "null" for v in variants
    )
    non_null = [v for v in variants if isinstance(v, dict) and v.get("type") != "null"]

    if not non_null:
        return schema

    # 取第一个非 null 分支，合并原 schema 中的其他字段（description 等）
    picked = dict(non_null[0])
    for k, v in schema.items():
        if k in ("anyOf", "oneOf"):
            continue
        if k not in picked:
            picked[k] = v

    if has_null:
        picked["nullable"] = True

    return picked


def normalize_json_schema(schema: Any) -> dict:
    """白名单策略：只保留 Kiro API 支持的 JSON Schema 字段，删除可能导致错误的字段"""
    if not isinstance(schema, dict):
        return {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": True,
        }

    # Claude Code 使用 Zod → JSON Schema，会产生 anyOf/oneOf（union 类型）和 default/const。
    # 先将 anyOf/oneOf 降级为 Kiro 可接受的形式，再做白名单过滤。
    schema = _flatten_anyof_oneof(schema)

    # Kiro API 支持的字段白名单（参考 AIClient-2-API 策略）
    ALLOWED_KEYS = {
        "type", "description", "properties", "required",
        "enum", "items", "nullable", "additionalProperties",
    }

    result = {}
    for key, value in schema.items():
        if key not in ALLOWED_KEYS:
            continue  # 删除不支持的字段（$schema, format, pattern, minimum, maximum 等）

        if key == "properties":
            if isinstance(value, dict):
                # 递归处理每个属性的子 schema
                result[key] = {
                    k: normalize_json_schema(v) if isinstance(v, dict) else v
                    for k, v in value.items()
                }
            else:
                result[key] = {}
            continue

        if key == "items":
            result[key] = normalize_json_schema(value) if isinstance(value, dict) else {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": True,
            }
            continue

        if key == "required":
            # 确保 required 始终是字符串数组
            result[key] = [r for r in value if isinstance(r, str)] if isinstance(value, list) else []
            continue

        if key == "additionalProperties":
            # Kiro 仅接受 bool 或 object，其他情况按 true 处理
            if isinstance(value, bool):
                result[key] = value
            elif isinstance(value, dict):
                result[key] = normalize_json_schema(value)
            else:
                result[key] = True
            continue

        result[key] = value

    # 确保基本字段存在
    if not isinstance(result.get("type"), str) or not result.get("type"):
        result["type"] = "object"
    if result.get("type") == "object" and not isinstance(result.get("properties"), dict):
        result["properties"] = {}
    if not isinstance(result.get("required"), list):
        result["required"] = []
    if "additionalProperties" not in result or not isinstance(result.get("additionalProperties"), (bool, dict)):
        result["additionalProperties"] = True

    return result


def _extract_session_id(user_id: str) -> Optional[str]:
    """从 metadata.user_id 中提取 session_id。

    Claude Code 发送两种格式：
    1. JSON: {"device_id":"...","session_id":"uuid-here",...}
    2. 旧格式: 包含 "session_" 前缀的纯字符串
    """
    if not user_id:
        return None
    # 尝试 JSON 解析（Claude Code getAPIMetadata 发送 JSON 格式）
    if user_id.startswith("{"):
        try:
            parsed = json.loads(user_id)
            if isinstance(parsed, dict):
                sid = parsed.get("session_id")
                if isinstance(sid, str) and len(sid) >= 36 and sid.count("-") == 4:
                    return sid[:36]
        except (json.JSONDecodeError, TypeError):
            pass
    # 回退：旧的 "session_" 前缀搜索
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
    return _extract_text_content(content)


def _extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [_extract_text_content(item) for item in content]
        return "\n".join(part for part in parts if part)
    if isinstance(content, dict):
        block_type = content.get("type", "")
        if block_type == "text":
            text = content.get("text")
            return text if isinstance(text, str) else ""
        if block_type == "thinking":
            thinking = content.get("thinking")
            if isinstance(thinking, str):
                return thinking
            text = content.get("text")
            return text if isinstance(text, str) else ""
        if block_type in ("redacted_thinking", "connector_text"):
            # Claude Code 特有块类型，Kiro 不支持，静默跳过
            return ""
        if block_type == "tool_result":
            return _extract_text_content(content.get("content"))
        if block_type == "web_search_tool_result":
            # 服务端搜索结果，提取文本内容
            return _extract_text_content(content.get("content"))
        if block_type == "tool_use":
            inp = content.get("input")
            if inp is None:
                return ""
            try:
                return json.dumps(inp, ensure_ascii=False, sort_keys=True)
            except TypeError:
                return str(inp)
        if block_type in ("tool_reference", "document", "image"):
            # 这些块类型不含可提取的文本
            return ""
        if "text" in content and isinstance(content.get("text"), str):
            return content.get("text", "")
        if "content" in content:
            return _extract_text_content(content.get("content"))
        # 回退：序列化为 JSON，但排除 cache_control 等元数据字段
        filtered = {k: v for k, v in content.items() if k not in ("cache_control", "citations")}
        try:
            return json.dumps(filtered, ensure_ascii=False, sort_keys=True)
        except TypeError:
            return str(content)
    if content is not None:
        return str(content)
    return ""


def _truncate_middle(text: str, max_chars: int, max_lines: int, label: str) -> str:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized:
        return ""

    lines = normalized.split("\n")
    line_count = len(lines)
    if len(normalized) <= max_chars and line_count <= max_lines:
        return normalized

    head_lines = max(1, max_lines // 2)
    tail_lines = max(1, max_lines - head_lines)
    head = "\n".join(lines[:head_lines])
    tail = "\n".join(lines[-tail_lines:]) if tail_lines < line_count else ""
    omitted_lines = max(line_count - head_lines - tail_lines, 0)
    omitted_chars = max(len(normalized) - len(head) - len(tail), 0)
    summary = (
        f"[{label} truncated: original {len(normalized)} chars / {line_count} lines; "
        f"omitted middle {omitted_chars} chars / {omitted_lines} lines]"
    )

    combined_parts = [head, summary]
    if tail:
        combined_parts.append(tail)
    truncated = "\n".join(part for part in combined_parts if part)

    if len(truncated) <= max_chars:
        return truncated

    budget = max(max_chars - len(summary) - 2, 0)
    if budget <= 0:
        return summary[:max_chars]
    head_budget = max(1, budget // 2)
    tail_budget = max(1, budget - head_budget)
    head_text = normalized[:head_budget].rstrip()
    tail_text = normalized[-tail_budget:].lstrip() if tail_budget < len(normalized) else ""
    parts = [head_text, summary]
    if tail_text:
        parts.append(tail_text)
    return "\n".join(part for part in parts if part)


def _classify_tool_result_text(text: str) -> Tuple[str, str]:
    normalized = (text or "").strip()
    lowered = normalized.lower()
    if not normalized:
        return "tool_result", "empty payload"
    if "<task-notification>" in normalized or "full transcript available at:" in lowered:
        return "task transcript", "contains sub-agent/task transcript output"
    if "<tool_use_error>" in normalized or "successfully stopped task:" in lowered:
        return "task control", "contains task control or tool error result"
    if "<retrieval_status>" in normalized or "<task_id>" in normalized:
        return "task status", "contains task status polling output"
    if "[request interrupted by user]" in lowered:
        return "interrupted transcript", "contains interrupted transcript fragments"
    if normalized.startswith("{") and "\"task_id\"" in normalized:
        return "task json", "contains task operation json payload"
    return "tool_result", "generic tool result content"


def _shrink_tool_result_content(content: Any, history_distance: Optional[int] = None) -> str:
    # A2 不会在请求转换阶段主动裁切 tool_result。
    # 这里保留原始内容，避免 continuation 依赖的工具上下文被提前破坏。
    return _extract_tool_result_content(content)


def _dedupe_tool_results(tool_results: List[ToolResult]) -> List[ToolResult]:
    seen: Set[str] = set()
    deduped: List[ToolResult] = []
    for result in tool_results:
        if not result.tool_use_id or result.tool_use_id in seen:
            continue
        seen.add(result.tool_use_id)
        deduped.append(result)
    return deduped
# --- 工具名称缩短 (Rust parity: TOOL_NAME_MAX_LEN = 63) ---
TOOL_NAME_MAX_LEN = 63


def _shorten_tool_name(name: str) -> str:
    """生成确定性短名称: 截断前缀 + '_' + 8 位 SHA256 hex（按 UTF-8 字节长度截断）"""
    hash_suffix = hashlib.sha256(name.encode()).hexdigest()[:8]
    prefix_max_bytes = TOOL_NAME_MAX_LEN - 1 - 8  # 54 bytes prefix + 1 underscore + 8 hash = 63
    # 按 UTF-8 字节长度截断，避免多字节字符编码后超长
    encoded = name.encode("utf-8")
    if len(encoded) > prefix_max_bytes:
        truncated = encoded[:prefix_max_bytes]
        # 确保不切断多字节 UTF-8 字符
        prefix = truncated.decode("utf-8", errors="ignore")
    else:
        prefix = name
    return f"{prefix}_{hash_suffix}"


def _map_tool_name(name: str, tool_name_map: Dict[str, str]) -> str:
    """如果名称超长则缩短，并记录映射 (short → original)"""
    if len(name) <= TOOL_NAME_MAX_LEN:
        return name
    short = _shorten_tool_name(name)
    tool_name_map[short] = name
    return short


# --- 工具配对验证 (Rust parity: validate_tool_pairing + remove_orphaned_tool_uses) ---

def _collect_history_tool_names(history: List[Message]) -> List[str]:
    """收集历史消息中使用的所有工具名称"""
    tool_names: List[str] = []
    for msg in history:
        inner = msg._data
        if isinstance(inner, HistoryAssistantMessage):
            if inner.assistant_response_message.tool_uses:
                for tu in inner.assistant_response_message.tool_uses:
                    if tu.name not in tool_names:
                        tool_names.append(tu.name)
    return tool_names


def _validate_tool_pairing(
    history: List[Message], tool_results: List[ToolResult],
) -> Tuple[List[ToolResult], Set[str]]:
    """验证 tool_use / tool_result 配对。

    Returns:
        (filtered_results, orphaned_tool_use_ids)
    """
    all_tool_use_ids: Set[str] = set()
    history_tool_result_ids: Set[str] = set()

    for msg in history:
        inner = msg._data
        if isinstance(inner, HistoryAssistantMessage):
            if inner.assistant_response_message.tool_uses:
                for tu in inner.assistant_response_message.tool_uses:
                    all_tool_use_ids.add(tu.tool_use_id)
        elif isinstance(inner, HistoryUserMessage):
            for r in inner.user_input_message.user_input_message_context.tool_results:
                history_tool_result_ids.add(r.tool_use_id)

    unpaired: Set[str] = all_tool_use_ids - history_tool_result_ids

    filtered: List[ToolResult] = []
    for r in tool_results:
        if r.tool_use_id in unpaired:
            filtered.append(r)
            unpaired.discard(r.tool_use_id)
        elif r.tool_use_id in all_tool_use_ids:
            logger.warning("跳过重复的 tool_result: tool_use_id=%s", r.tool_use_id)
        else:
            logger.warning("跳过孤立的 tool_result: tool_use_id=%s", r.tool_use_id)

    for oid in unpaired:
        logger.warning("检测到孤立的 tool_use，将从历史中移除: tool_use_id=%s", oid)

    return filtered, unpaired


def _remove_orphaned_tool_uses(history: List[Message], orphaned_ids: Set[str]) -> None:
    """从历史 assistant 消息中移除孤立的 tool_use"""
    if not orphaned_ids:
        return
    for msg in history:
        inner = msg._data
        if isinstance(inner, HistoryAssistantMessage):
            tus = inner.assistant_response_message.tool_uses
            if tus:
                original_len = len(tus)
                tus[:] = [tu for tu in tus if tu.tool_use_id not in orphaned_ids]
                if not tus:
                    inner.assistant_response_message.tool_uses = None
                elif len(tus) != original_len:
                    logger.debug("从 assistant 消息中移除了 %d 个孤立的 tool_use", original_len - len(tus))


def _process_message_content(
    content: Any,
    keep_images: bool = True,
    image_placeholder: bool = False,
    history_distance: Optional[int] = None,
) -> Tuple[str, List[KiroImage], List[ToolResult]]:
    """处理消息内容，提取文本、图片和工具结果。

    处理 Claude Code 发送的所有 user 侧 content block 类型：
    - text: 文本
    - image: 图片
    - tool_result: 工具结果
    - web_search_tool_result: 服务端搜索结果（转为普通 tool_result）
    - tool_reference: 工具搜索发现的工具（跳过）
    - document: PDF 等文档（跳过，Kiro 不支持）
    """
    text_parts: List[str] = []
    images: List[KiroImage] = []
    tool_results: List[ToolResult] = []
    omitted_images = 0

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
                if keep_images:
                    source = item.get("source", {})
                    fmt = _get_image_format(source.get("media_type", ""))
                    if fmt:
                        images.append(KiroImage.from_base64(fmt, source.get("data", "")))
                else:
                    omitted_images += 1
            elif block_type in ("tool_result", "web_search_tool_result"):
                tool_use_id = item.get("tool_use_id")
                if tool_use_id:
                    result_content = _shrink_tool_result_content(
                        item.get("content"),
                        history_distance=history_distance,
                    )
                    is_error = item.get("is_error", False)
                    tr = ToolResult.error(tool_use_id, result_content) if is_error else ToolResult.success(tool_use_id, result_content)
                    tr.status = "error" if is_error else "success"
                    tool_results.append(tr)
            # tool_reference, document, thinking 等静默跳过

    if omitted_images and image_placeholder:
        text_parts.append(f"[此历史消息包含 {omitted_images} 张图片，已省略原始内容]")

    return "\n".join(part for part in text_parts if part), images, _dedupe_tool_results(tool_results)


def _convert_tools(
    tools: Optional[List[Dict[str, Any]]],
    tool_name_map: Dict[str, str],
) -> List[Tool]:
    """按 A2 的思路转换工具定义，超长名称自动缩短并记录映射。"""
    if not tools:
        logger.info("未提供工具，插入 A2 风格占位工具")
        return [_create_placeholder_tool("no_tool_available")]

    result: List[Tool] = []
    filtered_tools: List[Dict[str, Any]] = []
    for t in tools:
        tool_type = str(t.get("type", "") or "")
        name = str(t.get("name", "") or "")
        lower_name = name.lower()
        if tool_type.startswith("web_search") or lower_name in {"web_search", "websearch"}:
            logger.info("按 A2 逻辑过滤工具: %s", name or tool_type)
            continue
        filtered_tools.append(t)

    for t in filtered_tools:
        name = t.get("name", "")
        if not name:
            continue
        desc = t.get("description", "")
        if not isinstance(desc, str):
            desc = ""
        # Claude Code 的 MCP/deferred 工具可能 description 为空，不应丢弃
        if not desc.strip():
            desc = f"Tool: {name}"
            logger.debug("工具 description 为空，使用默认描述: %s", name)
        # 对 Write/Edit 工具追加自定义描述后缀
        if name == "Write":
            desc = f"{desc}\n{WRITE_TOOL_DESCRIPTION_SUFFIX}"
        elif name == "Edit":
            desc = f"{desc}\n{EDIT_TOOL_DESCRIPTION_SUFFIX}"
        if len(desc) > MAX_TOOL_DESCRIPTION_LENGTH:
            desc = desc[:MAX_TOOL_DESCRIPTION_LENGTH] + "..."
        schema = normalize_json_schema(t.get("input_schema", {}))
        mapped_name = _map_tool_name(name, tool_name_map)
        result.append(Tool(
            tool_specification=ToolSpecification(
                name=mapped_name,
                description=desc,
                input_schema=InputSchema.from_json(schema),
            )
        ))

    if result:
        return result

    logger.info("所有工具均被过滤，插入 A2 风格占位工具")
    return [_create_placeholder_tool("no_tool_available")]


def _create_placeholder_tool(name: str) -> Tool:
    """创建与 A2 一致的占位工具定义。"""
    description = "This is a placeholder tool when no other tools are available. It does nothing."
    if name != "no_tool_available":
        description = "Tool used in conversation history"
    return Tool(
        tool_specification=ToolSpecification(
            name=name,
            description=description,
            input_schema=InputSchema.from_json({
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object", "properties": {},
                "required": [], "additionalProperties": True,
            }),
        )
    )
# PLACEHOLDER_CONVERTER_PART3


def _merge_adjacent_messages(messages: List[AnthropicMessage]) -> List[AnthropicMessage]:
    merged: List[AnthropicMessage] = []
    for msg in messages:
        if not merged:
            merged.append(AnthropicMessage(role=msg.role, content=msg.content))
            continue

        prev = merged[-1]
        if msg.role != prev.role:
            merged.append(AnthropicMessage(role=msg.role, content=msg.content))
            continue

        prev_content = prev.content
        cur_content = msg.content
        if isinstance(prev_content, list) and isinstance(cur_content, list):
            prev.content = prev_content + cur_content
        elif isinstance(prev_content, str) and isinstance(cur_content, str):
            prev.content = f"{prev_content}\n{cur_content}" if prev_content and cur_content else (prev_content or cur_content)
        elif isinstance(prev_content, list) and isinstance(cur_content, str):
            prev.content = prev_content + ([{"type": "text", "text": cur_content}] if cur_content else [])
        elif isinstance(prev_content, str) and isinstance(cur_content, list):
            prefix = [{"type": "text", "text": prev_content}] if prev_content else []
            prev.content = prefix + cur_content
        else:
            merged.append(AnthropicMessage(role=msg.role, content=msg.content))
    return merged


def _convert_history_user_message(msg: AnthropicMessage, model_id: str, history_distance: int) -> Message:
    keep_images = history_distance <= RECENT_HISTORY_WINDOW
    text, images, tool_results = _process_message_content(
        msg.content,
        keep_images=keep_images,
        image_placeholder=not keep_images,
        history_distance=history_distance,
    )
    user_msg = UserMessage.new(text, model_id)
    if images:
        user_msg.images = images
    if tool_results:
        user_msg.user_input_message_context = UserInputMessageContext(
            tool_results=_dedupe_tool_results(tool_results),
        )
    return Message(HistoryUserMessage(user_input_message=user_msg))


def _convert_history_assistant_message(
    msg: AnthropicMessage, tool_name_map: Optional[Dict[str, str]] = None,
) -> Message:
    return Message(_convert_assistant_message(msg, tool_name_map))


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


def _convert_assistant_message(
    msg: AnthropicMessage,
    tool_name_map: Optional[Dict[str, str]] = None,
) -> HistoryAssistantMessage:
    """转换 assistant 消息，工具名称自动缩短。

    处理 Claude Code 可能发送的所有 content block 类型：
    - thinking / redacted_thinking: 合并为 <thinking> 标签
    - text: 正文
    - tool_use: 工具调用
    - server_tool_use: 服务端工具（advisor/web_search），转为普通 tool_use
    - web_search_tool_result / tool_result: 跳过（属于 user 侧）
    - connector_text: 跳过（Kiro 不支持）
    """
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
            elif bt == "redacted_thinking":
                # Claude Code 会在历史中回传 redacted_thinking，Kiro 不支持
                # 静默跳过，不影响 tool_use 配对
                pass
            elif bt == "text":
                text_content += item.get("text", "")
            elif bt in ("tool_use", "server_tool_use"):
                tid = item.get("id")
                name = item.get("name")
                if tid and name:
                    mapped_name = _map_tool_name(name, tool_name_map) if tool_name_map is not None else name
                    inp = item.get("input", {})
                    te = ToolUseEntry(tool_use_id=tid, name=mapped_name, input=inp)
                    tool_uses.append(te)
            # connector_text, web_search_tool_result, tool_result 等静默跳过

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


def _merge_assistant_messages(
    messages: List[AnthropicMessage],
    tool_name_map: Optional[Dict[str, str]] = None,
) -> HistoryAssistantMessage:
    if len(messages) == 1:
        return _convert_assistant_message(messages[0], tool_name_map)
    all_tool_uses: List[ToolUseEntry] = []
    content_parts: List[str] = []
    for msg in messages:
        converted = _convert_assistant_message(msg, tool_name_map)
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
            tool_results=_dedupe_tool_results(all_tool_results),
        )
    return HistoryUserMessage(user_input_message=user_msg)


def _process_history_tools(
    history: List[Message], current_tool_results: List[ToolResult],
) -> Tuple[List[str], List[ToolResult]]:
    """收集 history 中出现过的工具名，并对当前 tool_results 做去重。

    A2 不会像当前实现这样在本地强制修复 tool_use/tool_result 配对。
    这里避免删除 history 中的 tool_use 或丢弃当前 tool_result，
    以免长对话续接时把关键状态“修坏”。
    """
    tool_names: List[str] = []
    tool_names_seen: Set[str] = set()

    for msg in history:
        inner = msg._data
        if isinstance(inner, HistoryAssistantMessage):
            tool_uses = inner.assistant_response_message.tool_uses
            if tool_uses:
                for tu in tool_uses:
                    if tu.name and tu.name not in tool_names_seen:
                        tool_names.append(tu.name)
                        tool_names_seen.add(tu.name)

    return tool_names, _dedupe_tool_results(current_tool_results)


def _build_history(
    req: MessagesRequest, messages: List[AnthropicMessage], model_id: str,
    tool_name_map: Optional[Dict[str, str]] = None,
) -> List[Message]:
    """按 A2 风格构建 history。"""
    history: List[Message] = []
    thinking_prefix = _generate_thinking_prefix(req)
    processed_messages = _merge_adjacent_messages(messages)
    if not processed_messages:
        return history
    start_index = 0

    # 系统消息作为独立的 user + assistant 配对注入（与 Rust 实现一致）
    # 不再合并到第一条 user 消息中，避免破坏消息结构
    if req.system:
        system_content = "\n".join(
            s.get("text", "") for s in req.system if s.get("text")
        )
        if system_content:
            # 追加分块写入策略
            system_content = f"{system_content}\n{SYSTEM_CHUNKED_POLICY}"
            # 注入 thinking 标签到系统消息最前面
            if thinking_prefix and not _has_thinking_tags(system_content):
                system_content = f"{thinking_prefix}\n{system_content}"
            # 系统消息作为 user + assistant("I will follow these instructions.") 配对
            history.append(Message(HistoryUserMessage(
                user_input_message=UserMessage.new(system_content, model_id),
            )))
            history.append(Message(HistoryAssistantMessage(
                assistant_response_message=AssistantMessage.new("I will follow these instructions."),
            )))
    elif thinking_prefix:
        # 没有系统消息但有 thinking 配置，也需要 user + assistant 配对
        history.append(Message(HistoryUserMessage(
            user_input_message=UserMessage.new(thinking_prefix, model_id),
        )))
        history.append(Message(HistoryAssistantMessage(
            assistant_response_message=AssistantMessage.new("I will follow these instructions."),
        )))
    history_end = len(processed_messages) - 1
    for i in range(start_index, history_end):
        msg = processed_messages[i]
        history_distance = history_end - i
        if msg.role == "user":
            history.append(_convert_history_user_message(msg, model_id, history_distance))
        elif msg.role == "assistant":
            history.append(_convert_history_assistant_message(msg, tool_name_map))

    return history


def convert_request(req: MessagesRequest) -> ConversionResult:
    """将 Anthropic MessagesRequest 转换为 Kiro ConversationState"""
    model_id = map_model(req.model)
    if model_id is None:
        raise UnsupportedModelError(req.model)

    raw_messages = req.get_messages()
    if not raw_messages:
        raise EmptyMessagesError()
    messages = _merge_adjacent_messages(raw_messages)

    # Rust parity: 静默丢弃末尾的 assistant 消息（prefill），截断到最后一条 user 消息
    while messages and messages[-1].role == "assistant":
        logger.info("静默丢弃末尾 assistant 消息 (prefill)，与 Rust 行为一致")
        messages.pop()
    if not messages:
        raise EmptyMessagesError()

    # 工具名称映射表 (short → original)
    tool_name_map: Dict[str, str] = {}

    # 提取 session_id
    session_id = None
    meta = req.get_metadata()
    if meta and meta.user_id:
        session_id = _extract_session_id(meta.user_id)
    conversation_id = session_id or str(uuid.uuid4())

    # 转换工具定义（自动缩短超长名称）
    tools = _convert_tools(req.tools, tool_name_map)

    # 构建历史消息（工具名称也要映射）
    history = _build_history(req, messages, model_id, tool_name_map)

    # 处理最后一条消息作为 current_message
    last_msg = messages[-1]
    current_text, current_images, current_tool_results = _process_message_content(
        last_msg.content,
        keep_images=True,
        image_placeholder=False,
        history_distance=None,
    )
    if not current_text:
        current_text = CURRENT_MESSAGE_PLACEHOLDER if current_tool_results else CONTINUE_PLACEHOLDER

    # 工具配对验证 + 孤立工具清理
    deduped_results = _dedupe_tool_results(current_tool_results)
    validated_tool_results, orphaned_ids = _validate_tool_pairing(history, deduped_results)
    if orphaned_ids:
        _remove_orphaned_tool_uses(history, orphaned_ids)

    # 收集历史中使用的工具名，为缺失的工具生成占位定义
    history_tool_names = _collect_history_tool_names(history)
    existing_tool_names = {t.tool_specification.name for t in tools}
    for tn in history_tool_names:
        if tn not in existing_tool_names:
            tools.append(_create_placeholder_tool(tn))
            existing_tool_names.add(tn)

    # 构建 UserInputMessageContext
    context = UserInputMessageContext()
    if tools:
        context.tools = tools
    if validated_tool_results:
        context.tool_results = validated_tool_results

    # 构建当前消息
    current_msg = UserInputMessage.new(current_text or CONTINUE_PLACEHOLDER, model_id)
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

    return ConversionResult(conversation_state=state, tool_name_map=tool_name_map)
