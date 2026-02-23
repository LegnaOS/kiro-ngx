"""工具类型定义 - 参考 src/kiro/model/requests/tool.rs"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class InputSchema:
    """输入模式（JSON Schema）"""
    json: Dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})

    @classmethod
    def from_json(cls, json_val: Dict[str, Any]) -> "InputSchema":
        return cls(json=json_val)

    @classmethod
    def from_dict(cls, data: dict) -> "InputSchema":
        return cls(json=data.get("json", {"type": "object", "properties": {}}))

    def to_dict(self) -> dict:
        return {"json": self.json}


@dataclass
class ToolSpecification:
    """工具规范"""
    name: str = ""
    description: str = ""
    input_schema: InputSchema = field(default_factory=InputSchema)

    @classmethod
    def from_dict(cls, data: dict) -> "ToolSpecification":
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            input_schema=InputSchema.from_dict(data.get("inputSchema", {})),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema.to_dict(),
        }


@dataclass
class Tool:
    """工具定义"""
    tool_specification: ToolSpecification = field(default_factory=ToolSpecification)

    @classmethod
    def from_dict(cls, data: dict) -> "Tool":
        return cls(
            tool_specification=ToolSpecification.from_dict(data.get("toolSpecification", {})),
        )

    def to_dict(self) -> dict:
        return {"toolSpecification": self.tool_specification.to_dict()}


@dataclass
class ToolResult:
    """工具执行结果"""
    tool_use_id: str = ""
    content: List[Dict[str, Any]] = field(default_factory=list)
    status: Optional[str] = None
    is_error: bool = False

    @classmethod
    def success(cls, tool_use_id: str, content: str) -> "ToolResult":
        return cls(
            tool_use_id=tool_use_id,
            content=[{"text": content}],
            status="success",
            is_error=False,
        )

    @classmethod
    def error(cls, tool_use_id: str, error_message: str) -> "ToolResult":
        return cls(
            tool_use_id=tool_use_id,
            content=[{"text": error_message}],
            status="error",
            is_error=True,
        )

    @classmethod
    def from_dict(cls, data: dict) -> "ToolResult":
        return cls(
            tool_use_id=data.get("toolUseId", ""),
            content=data.get("content", []),
            status=data.get("status"),
            is_error=data.get("isError", False),
        )

    def to_dict(self) -> dict:
        d: Dict[str, Any] = {
            "toolUseId": self.tool_use_id,
            "content": self.content,
        }
        if self.status is not None:
            d["status"] = self.status
        if self.is_error:
            d["isError"] = self.is_error
        return d


@dataclass
class ToolUseEntry:
    """工具使用条目（历史消息中记录工具调用）"""
    tool_use_id: str = ""
    name: str = ""
    input: Any = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "ToolUseEntry":
        return cls(
            tool_use_id=data.get("toolUseId", ""),
            name=data.get("name", ""),
            input=data.get("input", {}),
        )

    def to_dict(self) -> dict:
        return {
            "toolUseId": self.tool_use_id,
            "name": self.name,
            "input": self.input,
        }
