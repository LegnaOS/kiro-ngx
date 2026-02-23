"""事件基础定义 - 参考 src/kiro/model/events/base.rs"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class EventType(Enum):
    ASSISTANT_RESPONSE = "assistantResponseEvent"
    TOOL_USE = "toolUseEvent"
    METERING = "meteringEvent"
    CONTEXT_USAGE = "contextUsageEvent"
    UNKNOWN = "unknown"

    @classmethod
    def from_str(cls, s: str) -> "EventType":
        for member in cls:
            if member.value == s:
                return member
        return cls.UNKNOWN
