"""运行时日志缓冲区。

提供一个内存环形缓冲区，供 Admin UI 按尾部/增量读取最近日志，
避免高并发时一次性加载全量日志。
"""

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import threading
from typing import Optional


DEFAULT_RUNTIME_LOG_LINES = 5000
DEFAULT_RUNTIME_LOG_LIMIT = 100
MAX_RUNTIME_LOG_LIMIT = 200


@dataclass
class RuntimeLogEntry:
    seq: int
    timestamp: str
    level: str
    logger: str
    message: str

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "timestamp": self.timestamp,
            "level": self.level,
            "logger": self.logger,
            "message": self.message,
        }


class RuntimeLogBuffer(logging.Handler):
    """线程安全的运行时日志环形缓冲区。"""

    def __init__(self, max_lines: int = DEFAULT_RUNTIME_LOG_LINES):
        super().__init__()
        self._max_lines = max(max_lines, 1)
        self._entries: deque[RuntimeLogEntry] = deque(maxlen=self._max_lines)
        self._lock = threading.Lock()
        self._next_seq = 1

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            message = record.getMessage()

        entry = RuntimeLogEntry(
            seq=0,
            timestamp=datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            level=record.levelname,
            logger=record.name,
            message=message,
        )
        with self._lock:
            entry.seq = self._next_seq
            self._next_seq += 1
            self._entries.append(entry)

    def _snapshot(self) -> tuple[list[RuntimeLogEntry], int]:
        with self._lock:
            return list(self._entries), self._next_seq - 1

    @staticmethod
    def _apply_filters(
        entries: list[RuntimeLogEntry],
        level: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> list[RuntimeLogEntry]:
        if level:
            level_upper = level.upper()
            entries = [e for e in entries if e.level == level_upper]
        if keyword:
            needle = keyword.lower()
            entries = [
                e for e in entries
                if needle in e.message.lower() or needle in e.logger.lower()
            ]
        return entries

    def tail(
        self,
        limit: int = DEFAULT_RUNTIME_LOG_LIMIT,
        level: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> dict:
        entries, next_cursor = self._snapshot()
        filtered = self._apply_filters(entries, level=level, keyword=keyword)
        limited = filtered[-max(1, min(limit, MAX_RUNTIME_LOG_LIMIT)):]
        return {
            "entries": [e.to_dict() for e in limited],
            "nextCursor": next_cursor,
            "bufferSize": len(entries),
        }

    def since(
        self,
        cursor: int,
        limit: int = DEFAULT_RUNTIME_LOG_LIMIT,
        level: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> dict:
        entries, next_cursor = self._snapshot()
        fresh = [e for e in entries if e.seq > cursor]
        filtered = self._apply_filters(fresh, level=level, keyword=keyword)
        limited = filtered[:max(1, min(limit, MAX_RUNTIME_LOG_LIMIT))]
        return {
            "entries": [e.to_dict() for e in limited],
            "nextCursor": next_cursor,
            "bufferSize": len(entries),
        }


_runtime_log_buffer: Optional[RuntimeLogBuffer] = None


def init_runtime_log_buffer(max_lines: int = DEFAULT_RUNTIME_LOG_LINES) -> RuntimeLogBuffer:
    global _runtime_log_buffer
    if _runtime_log_buffer is not None:
        return _runtime_log_buffer

    handler = RuntimeLogBuffer(max_lines=max_lines)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s"))
    logging.getLogger().addHandler(handler)
    _runtime_log_buffer = handler
    return handler


def get_runtime_log_buffer() -> Optional[RuntimeLogBuffer]:
    return _runtime_log_buffer
