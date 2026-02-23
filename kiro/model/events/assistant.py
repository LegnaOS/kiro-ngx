"""助手响应事件 - 参考 src/kiro/model/events/assistant.rs"""

from dataclasses import dataclass, field


@dataclass
class AssistantResponseEvent:
    content: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "AssistantResponseEvent":
        return cls(content=data.get("content", ""))
