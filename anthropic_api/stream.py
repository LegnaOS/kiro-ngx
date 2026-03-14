"""流式响应处理模块 - 参考 src/anthropic/stream.rs

实现 Kiro -> Anthropic 流式响应转换和 SSE 状态管理
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 需要跳过的包裹字符（当 thinking 标签被这些字符包裹时，认为是引用而非真正标签）
QUOTE_CHARS = {ord(c) for c in '`"\'\\#!@$%^&*()-_=+[]{};:<>,.?/'}

CONTEXT_WINDOW_SIZE = 200_000


class IncompleteToolUseError(RuntimeError):
    """流结束时 tool_use 未完整闭合。"""


def _is_quote_char(buffer: str, pos: int) -> bool:
    if 0 <= pos < len(buffer):
        return ord(buffer[pos]) < 128 and ord(buffer[pos]) in QUOTE_CHARS
    return False


def find_real_thinking_start_tag(buffer: str) -> Optional[int]:
    """查找真正的 <thinking> 开始标签（不被引用字符包裹）"""
    tag = "<thinking>"
    search_start = 0
    while True:
        pos = buffer.find(tag, search_start)
        if pos == -1:
            return None
        has_quote_before = pos > 0 and _is_quote_char(buffer, pos - 1)
        after_pos = pos + len(tag)
        has_quote_after = _is_quote_char(buffer, after_pos)
        if not has_quote_before and not has_quote_after:
            return pos
        search_start = pos + 1


def find_real_thinking_end_tag(buffer: str) -> Optional[int]:
    """查找真正的 </thinking> 结束标签（后面有双换行符）"""
    tag = "</thinking>"
    search_start = 0
    while True:
        pos = buffer.find(tag, search_start)
        if pos == -1:
            return None
        has_quote_before = pos > 0 and _is_quote_char(buffer, pos - 1)
        after_pos = pos + len(tag)
        has_quote_after = _is_quote_char(buffer, after_pos)
        if has_quote_before or has_quote_after:
            search_start = pos + 1
            continue
        after_content = buffer[after_pos:]
        if len(after_content) < 2:
            return None
        if after_content.startswith("\n\n"):
            return pos
        search_start = pos + 1


def _find_real_thinking_end_tag_at_buffer_end(buffer: str) -> Optional[int]:
    """查找缓冲区末尾的 thinking 结束标签（允许末尾只有空白字符）"""
    tag = "</thinking>"
    search_start = 0
    while True:
        pos = buffer.find(tag, search_start)
        if pos == -1:
            return None
        has_quote_before = pos > 0 and _is_quote_char(buffer, pos - 1)
        after_pos = pos + len(tag)
        has_quote_after = _is_quote_char(buffer, after_pos)
        if has_quote_before or has_quote_after:
            search_start = pos + 1
            continue
        if buffer[after_pos:].strip() == "":
            return pos
        search_start = pos + 1


@dataclass
class SseEvent:
    """SSE 事件"""
    event: str
    data: Any

    def to_sse_string(self) -> str:
        return f"event: {self.event}\ndata: {json.dumps(self.data, ensure_ascii=False)}\n\n"


@dataclass
class _BlockState:
    block_type: str
    started: bool = False
    stopped: bool = False


class SseStateManager:
    """SSE 状态管理器 - 确保事件序列符合 Claude API 规范"""

    def __init__(self):
        self.message_started = False
        self.message_delta_sent = False
        self.active_blocks: Dict[int, _BlockState] = {}
        self.message_ended = False
        self.next_block_idx = 0
        self.stop_reason: Optional[str] = None
        self.has_tool_use = False

    def _is_block_open_of_type(self, index: int, expected_type: str) -> bool:
        b = self.active_blocks.get(index)
        return b is not None and b.started and not b.stopped and b.block_type == expected_type

    def next_block_index(self) -> int:
        idx = self.next_block_idx
        self.next_block_idx += 1
        return idx

    def set_has_tool_use(self, has: bool):
        self.has_tool_use = has

    def set_stop_reason(self, reason: str):
        self.stop_reason = reason

    def _has_non_thinking_blocks(self) -> bool:
        return any(b.block_type != "thinking" for b in self.active_blocks.values())

    def get_stop_reason(self) -> str:
        if self.stop_reason:
            return self.stop_reason
        return "tool_use" if self.has_tool_use else "end_turn"

    def handle_message_start(self, event_data: dict) -> Optional[SseEvent]:
        if self.message_started:
            return None
        self.message_started = True
        return SseEvent(event="message_start", data=event_data)

    def handle_content_block_start(self, index: int, block_type: str, data: dict) -> List[SseEvent]:
        events: List[SseEvent] = []
        # tool_use 块开始前，自动关闭之前的 text 块
        if block_type == "tool_use":
            self.has_tool_use = True
            for bi, block in self.active_blocks.items():
                if block.block_type == "text" and block.started and not block.stopped:
                    events.append(SseEvent("content_block_stop", {"type": "content_block_stop", "index": bi}))
                    block.stopped = True
        existing = self.active_blocks.get(index)
        if existing and existing.started:
            return events
        bs = _BlockState(block_type=block_type, started=True)
        self.active_blocks[index] = bs
        events.append(SseEvent("content_block_start", data))
        return events

    def handle_content_block_delta(self, index: int, data: dict) -> Optional[SseEvent]:
        block = self.active_blocks.get(index)
        if not block or not block.started or block.stopped:
            return None
        return SseEvent("content_block_delta", data)

    def handle_content_block_stop(self, index: int) -> Optional[SseEvent]:
        block = self.active_blocks.get(index)
        if not block or block.stopped:
            return None
        block.stopped = True
        return SseEvent("content_block_stop", {"type": "content_block_stop", "index": index})

    def generate_final_events(self, input_tokens: int, output_tokens: int) -> List[SseEvent]:
        events: List[SseEvent] = []
        for idx, block in self.active_blocks.items():
            if block.started and not block.stopped:
                events.append(SseEvent("content_block_stop", {"type": "content_block_stop", "index": idx}))
                block.stopped = True
        if not self.message_delta_sent:
            self.message_delta_sent = True
            events.append(SseEvent("message_delta", {
                "type": "message_delta",
                "delta": {"stop_reason": self.get_stop_reason(), "stop_sequence": None},
                "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
            }))
        if not self.message_ended:
            self.message_ended = True
            events.append(SseEvent("message_stop", {"type": "message_stop"}))
        return events


def estimate_tokens(text: str) -> int:
    """token 近似估算：中文约 1.5 字符/token，其他约 4 字符/token"""
    chinese_count = 0
    other_count = 0
    for c in text:
        if '\u4e00' <= c <= '\u9fff':
            chinese_count += 1
        else:
            other_count += 1
    chinese_tokens = (chinese_count * 2 + 2) // 3
    other_tokens = (other_count + 3) // 4
    return max(chinese_tokens + other_tokens, 1)


class StreamContext:
    """流处理上下文 - 处理 Kiro 事件并转换为 Anthropic SSE 事件"""

    def __init__(self, model: str, input_tokens: int, thinking_enabled: bool = False):
        self.state_manager = SseStateManager()
        self.model = model
        self.message_id = f"msg_{uuid.uuid4().hex}"
        self.input_tokens = input_tokens
        self.context_input_tokens: Optional[int] = None
        self.context_total_tokens: Optional[int] = None
        self.output_tokens = 0
        self.tool_block_indices: Dict[str, int] = {}
        self.thinking_enabled = thinking_enabled
        self.thinking_buffer = ""
        self.in_thinking_block = False
        self.thinking_extracted = False
        self.thinking_block_index: Optional[int] = None
        self.text_block_index: Optional[int] = None
        self._strip_thinking_leading_newline = False
        self._accumulated_text_parts: List[str] = []  # 累积文本片段，用于日志记录
        self.web_search_tool_uses: List[Dict[str, Any]] = []  # 记录 web_search tool_use
        self._tool_json_buffers: Dict[str, str] = {}  # tool_use_id → 累积的 JSON 字符串
        self._tool_names: Dict[str, str] = {}  # tool_use_id -> 最近一次观察到的工具名
        self._last_assistant_content: Optional[str] = None

    @property
    def accumulated_text(self) -> str:
        """延迟 join 累积文本"""
        return "".join(self._accumulated_text_parts)

    def create_message_start_event(self) -> dict:
        return {
            "type": "message_start",
            "message": {
                "id": self.message_id, "type": "message", "role": "assistant",
                "content": [], "model": self.model,
                "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": self.input_tokens, "output_tokens": 1},
            },
        }

    def generate_initial_events(self) -> List[SseEvent]:
        events: List[SseEvent] = []
        msg_start = self.create_message_start_event()
        evt = self.state_manager.handle_message_start(msg_start)
        if evt:
            events.append(evt)
        if self.thinking_enabled:
            return events
        # 创建初始文本块
        idx = self.state_manager.next_block_index()
        self.text_block_index = idx
        events.extend(self.state_manager.handle_content_block_start(idx, "text", {
            "type": "content_block_start", "index": idx,
            "content_block": {"type": "text", "text": ""},
        }))
        return events

    def process_kiro_event(self, event) -> List[SseEvent]:
        """处理 Kiro 事件并转换为 Anthropic SSE 事件"""
        from kiro.model.events.assistant import AssistantResponseEvent
        from kiro.model.events.tool_use import ToolUseEvent
        from kiro.model.events.context_usage import ContextUsageEvent

        if isinstance(event, AssistantResponseEvent):
            if event.content and event.content == self._last_assistant_content:
                return []
            self._last_assistant_content = event.content
            return self._process_assistant_response(event.content)
        elif isinstance(event, ToolUseEvent):
            self._last_assistant_content = None
            try:
                return self._process_tool_use(event)
            except Exception:
                # 单个 tool_use 事件处理失败不应中断整个流
                logger.error("处理 ToolUseEvent 异常 (id=%s, name=%s, input_type=%s, input_len=%s, stop=%s)",
                             event.tool_use_id, event.name,
                             type(event.input).__name__,
                             len(event.input) if isinstance(event.input, str) else "N/A",
                             event.stop,
                             exc_info=True)
                return []
        elif isinstance(event, ContextUsageEvent):
            self._last_assistant_content = None
            actual = int(event.context_usage_percentage * CONTEXT_WINDOW_SIZE / 100.0)
            self.context_total_tokens = actual
            if event.context_usage_percentage >= 100.0:
                self.state_manager.set_stop_reason("model_context_window_exceeded")
            return []
        # 处理 dict 格式的事件（异常/错误）
        elif isinstance(event, dict):
            self._last_assistant_content = None
            etype = event.get("type", "")
            if etype == "exception":
                if event.get("exception_type") == "ContentLengthExceededException":
                    self.state_manager.set_stop_reason("max_tokens")
            return []
        return []

    def _process_assistant_response(self, content: str) -> List[SseEvent]:
        if not content:
            return []
        self.output_tokens += estimate_tokens(content)
        if self.thinking_enabled:
            return self._process_content_with_thinking(content)
        return self._create_text_delta_events(content)

    def _process_content_with_thinking(self, content: str) -> List[SseEvent]:
        events: List[SseEvent] = []
        self.thinking_buffer += content

        while True:
            if not self.in_thinking_block and not self.thinking_extracted:
                start_pos = find_real_thinking_start_tag(self.thinking_buffer)
                if start_pos is not None:
                    before = self.thinking_buffer[:start_pos]
                    if before and before.strip():
                        events.extend(self._create_text_delta_events(before))
                    self.in_thinking_block = True
                    self._strip_thinking_leading_newline = True
                    self.thinking_buffer = self.thinking_buffer[start_pos + len("<thinking>"):]
                    # 创建 thinking 块
                    ti = self.state_manager.next_block_index()
                    self.thinking_block_index = ti
                    events.extend(self.state_manager.handle_content_block_start(ti, "thinking", {
                        "type": "content_block_start", "index": ti,
                        "content_block": {"type": "thinking", "thinking": ""},
                    }))
                else:
                    # 保留可能是部分标签的内容
                    safe_len = max(0, len(self.thinking_buffer) - len("<thinking>"))
                    if safe_len > 0:
                        safe_content = self.thinking_buffer[:safe_len]
                        if safe_content and safe_content.strip():
                            events.extend(self._create_text_delta_events(safe_content))
                            self.thinking_buffer = self.thinking_buffer[safe_len:]
                    break

            elif self.in_thinking_block:
                # 剥离 <thinking> 后紧跟的换行符
                if self._strip_thinking_leading_newline:
                    if self.thinking_buffer.startswith("\n"):
                        self.thinking_buffer = self.thinking_buffer[1:]
                        self._strip_thinking_leading_newline = False
                    elif self.thinking_buffer:
                        self._strip_thinking_leading_newline = False

                end_pos = find_real_thinking_end_tag(self.thinking_buffer)
                if end_pos is not None:
                    thinking_content = self.thinking_buffer[:end_pos]
                    if thinking_content and self.thinking_block_index is not None:
                        events.append(self._create_thinking_delta(self.thinking_block_index, thinking_content))
                    self.in_thinking_block = False
                    self.thinking_extracted = True
                    if self.thinking_block_index is not None:
                        events.append(self._create_thinking_delta(self.thinking_block_index, ""))
                        stop = self.state_manager.handle_content_block_stop(self.thinking_block_index)
                        if stop:
                            events.append(stop)
                    self.thinking_buffer = self.thinking_buffer[end_pos + len("</thinking>\n\n"):]
                else:
                    # 保留末尾可能是部分 </thinking>\n\n 的内容
                    safe_len = max(0, len(self.thinking_buffer) - len("</thinking>\n\n"))
                    if safe_len > 0:
                        safe_content = self.thinking_buffer[:safe_len]
                        if safe_content and self.thinking_block_index is not None:
                            events.append(self._create_thinking_delta(self.thinking_block_index, safe_content))
                        self.thinking_buffer = self.thinking_buffer[safe_len:]
                    break
            else:
                # thinking 已提取完成，剩余内容作为 text_delta
                if self.thinking_buffer:
                    remaining = self.thinking_buffer
                    self.thinking_buffer = ""
                    events.extend(self._create_text_delta_events(remaining))
                break

        return events

    def _create_text_delta_events(self, text: str) -> List[SseEvent]:
        events: List[SseEvent] = []
        self._accumulated_text_parts.append(text)  # 累积文本用于日志
        # 如果当前 text block 已被关闭，丢弃索引
        if self.text_block_index is not None:
            if not self.state_manager._is_block_open_of_type(self.text_block_index, "text"):
                self.text_block_index = None
        # 获取或创建文本块
        if self.text_block_index is None:
            idx = self.state_manager.next_block_index()
            self.text_block_index = idx
            events.extend(self.state_manager.handle_content_block_start(idx, "text", {
                "type": "content_block_start", "index": idx,
                "content_block": {"type": "text", "text": ""},
            }))
        idx = self.text_block_index
        delta = self.state_manager.handle_content_block_delta(idx, {
            "type": "content_block_delta", "index": idx,
            "delta": {"type": "text_delta", "text": text},
        })
        if delta:
            events.append(delta)
        return events

    def _create_thinking_delta(self, index: int, thinking: str) -> SseEvent:
        return SseEvent("content_block_delta", {
            "type": "content_block_delta", "index": index,
            "delta": {"type": "thinking_delta", "thinking": thinking},
        })

    def _process_tool_use(self, tool_use) -> List[SseEvent]:
        events: List[SseEvent] = []

        # 处理 thinking 块在 tool_use 之前的边界场景
        if self.thinking_enabled and self.in_thinking_block:
            end_pos = _find_real_thinking_end_tag_at_buffer_end(self.thinking_buffer)
            if end_pos is not None:
                thinking_content = self.thinking_buffer[:end_pos]
                if thinking_content and self.thinking_block_index is not None:
                    events.append(self._create_thinking_delta(self.thinking_block_index, thinking_content))
                self.in_thinking_block = False
                self.thinking_extracted = True
                if self.thinking_block_index is not None:
                    events.append(self._create_thinking_delta(self.thinking_block_index, ""))
                    stop = self.state_manager.handle_content_block_stop(self.thinking_block_index)
                    if stop:
                        events.append(stop)
                after_pos = end_pos + len("</thinking>")
                remaining = self.thinking_buffer[after_pos:].lstrip()
                self.thinking_buffer = ""
                if remaining:
                    events.extend(self._create_text_delta_events(remaining))

        # flush thinking_buffer 中暂存的文本
        if (self.thinking_enabled and not self.in_thinking_block
                and not self.thinking_extracted and self.thinking_buffer):
            buffered = self.thinking_buffer
            self.thinking_buffer = ""
            events.extend(self._create_text_delta_events(buffered))

        # 参数分片先缓存；仅在 stop=true 时对下游输出完整 tool_use，避免半截 tool_call 污染会话。
        if tool_use.tool_use_id and tool_use.name:
            self._tool_names[tool_use.tool_use_id] = tool_use.name
        if tool_use.input:
            if not isinstance(tool_use.input, str):
                logger.warning("流式 ToolUseEvent.input 类型异常: id=%s, name=%s, type=%s",
                               tool_use.tool_use_id, tool_use.name, type(tool_use.input).__name__)
            else:
                self.output_tokens += (len(tool_use.input) + 3) // 4
                buf = self._tool_json_buffers.get(tool_use.tool_use_id, "")
                buf += tool_use.input
                self._tool_json_buffers[tool_use.tool_use_id] = buf

        # 完整工具调用
        if tool_use.stop:
            self.state_manager.set_has_tool_use(True)
            final_input = self._tool_json_buffers.get(tool_use.tool_use_id, "")
            if not final_input:
                logger.warning("ToolUseEvent stop=true 但 JSON 缓冲区为空: id=%s, name=%s",
                               tool_use.tool_use_id, tool_use.name)

            # 获取或分配块索引
            block_index = self.tool_block_indices.get(tool_use.tool_use_id)
            if block_index is None:
                block_index = self.state_manager.next_block_index()
                self.tool_block_indices[tool_use.tool_use_id] = block_index

            # content_block_start
            events.extend(self.state_manager.handle_content_block_start(block_index, "tool_use", {
                "type": "content_block_start", "index": block_index,
                "content_block": {
                    "type": "tool_use", "id": tool_use.tool_use_id,
                    "name": tool_use.name, "input": {},
                },
            }))

            if final_input:
                delta = self.state_manager.handle_content_block_delta(block_index, {
                    "type": "content_block_delta", "index": block_index,
                    "delta": {"type": "input_json_delta", "partial_json": final_input},
                })
                if delta:
                    events.append(delta)

            stop = self.state_manager.handle_content_block_stop(block_index)
            if stop:
                events.append(stop)
            # 记录 web_search tool_use
            if tool_use.name == "web_search":
                input_json = final_input
                self.web_search_tool_uses.append({
                    "tool_use_id": tool_use.tool_use_id,
                    "name": tool_use.name,
                    "input_json": input_json,
                })
            self._tool_json_buffers.pop(tool_use.tool_use_id, None)
            self.tool_block_indices.pop(tool_use.tool_use_id, None)
            self._tool_names.pop(tool_use.tool_use_id, None)

        return events

    def generate_final_events(self) -> List[SseEvent]:
        events: List[SseEvent] = []

        # Flush thinking_buffer 中的剩余内容
        if self.thinking_enabled and self.thinking_buffer:
            if self.in_thinking_block:
                end_pos = _find_real_thinking_end_tag_at_buffer_end(self.thinking_buffer)
                if end_pos is not None:
                    thinking_content = self.thinking_buffer[:end_pos]
                    if thinking_content and self.thinking_block_index is not None:
                        events.append(self._create_thinking_delta(self.thinking_block_index, thinking_content))
                    if self.thinking_block_index is not None:
                        events.append(self._create_thinking_delta(self.thinking_block_index, ""))
                        stop = self.state_manager.handle_content_block_stop(self.thinking_block_index)
                        if stop:
                            events.append(stop)
                    after_pos = end_pos + len("</thinking>")
                    remaining = self.thinking_buffer[after_pos:].lstrip()
                    self.thinking_buffer = ""
                    self.in_thinking_block = False
                    self.thinking_extracted = True
                    if remaining:
                        events.extend(self._create_text_delta_events(remaining))
                else:
                    if self.thinking_block_index is not None:
                        events.append(self._create_thinking_delta(self.thinking_block_index, self.thinking_buffer))
                        events.append(self._create_thinking_delta(self.thinking_block_index, ""))
                        stop = self.state_manager.handle_content_block_stop(self.thinking_block_index)
                        if stop:
                            events.append(stop)
            else:
                events.extend(self._create_text_delta_events(self.thinking_buffer))
            self.thinking_buffer = ""

        # 只有 thinking 块时，补发 text 块并设置 max_tokens
        if (self.thinking_enabled and self.thinking_block_index is not None
                and not self.state_manager._has_non_thinking_blocks()):
            self.state_manager.set_stop_reason("max_tokens")
            events.extend(self._create_text_delta_events(" "))

        # 向 A2 靠拢：未完成的 tool_use 在流末尾尽量收尾输出，而不是直接抛异常炸流。
        if self._tool_json_buffers:
            for tid in list(self._tool_json_buffers.keys()):
                final_input = self._tool_json_buffers.get(tid, "")
                tool_name = self._tool_names.get(tid, "") or "unknown_tool"
                pending_len = len(final_input)
                logger.warning(
                    "检测到未完成 tool_use，按 A2 风格收尾输出: tool_use_id=%s, name=%s, pending_input_len=%d",
                    tid, tool_name, pending_len,
                )
                self.state_manager.set_has_tool_use(True)
                block_index = self.tool_block_indices.get(tid)
                if block_index is None:
                    block_index = self.state_manager.next_block_index()
                    self.tool_block_indices[tid] = block_index
                events.extend(self.state_manager.handle_content_block_start(block_index, "tool_use", {
                    "type": "content_block_start", "index": block_index,
                    "content_block": {
                        "type": "tool_use", "id": tid,
                        "name": tool_name, "input": {},
                    },
                }))
                if final_input:
                    delta = self.state_manager.handle_content_block_delta(block_index, {
                        "type": "content_block_delta", "index": block_index,
                        "delta": {"type": "input_json_delta", "partial_json": final_input},
                    })
                    if delta:
                        events.append(delta)
                stop = self.state_manager.handle_content_block_stop(block_index)
                if stop:
                    events.append(stop)
                self._tool_json_buffers.pop(tid, None)
                self.tool_block_indices.pop(tid, None)
                self._tool_names.pop(tid, None)

        final_input = self.resolve_input_tokens()
        events.extend(self.state_manager.generate_final_events(final_input, self.output_tokens))
        return events

    def resolve_input_tokens(self) -> int:
        if self.context_total_tokens is not None:
            self.context_input_tokens = max(self.context_total_tokens - self.output_tokens, 0)
        return self.context_input_tokens if self.context_input_tokens is not None else self.input_tokens


class BufferedStreamContext:
    """缓冲流处理上下文 - 用于 /cc/v1/messages 端点

    缓冲所有事件直到流结束，然后用 contextUsageEvent 的正确 input_tokens 更正 message_start。
    """

    def __init__(self, model: str, estimated_input_tokens: int, thinking_enabled: bool = False):
        self.inner = StreamContext(model, estimated_input_tokens, thinking_enabled)
        self.event_buffer: List[SseEvent] = []
        self.estimated_input_tokens = estimated_input_tokens
        self._initial_events_generated = False

    def process_and_buffer(self, event) -> None:
        if not self._initial_events_generated:
            self.event_buffer.extend(self.inner.generate_initial_events())
            self._initial_events_generated = True
        self.event_buffer.extend(self.inner.process_kiro_event(event))

    def get_web_search_tool_uses(self) -> List[Dict[str, Any]]:
        """获取响应中的 web_search tool_use 列表"""
        return self.inner.web_search_tool_uses

    def finish_and_get_all_events(self) -> List[SseEvent]:
        if not self._initial_events_generated:
            self.event_buffer.extend(self.inner.generate_initial_events())
            self._initial_events_generated = True

        self.event_buffer.extend(self.inner.generate_final_events())

        final_input = self.inner.resolve_input_tokens()

        # 更正 message_start 中的 input_tokens
        for evt in self.event_buffer:
            if evt.event == "message_start":
                msg = evt.data.get("message")
                if msg and "usage" in msg:
                    msg["usage"]["input_tokens"] = final_input

        result = self.event_buffer
        self.event_buffer = []
        return result
