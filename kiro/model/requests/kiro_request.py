"""Kiro 请求类型定义 - 参考 src/kiro/model/requests/kiro.rs"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .conversation import ConversationState


@dataclass
class KiroRequest:
    """Kiro API 请求"""
    conversation_state: ConversationState = field(default_factory=ConversationState)
    profile_arn: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "KiroRequest":
        return cls(
            conversation_state=ConversationState.from_dict(data.get("conversationState", {})),
            profile_arn=data.get("profileArn"),
        )

    def to_dict(self) -> dict:
        d: Dict[str, Any] = {
            "conversationState": self.conversation_state.to_dict(),
        }
        if self.profile_arn is not None:
            d["profileArn"] = self.profile_arn
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)
