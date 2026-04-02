"""Anthropic API 类型定义 - 参考 src/anthropic/types.rs"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, field_validator

MAX_BUDGET_TOKENS = 24576  # 对齐 Go/kiro.rs，降低 thinking output 消耗


# === 错误响应 ===

@dataclass
class ErrorDetail:
    type: str
    message: str


@dataclass
class ErrorResponse:
    error: ErrorDetail

    @classmethod
    def new(cls, error_type: str, message: str) -> "ErrorResponse":
        return cls(error=ErrorDetail(type=error_type, message=message))

    @classmethod
    def authentication_error(cls) -> "ErrorResponse":
        return cls.new("authentication_error", "Invalid API key")

    def to_dict(self) -> dict:
        return {"error": {"type": self.error.type, "message": self.error.message}}


# === Models 端点类型 ===

@dataclass
class Model:
    id: str
    object: str
    created: int
    owned_by: str
    display_name: str
    type: str
    max_tokens: int

    def to_dict(self) -> dict:
        return {
            "id": self.id, "object": self.object, "created": self.created,
            "owned_by": self.owned_by, "display_name": self.display_name,
            "type": self.type, "max_tokens": self.max_tokens,
        }


@dataclass
class ModelsResponse:
    object: str = "list"
    data: List[Model] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"object": self.object, "data": [m.to_dict() for m in self.data]}


# === Messages 端点类型 ===

@dataclass
class Thinking:
    type: str = "disabled"
    budget_tokens: int = 20000

    def __post_init__(self):
        self.budget_tokens = min(self.budget_tokens, MAX_BUDGET_TOKENS)

    def is_enabled(self) -> bool:
        return self.type in ("enabled", "adaptive")


@dataclass
class OutputConfig:
    effort: str = "high"


@dataclass
class Metadata:
    user_id: Optional[str] = None


@dataclass
class SystemMessage:
    text: str = ""


@dataclass
class AnthropicMessage:
    """Anthropic 格式的消息"""
    role: str = ""
    content: Any = ""


@dataclass
class ImageSource:
    type: str = ""
    media_type: str = ""
    data: str = ""


@dataclass
class ContentBlock:
    type: str = ""
    text: Optional[str] = None
    thinking: Optional[str] = None
    tool_use_id: Optional[str] = None
    content: Optional[Any] = None
    name: Optional[str] = None
    input: Optional[Any] = None
    id: Optional[str] = None
    is_error: Optional[bool] = None
    source: Optional[ImageSource] = None


@dataclass
class Tool:
    """工具定义，支持普通工具和 WebSearch 工具"""
    type: Optional[str] = None
    name: str = ""
    description: str = ""
    input_schema: Dict[str, Any] = field(default_factory=dict)
    max_uses: Optional[int] = None

    def is_web_search(self) -> bool:
        return self.type is not None and self.type.startswith("web_search")


class MessagesRequest(BaseModel):
    """Messages 请求体（使用 pydantic 支持 system 字段的灵活反序列化）"""
    model: str
    max_tokens: int
    messages: List[Dict[str, Any]]
    stream: bool = False
    system: Optional[List[Dict[str, Any]]] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Any] = None
    thinking: Optional[Dict[str, Any]] = None
    output_config: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None

    @field_validator("system", mode="before")
    @classmethod
    def normalize_system(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            return [{"text": v}]
        if isinstance(v, list):
            return v if v else None
        return v

    # --- 便捷属性 ---

    def get_thinking(self) -> Optional[Thinking]:
        if self.thinking is None:
            return None
        return Thinking(
            type=self.thinking.get("type", "disabled"),
            budget_tokens=min(self.thinking.get("budget_tokens", 20000), MAX_BUDGET_TOKENS),
        )

    def get_output_config(self) -> Optional[OutputConfig]:
        if self.output_config is None:
            return None
        return OutputConfig(effort=self.output_config.get("effort", "high"))

    def get_metadata(self) -> Optional[Metadata]:
        if self.metadata is None:
            return None
        return Metadata(user_id=self.metadata.get("user_id"))

    def get_system_messages(self) -> Optional[List[SystemMessage]]:
        if self.system is None:
            return None
        return [SystemMessage(text=s.get("text", "")) for s in self.system]

    def get_messages(self) -> List[AnthropicMessage]:
        return [AnthropicMessage(role=m.get("role", ""), content=m.get("content", "")) for m in self.messages]

    def get_tools(self) -> Optional[List[Tool]]:
        if self.tools is None:
            return None
        result = []
        for t in self.tools:
            result.append(Tool(
                type=t.get("type"),
                name=t.get("name", ""),
                description=t.get("description", ""),
                input_schema=t.get("input_schema", {}),
                max_uses=t.get("max_uses"),
            ))
        return result


# === Count Tokens 端点类型 ===

class CountTokensRequest(BaseModel):
    model: str
    messages: List[Dict[str, Any]]
    system: Optional[List[Dict[str, Any]]] = None
    tools: Optional[List[Dict[str, Any]]] = None

    @field_validator("system", mode="before")
    @classmethod
    def normalize_system(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            return [{"text": v}]
        if isinstance(v, list):
            return v if v else None
        return v


@dataclass
class CountTokensResponse:
    input_tokens: int = 0

    def to_dict(self) -> dict:
        return {"input_tokens": self.input_tokens}
