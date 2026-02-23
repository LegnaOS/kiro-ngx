"""上下文使用率事件 - 参考 src/kiro/model/events/context_usage.rs"""

from dataclasses import dataclass


@dataclass
class ContextUsageEvent:
    context_usage_percentage: float = 0.0

    @classmethod
    def from_dict(cls, data: dict) -> "ContextUsageEvent":
        return cls(context_usage_percentage=data.get("contextUsagePercentage", 0.0))

    def formatted_percentage(self) -> str:
        return f"{self.context_usage_percentage:.2f}%"
