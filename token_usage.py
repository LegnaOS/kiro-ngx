"""Token 用量追踪器 — 按日统计 input/output tokens"""

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

STATS_SAVE_DEBOUNCE = 30.0


class TokenUsageTracker:
    """内存追踪 + 防抖落盘，支持今日/昨日/每模型维度"""

    def __init__(self, cache_dir: Optional[Path] = None):
        self._lock = threading.Lock()
        self._cache_path = cache_dir / "kiro_token_usage.json" if cache_dir else None

        self._today: str = _today_str()
        self._today_input: int = 0
        self._today_output: int = 0
        self._yesterday_input: int = 0
        self._yesterday_output: int = 0
        self._model_today: dict[str, dict[str, int]] = {}   # {model: {"input": n, "output": n}}
        self._model_yesterday: dict[str, dict[str, int]] = {}

        self._last_save_at: Optional[float] = None
        self._dirty = False

        self._load()

    # ---- 公开 API ----

    def report(self, model: str, input_tokens: int, output_tokens: int):
        """累加一次请求的 token 用量"""
        with self._lock:
            self._maybe_rotate()
            self._today_input += input_tokens
            self._today_output += output_tokens
            m = self._model_today.setdefault(model, {"input": 0, "output": 0})
            m["input"] += input_tokens
            m["output"] += output_tokens
            self._dirty = True
        self._save_debounced()

    def get_stats(self) -> dict:
        """返回今日/昨日/每模型汇总"""
        with self._lock:
            self._maybe_rotate()
            models: dict[str, dict] = {}
            all_keys = set(self._model_today) | set(self._model_yesterday)
            for m in all_keys:
                models[m] = {
                    "today": dict(self._model_today.get(m, {"input": 0, "output": 0})),
                    "yesterday": dict(self._model_yesterday.get(m, {"input": 0, "output": 0})),
                }
            return {
                "today": {"input": self._today_input, "output": self._today_output},
                "yesterday": {"input": self._yesterday_input, "output": self._yesterday_output},
                "models": models,
            }

    # ---- 日期轮转 ----

    def _maybe_rotate(self):
        """调用方须持有 _lock"""
        now_str = _today_str()
        if now_str == self._today:
            return
        # 日期变了，把今日数据挪到昨日
        self._yesterday_input = self._today_input
        self._yesterday_output = self._today_output
        self._model_yesterday = self._model_today
        self._today_input = 0
        self._today_output = 0
        self._model_today = {}
        self._today = now_str
        self._dirty = True

    # ---- 持久化 ----

    def _load(self):
        if not self._cache_path or not self._cache_path.exists():
            return
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("解析 token 用量缓存失败: %s", e)
            return

        saved_date = data.get("date", "")
        today = _today_str()

        if saved_date == today:
            self._today_input = data.get("todayInput", 0)
            self._today_output = data.get("todayOutput", 0)
            self._yesterday_input = data.get("yesterdayInput", 0)
            self._yesterday_output = data.get("yesterdayOutput", 0)
            self._model_today = data.get("modelToday", {})
            self._model_yesterday = data.get("modelYesterday", {})
        else:
            # 存档日期不是今天 → 存档数据变成昨日
            self._yesterday_input = data.get("todayInput", 0)
            self._yesterday_output = data.get("todayOutput", 0)
            self._model_yesterday = data.get("modelToday", {})
            self._today = today

        logger.info("已加载 token 用量缓存 (date=%s)", saved_date)

    def _save(self):
        if not self._cache_path:
            return
        with self._lock:
            data = {
                "date": self._today,
                "todayInput": self._today_input,
                "todayOutput": self._today_output,
                "yesterdayInput": self._yesterday_input,
                "yesterdayOutput": self._yesterday_output,
                "modelToday": self._model_today,
                "modelYesterday": self._model_yesterday,
            }
            self._dirty = False
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            with self._lock:
                self._last_save_at = time.monotonic()
        except Exception as e:
            logger.warning("保存 token 用量缓存失败: %s", e)

    def _save_debounced(self):
        with self._lock:
            should_flush = (
                self._last_save_at is None
                or (time.monotonic() - self._last_save_at) >= STATS_SAVE_DEBOUNCE
            )
        if should_flush:
            self._save()

    def flush(self):
        """强制落盘（进程退出前调用）"""
        if self._dirty:
            self._save()


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---- 模块级单例 ----

_instance: Optional[TokenUsageTracker] = None


def init_token_usage_tracker(cache_dir: Optional[Path] = None) -> TokenUsageTracker:
    global _instance
    _instance = TokenUsageTracker(cache_dir)
    return _instance


def get_token_usage_tracker() -> Optional[TokenUsageTracker]:
    return _instance
