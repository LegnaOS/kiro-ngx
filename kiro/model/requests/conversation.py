"""对话类型定义 - 参考 src/kiro/model/requests/conversation.rs"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .tool import Tool, ToolResult, ToolUseEntry


@dataclass
class KiroImageSource:
    """图片数据源"""
    bytes: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "KiroImageSource":
        return cls(bytes=data.get("bytes", ""))

    def to_dict(self) -> dict:
        return {"bytes": self.bytes}


@dataclass
class KiroImage:
    """Kiro 图片"""
    format: str = ""
    source: KiroImageSource = field(default_factory=KiroImageSource)

    @classmethod
    def from_base64(cls, fmt: str, data: str) -> "KiroImage":
        return cls(format=fmt, source=KiroImageSource(bytes=data))

    @classmethod
    def from_dict(cls, data: dict) -> "KiroImage":
        return cls(
            format=data.get("format", ""),
            source=KiroImageSource.from_dict(data.get("source", {})),
        )

    def to_dict(self) -> dict:
        return {"format": self.format, "source": self.source.to_dict()}


@dataclass
class UserInputMessageContext:
    """用户输入消息上下文"""
    tool_results: List[ToolResult] = field(default_factory=list)
    tools: List[Tool] = field(default_factory=list)

    def _is_default(self) -> bool:
        return not self.tools and not self.tool_results

    @classmethod
    def from_dict(cls, data: dict) -> "UserInputMessageContext":
        return cls(
            tool_results=[ToolResult.from_dict(r) for r in data.get("toolResults", [])],
            tools=[Tool.from_dict(t) for t in data.get("tools", [])],
        )

    def to_dict(self) -> dict:
        d: Dict[str, Any] = {}
        if self.tool_results:
            d["toolResults"] = [r.to_dict() for r in self.tool_results]
        if self.tools:
            d["tools"] = [t.to_dict() for t in self.tools]
        return d


@dataclass
class UserInputMessage:
    """用户输入消息"""
    user_input_message_context: UserInputMessageContext = field(default_factory=UserInputMessageContext)
    content: str = ""
    model_id: str = ""
    images: List[KiroImage] = field(default_factory=list)
    origin: Optional[str] = None

    @classmethod
    def new(cls, content: str, model_id: str) -> "UserInputMessage":
        return cls(
            content=content,
            model_id=model_id,
            origin="AI_EDITOR",
        )

    @classmethod
    def from_dict(cls, data: dict) -> "UserInputMessage":
        return cls(
            user_input_message_context=UserInputMessageContext.from_dict(
                data.get("userInputMessageContext", {})
            ),
            content=data.get("content", ""),
            model_id=data.get("modelId", ""),
            images=[KiroImage.from_dict(i) for i in data.get("images", [])],
            origin=data.get("origin"),
        )

    def to_dict(self) -> dict:
        d: Dict[str, Any] = {
            "userInputMessageContext": self.user_input_message_context.to_dict(),
            "content": self.content,
            "modelId": self.model_id,
        }
        if self.images:
            d["images"] = [i.to_dict() for i in self.images]
        if self.origin is not None:
            d["origin"] = self.origin
        return d


@dataclass
class CurrentMessage:
    """当前消息容器"""
    user_input_message: UserInputMessage = field(default_factory=UserInputMessage)

    @classmethod
    def from_dict(cls, data: dict) -> "CurrentMessage":
        return cls(
            user_input_message=UserInputMessage.from_dict(data.get("userInputMessage", {})),
        )

    def to_dict(self) -> dict:
        return {"userInputMessage": self.user_input_message.to_dict()}


@dataclass
class UserMessage:
    """用户消息（历史记录中使用）"""
    content: str = ""
    model_id: str = ""
    origin: Optional[str] = None
    images: List[KiroImage] = field(default_factory=list)
    user_input_message_context: UserInputMessageContext = field(default_factory=UserInputMessageContext)

    @classmethod
    def new(cls, content: str, model_id: str) -> "UserMessage":
        return cls(content=content, model_id=model_id, origin="AI_EDITOR")

    @classmethod
    def from_dict(cls, data: dict) -> "UserMessage":
        return cls(
            content=data.get("content", ""),
            model_id=data.get("modelId", ""),
            origin=data.get("origin"),
            images=[KiroImage.from_dict(i) for i in data.get("images", [])],
            user_input_message_context=UserInputMessageContext.from_dict(
                data.get("userInputMessageContext", {})
            ),
        )

    def to_dict(self) -> dict:
        d: Dict[str, Any] = {
            "content": self.content,
            "modelId": self.model_id,
        }
        if self.origin is not None:
            d["origin"] = self.origin
        if self.images:
            d["images"] = [i.to_dict() for i in self.images]
        if not self.user_input_message_context._is_default():
            d["userInputMessageContext"] = self.user_input_message_context.to_dict()
        return d


@dataclass
class AssistantMessage:
    """助手消息（历史记录中使用）"""
    content: str = ""
    tool_uses: Optional[List[ToolUseEntry]] = None

    @classmethod
    def new(cls, content: str) -> "AssistantMessage":
        return cls(content=content)

    @classmethod
    def from_dict(cls, data: dict) -> "AssistantMessage":
        tool_uses = None
        if "toolUses" in data:
            tool_uses = [ToolUseEntry.from_dict(t) for t in data["toolUses"]]
        return cls(content=data.get("content", ""), tool_uses=tool_uses)

    def to_dict(self) -> dict:
        d: Dict[str, Any] = {"content": self.content}
        if self.tool_uses is not None:
            d["toolUses"] = [t.to_dict() for t in self.tool_uses]
        return d


@dataclass
class HistoryUserMessage:
    """历史用户消息"""
    user_input_message: UserMessage = field(default_factory=UserMessage)

    @classmethod
    def new(cls, content: str, model_id: str) -> "HistoryUserMessage":
        return cls(user_input_message=UserMessage.new(content, model_id))

    @classmethod
    def from_dict(cls, data: dict) -> "HistoryUserMessage":
        return cls(user_input_message=UserMessage.from_dict(data.get("userInputMessage", {})))

    def to_dict(self) -> dict:
        return {"userInputMessage": self.user_input_message.to_dict()}


@dataclass
class HistoryAssistantMessage:
    """历史助手消息"""
    assistant_response_message: AssistantMessage = field(default_factory=AssistantMessage)

    @classmethod
    def new(cls, content: str) -> "HistoryAssistantMessage":
        return cls(assistant_response_message=AssistantMessage.new(content))

    @classmethod
    def from_dict(cls, data: dict) -> "HistoryAssistantMessage":
        return cls(
            assistant_response_message=AssistantMessage.from_dict(
                data.get("assistantResponseMessage", {})
            )
        )

    def to_dict(self) -> dict:
        return {"assistantResponseMessage": self.assistant_response_message.to_dict()}


class Message:
    """历史消息（用户或助手），使用 untagged 风格序列化"""

    def __init__(self, data: Any):
        self._data = data

    @staticmethod
    def user(content: str, model_id: str) -> "Message":
        return Message(HistoryUserMessage.new(content, model_id))

    @staticmethod
    def assistant(content: str) -> "Message":
        return Message(HistoryAssistantMessage.new(content))

    def is_user(self) -> bool:
        return isinstance(self._data, HistoryUserMessage)

    def is_assistant(self) -> bool:
        return isinstance(self._data, HistoryAssistantMessage)

    @staticmethod
    def from_dict(data: dict) -> "Message":
        if "userInputMessage" in data:
            return Message(HistoryUserMessage.from_dict(data))
        elif "assistantResponseMessage" in data:
            return Message(HistoryAssistantMessage.from_dict(data))
        raise ValueError(f"Unknown message format: {list(data.keys())}")

    def to_dict(self) -> dict:
        return self._data.to_dict()


@dataclass
class ConversationState:
    """对话状态"""
    conversation_id: str = ""
    current_message: CurrentMessage = field(default_factory=CurrentMessage)
    history: List[Message] = field(default_factory=list)
    agent_continuation_id: Optional[str] = None
    agent_task_type: Optional[str] = None
    chat_trigger_type: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "ConversationState":
        return cls(
            conversation_id=data.get("conversationId", ""),
            current_message=CurrentMessage.from_dict(data.get("currentMessage", {})),
            history=[Message.from_dict(m) for m in data.get("history", [])],
            agent_continuation_id=data.get("agentContinuationId"),
            agent_task_type=data.get("agentTaskType"),
            chat_trigger_type=data.get("chatTriggerType"),
        )

    def to_dict(self) -> dict:
        d: Dict[str, Any] = {
            "currentMessage": self.current_message.to_dict(),
            "conversationId": self.conversation_id,
        }
        if self.agent_continuation_id is not None:
            d["agentContinuationId"] = self.agent_continuation_id
        if self.agent_task_type is not None:
            d["agentTaskType"] = self.agent_task_type
        if self.chat_trigger_type is not None:
            d["chatTriggerType"] = self.chat_trigger_type
        if self.history:
            d["history"] = [m.to_dict() for m in self.history]
        return d
