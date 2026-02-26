"""工具使用事件 - 参考 src/kiro/model/events/tool_use.rs"""

import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ToolUseEvent:
    name: str = ""
    tool_use_id: str = ""
    input: str = ""
    stop: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "ToolUseEvent":
        raw_input = data.get("input", "")
        # Rust 版 input 是 String 类型，非字符串值会导致 serde 反序列化失败
        # Python 侧需要手动保证类型安全
        if raw_input is None:
            raw_input = ""
        elif not isinstance(raw_input, str):
            # Kiro 偶尔可能返回 JSON 对象而非字符串片段，转为 JSON 字符串
            logger.warning("ToolUseEvent.input 类型异常: %s (type=%s), 转为 JSON 字符串",
                           repr(raw_input)[:200], type(raw_input).__name__)
            try:
                raw_input = json.dumps(raw_input, ensure_ascii=False)
            except (TypeError, ValueError):
                raw_input = ""
        return cls(
            name=data.get("name", ""),
            tool_use_id=data.get("toolUseId", ""),
            input=raw_input,
            stop=bool(data.get("stop", False)),
        )
