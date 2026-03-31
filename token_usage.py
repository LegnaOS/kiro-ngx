"""Token 用量追踪器 — 按日统计 input/output tokens，支持最多 31 天历史"""

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

STATS_SAVE_DEBOUNCE = 30.0
MAX_HISTORY_DAYS = 31


class TokenUsageTracker:
    """内存追踪 + 防抖落盘，支持今日/昨日/每模型维度 + 历史日聚合"""

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
        # 小时级追踪: {"00": {"input": n, "output": n}, ...}
        self._hourly_today: dict[str, dict[str, int]] = {}
        # 历史日聚合: {"2026-03-18": {"input": n, "output": n, "models": {model: {"input": n, "output": n}}}}
        self._daily_history: dict[str, dict] = {}

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
            # 小时级
            hour = datetime.now().strftime("%H")
            h = self._hourly_today.setdefault(hour, {"input": 0, "output": 0})
            h["input"] += input_tokens
            h["output"] += output_tokens
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

    def get_history(self, days: int = 7) -> dict:
        """返回最近 N 天的日聚合数据（含今日）"""
        days = max(1, min(days, MAX_HISTORY_DAYS))
        with self._lock:
            self._maybe_rotate()
            result: dict[str, dict] = {}
            # 今日
            result[self._today] = {
                "input": self._today_input,
                "output": self._today_output,
                "models": {k: dict(v) for k, v in self._model_today.items()},
            }
            # 历史
            today_dt = datetime.strptime(self._today, "%Y-%m-%d")
            for i in range(1, days):
                date_str = (today_dt - timedelta(days=i)).strftime("%Y-%m-%d")
                if date_str in self._daily_history:
                    entry = self._daily_history[date_str]
                    result[date_str] = {
                        "input": entry.get("input", 0),
                        "output": entry.get("output", 0),
                        "models": entry.get("models", {}),
                    }
                else:
                    # 昨日数据可能还没进 history（当天首次轮转前）
                    if i == 1:
                        result[date_str] = {
                            "input": self._yesterday_input,
                            "output": self._yesterday_output,
                            "models": {k: dict(v) for k, v in self._model_yesterday.items()},
                        }
            return result

    def get_hourly(self) -> dict:
        """返回今日 24 小时的用量分布"""
        with self._lock:
            self._maybe_rotate()
            result: dict[str, dict[str, int]] = {}
            for h in range(24):
                key = f"{h:02d}"
                entry = self._hourly_today.get(key)
                if entry:
                    result[key] = dict(entry)
                else:
                    result[key] = {"input": 0, "output": 0}
            return result

    # ---- 日期轮转 ----

    def _maybe_rotate(self):
        """调用方须持有 _lock"""
        now_str = _today_str()
        if now_str == self._today:
            return
        # 把今日数据归档到 daily_history
        self._daily_history[self._today] = {
            "input": self._today_input,
            "output": self._today_output,
            "models": {k: dict(v) for k, v in self._model_today.items()},
        }
        # 清理超过 MAX_HISTORY_DAYS 的旧数据
        self._trim_history()
        # 日期变了，把今日数据挪到昨日
        self._yesterday_input = self._today_input
        self._yesterday_output = self._today_output
        self._model_yesterday = self._model_today
        self._today_input = 0
        self._today_output = 0
        self._model_today = {}
        self._hourly_today = {}
        self._today = now_str
        self._dirty = True

    def _trim_history(self):
        if len(self._daily_history) <= MAX_HISTORY_DAYS:
            return
        sorted_dates = sorted(self._daily_history.keys())
        excess = len(sorted_dates) - MAX_HISTORY_DAYS
        for date_str in sorted_dates[:excess]:
            del self._daily_history[date_str]

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
        self._daily_history = data.get("history", {})

        if saved_date == today:
            self._today_input = data.get("todayInput", 0)
            self._today_output = data.get("todayOutput", 0)
            self._yesterday_input = data.get("yesterdayInput", 0)
            self._yesterday_output = data.get("yesterdayOutput", 0)
            self._model_today = data.get("modelToday", {})
            self._model_yesterday = data.get("modelYesterday", {})
            self._hourly_today = data.get("hourlyToday", {})
            if not data.get("hourlyTzLocal"):
                self._hourly_today = _migrate_hourly_utc_to_local(self._hourly_today)
                self._dirty = True
        else:
            # 存档日期不是今天 → 存档数据归档到 history 并变成昨日
            self._daily_history[saved_date] = {
                "input": data.get("todayInput", 0),
                "output": data.get("todayOutput", 0),
                "models": data.get("modelToday", {}),
            }
            self._yesterday_input = data.get("todayInput", 0)
            self._yesterday_output = data.get("todayOutput", 0)
            self._model_yesterday = data.get("modelToday", {})
            self._today = today
            self._trim_history()

        logger.info("已加载 token 用量缓存 (date=%s, history_days=%d)", saved_date, len(self._daily_history))

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
                "hourlyToday": self._hourly_today,
                "hourlyTzLocal": True,
                "history": self._daily_history,
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
    return datetime.now().strftime("%Y-%m-%d")


def _migrate_hourly_utc_to_local(hourly: dict) -> dict:
    """一次性将 UTC 小时键偏移到本地时间"""
    offset = datetime.now().astimezone().utcoffset()
    if not offset:
        return hourly
    offset_hours = int(offset.total_seconds() // 3600)
    if offset_hours == 0:
        return hourly
    migrated: dict[str, dict] = {}
    for h, v in hourly.items():
        new_h = f"{(int(h) + offset_hours) % 24:02d}"
        if new_h in migrated:
            migrated[new_h]["input"] += v.get("input", 0)
            migrated[new_h]["output"] += v.get("output", 0)
        else:
            migrated[new_h] = dict(v)
    return migrated


# ---- 模块级单例 ----

_instance: Optional[TokenUsageTracker] = None


def init_token_usage_tracker(cache_dir: Optional[Path] = None) -> TokenUsageTracker:
    global _instance
    _instance = TokenUsageTracker(cache_dir)
    return _instance


def get_token_usage_tracker() -> Optional[TokenUsageTracker]:
    return _instance
