"""工具使用事件 - 参考 src/kiro/model/events/tool_use.rs"""

from dataclasses import dataclass


@dataclass
class ToolUseEvent:
    name: str = ""
    tool_use_id: str = ""
    input: str = ""
    stop: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "ToolUseEvent":
        return cls(
            name=data.get("name", ""),
            tool_use_id=data.get("toolUseId", ""),
            input=data.get("input", ""),
            stop=data.get("stop", False),
        )
