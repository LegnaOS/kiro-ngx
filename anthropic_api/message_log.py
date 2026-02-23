"""消息日志模块 - 记录完整的 API 请求和响应

长文本（>=200字符）或大块数据（序列化>1000字符）存入独立的 texts 文件，主日志中用引用替代。
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

TEXT_THRESHOLD = 200  # 单个字符串外置阈值
BULK_THRESHOLD = 1000  # 整体序列化后超此长度则整块外置


class MessageLogger:
    """消息日志记录器，线程安全"""

    def __init__(self, log_dir: Optional[Path] = None):
        self._enabled = False
        self._lock = threading.Lock()
        self._log_dir = log_dir
        self._log_file: Optional[Path] = None
        self._text_file: Optional[Path] = None
        self._text_line: int = 0  # texts 文件当前行号
        self._session_tag: Optional[str] = None
        self._init_log_file()

    def _init_log_file(self):
        if not self._log_dir:
            return
        self._log_dir.mkdir(parents=True, exist_ok=True)
        if not self._session_tag:
            self._session_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._log_file = self._log_dir / f"messages_{self._session_tag}.jsonl"
        self._text_file = self._log_dir / f"texts_{self._session_tag}.jsonl"
        self._text_line = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool):
        with self._lock:
            self._enabled = enabled
            if enabled:
                self._init_log_file()
        logger.info("消息日志已%s", "开启" if enabled else "关闭")

    def _store_text(self, text: str) -> str:
        """长文本写入 texts 文件，返回引用标记"""
        if not self._text_file:
            return text
        self._text_line += 1
        line_no = self._text_line
        try:
            row = json.dumps({"line": line_no, "text": text}, ensure_ascii=False, default=str) + "\n"
            with open(self._text_file, "a", encoding="utf-8") as f:
                f.write(row)
        except Exception as e:
            logger.warning("写入 texts 日志失败: %s", e)
        return f"...[texts:{line_no}]..."

    def _compact_value(self, value: Any) -> Any:
        """递归压缩：字符串超阈值则外置"""
        if isinstance(value, str):
            return self._store_text(value) if len(value) >= TEXT_THRESHOLD else value
        if isinstance(value, list):
            return [self._compact_value(v) for v in value]
        if isinstance(value, dict):
            return {k: self._compact_value(v) for k, v in value.items()}
        return value

    def _compact_bulk(self, value: Any) -> Any:
        """整块压缩：先递归压缩内部，若结果序列化仍超阈值则整块外置"""
        compacted = self._compact_value(value)
        try:
            serialized = json.dumps(compacted, ensure_ascii=False, default=str)
        except Exception:
            return compacted
        if len(serialized) > BULK_THRESHOLD:
            return self._store_text(serialized)
        return compacted

    def log_request(self, model: str, messages: list, system: Optional[list] = None,
                    tools: Optional[list] = None, stream: bool = False, **kwargs):
        """记录请求"""
        if not self._enabled:
            return
        entry = {
            "type": "request",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "stream": stream,
            "msg_count": len(messages) if messages else 0,
            "system": self._compact_bulk(system),
            "messages": self._compact_bulk(messages),
        }
        if tools:
            entry["tool_count"] = len(tools)
            entry["tools"] = self._compact_bulk(tools)
        for k, v in kwargs.items():
            entry[k] = self._compact_value(v)
        self._write(entry)

    def log_response(self, model: str, content: list, stop_reason: Optional[str] = None,
                     usage: Optional[dict] = None, **kwargs):
        """记录非流式响应"""
        if not self._enabled:
            return
        entry = {
            "type": "response",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "content": self._compact_bulk(content),
            "stopReason": stop_reason,
            "usage": usage,
        }
        for k, v in kwargs.items():
            entry[k] = self._compact_value(v)
        self._write(entry)

    def log_stream_text(self, model: str, text: str, stop_reason: Optional[str] = None,
                        usage: Optional[dict] = None):
        """记录流式响应的最终文本"""
        if not self._enabled:
            return
        entry = {
            "type": "stream_response",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "text": self._compact_value(text),
            "stopReason": stop_reason,
            "usage": usage,
        }
        self._write(entry)

    def _write(self, entry: dict):
        if not self._log_file:
            return
        try:
            line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
            with self._lock:
                with open(self._log_file, "a", encoding="utf-8") as f:
                    f.write(line)
        except Exception as e:
            logger.warning("写入消息日志失败: %s", e)


# 全局实例
_instance: Optional[MessageLogger] = None


def get_message_logger() -> Optional[MessageLogger]:
    return _instance


def init_message_logger(log_dir: Optional[Path] = None) -> MessageLogger:
    global _instance
    _instance = MessageLogger(log_dir)
    return _instance
