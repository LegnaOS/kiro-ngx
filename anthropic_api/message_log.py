"""消息日志模块 - 记录完整的 API 请求和响应"""

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class MessageLogger:
    """消息日志记录器，线程安全"""

    def __init__(self, log_dir: Optional[Path] = None):
        self._enabled = False
        self._lock = threading.Lock()
        self._log_dir = log_dir
        self._log_file: Optional[Path] = None
        self._session_tag: Optional[str] = None
        self._init_log_file()

    def _init_log_file(self):
        if not self._log_dir:
            return
        self._log_dir.mkdir(parents=True, exist_ok=True)
        # 每次启动使用独立文件：日期_时间
        if not self._session_tag:
            self._session_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._log_file = self._log_dir / f"messages_{self._session_tag}.jsonl"

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool):
        with self._lock:
            self._enabled = enabled
            if enabled:
                self._init_log_file()
        logger.info("消息日志已%s", "开启" if enabled else "关闭")

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
            "system": system,
            "messages": messages,
        }
        if tools:
            entry["tools"] = tools
        entry.update(kwargs)
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
            "content": content,
            "stopReason": stop_reason,
            "usage": usage,
        }
        entry.update(kwargs)
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
            "text": text,
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
