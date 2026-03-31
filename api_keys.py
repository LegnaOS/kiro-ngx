"""多 API Key 管理 — 分组、额度、计费倍率、月度重置"""

import json
import logging
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _current_month() -> str:
    return datetime.now().strftime("%Y-%m")


def _gen_key() -> str:
    return "sk-" + secrets.token_hex(24)


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


class ApiKeyManager:
    def __init__(self, data_dir: Optional[Path] = None):
        self._lock = threading.Lock()
        self._path = data_dir / "api_keys.json" if data_dir else None
        self._groups: dict[str, dict] = {}  # {name: {rate, monthlyQuota}}
        self._keys: list[dict] = []
        self._key_index: dict[str, dict] = {}  # key_string -> key_dict
        self._extra_tracking: dict[str, dict] = {}  # 非托管 key（管理员）的用量追踪
        self._dirty = False
        self._load()

    # ---- 查询 ----

    def lookup(self, key_str: str) -> Optional[dict]:
        """根据 key 字符串查找，自动月度重置"""
        with self._lock:
            entry = self._key_index.get(key_str)
            if not entry:
                return None
            self._maybe_reset_month(entry)
            return dict(entry)

    def check_quota(self, key_str: str) -> tuple[bool, str]:
        """检查额度，返回 (allowed, reason)"""
        with self._lock:
            entry = self._key_index.get(key_str)
            if not entry:
                return False, "key_not_found"
            if not entry.get("enabled", True):
                return False, "key_disabled"
            self._maybe_reset_month(entry)
            quota = self._effective_quota(entry)
            if quota < 0:  # -1 = unlimited
                return True, ""
            if entry.get("billedTokens", 0) >= quota:
                return False, "quota_exceeded"
            return True, ""

    def report_usage(self, key_str: str, input_tokens: int, output_tokens: int, model: str = ""):
        """上报 token 用量，按倍率计入额度，同时追踪模型维度"""
        with self._lock:
            entry = self._key_index.get(key_str)
            if not entry:
                # 非托管 key（管理员），自动创建追踪条目
                entry = self._extra_tracking.get(key_str)
                if not entry:
                    entry = {
                        "name": "管理员",
                        "group": "admin",
                        "billedTokens": 0,
                        "totalRawTokens": 0,
                        "requestCount": 0,
                    }
                    self._extra_tracking[key_str] = entry
            self._maybe_reset_month(entry)
            rate = self._effective_rate(entry)
            raw = input_tokens + output_tokens
            billed = raw * rate
            entry["billedTokens"] = entry.get("billedTokens", 0) + billed
            entry["totalRawTokens"] = entry.get("totalRawTokens", 0) + raw
            entry["requestCount"] = entry.get("requestCount", 0) + 1
            # 按模型追踪
            if model:
                mc = entry.setdefault("modelCounts", {})
                mc[model] = mc.get(model, 0) + 1
                mt = entry.setdefault("modelTokens", {})
                m = mt.setdefault(model, {"input": 0, "output": 0})
                m["input"] += input_tokens
                m["output"] += output_tokens
            # 按天追踪（保留最近 31 天）
            today = datetime.now().strftime("%Y-%m-%d")
            du = entry.setdefault("dailyUsage", {})
            day_e = du.setdefault(today, {"input": 0, "output": 0})
            day_e["input"] += input_tokens
            day_e["output"] += output_tokens
            # 按小时追踪（仅当天）
            hour = datetime.now().strftime("%H")
            if entry.get("hourlyDate") != today:
                entry["hourlyUsage"] = {}
                entry["hourlyDate"] = today
            hu = entry.setdefault("hourlyUsage", {})
            hour_e = hu.setdefault(hour, {"input": 0, "output": 0})
            hour_e["input"] += input_tokens
            hour_e["output"] += output_tokens
            self._dirty = True
        self._save_debounced()

    def get_all_keys(self) -> list[dict]:
        with self._lock:
            result = []
            for e in self._keys:
                self._maybe_reset_month(e)
                d = dict(e)
                d["effectiveRate"] = self._effective_rate(e)
                d["effectiveQuota"] = self._effective_quota(e)
                # 隐藏完整 key，只显示前8后4
                k = d.get("key", "")
                d["maskedKey"] = k[:7] + "..." + k[-4:] if len(k) > 12 else k
                result.append(d)
            return result

    def get_groups(self) -> dict[str, dict]:
        with self._lock:
            return {k: dict(v) for k, v in self._groups.items()}

    def get_usage_stats(self) -> list[dict]:
        """返回每个 key 的用量统计（不含 key 字符串，供首页展示）"""
        with self._lock:
            result = []
            all_entries = list(self._keys) + list(self._extra_tracking.values())
            for e in all_entries:
                self._maybe_reset_month(e)
                today = datetime.now().strftime("%Y-%m-%d")
                if e.get("hourlyDate") != today:
                    e["hourlyUsage"] = {}
                    e["hourlyDate"] = today
                result.append({
                    "name": e.get("name", ""),
                    "group": e.get("group", ""),
                    "modelCounts": dict(e.get("modelCounts", {})),
                    "modelTokens": {m: dict(v) for m, v in e.get("modelTokens", {}).items()},
                    "dailyUsage": dict(e.get("dailyUsage", {})),
                    "hourlyUsage": dict(e.get("hourlyUsage", {})),
                    "billedTokens": e.get("billedTokens", 0),
                    "totalRawTokens": e.get("totalRawTokens", 0),
                    "requestCount": e.get("requestCount", 0),
                })
            return result

    # ---- 分组管理 ----

    def set_group(self, name: str, rate: float, monthly_quota: int):
        with self._lock:
            self._groups[name] = {"rate": rate, "monthlyQuota": monthly_quota}
            self._dirty = True
        self._save()

    def delete_group(self, name: str) -> bool:
        with self._lock:
            if name not in self._groups:
                return False
            # 检查是否有 key 在用
            for e in self._keys:
                if e.get("group") == name:
                    return False
            del self._groups[name]
            self._dirty = True
        self._save()
        return True

    # ---- Key 管理 ----

    def add_key(self, name: str, group: str, rate: Optional[float] = None,
                monthly_quota: Optional[int] = None) -> dict:
        key_str = _gen_key()
        entry = {
            "key": key_str,
            "name": name,
            "group": group,
            "rate": rate,
            "monthlyQuota": monthly_quota,
            "billedTokens": 0,
            "billedMonth": _current_month(),
            "totalRawTokens": 0,
            "requestCount": 0,
            "enabled": True,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._keys.append(entry)
            self._key_index[key_str] = entry
            self._dirty = True
        self._save()
        return dict(entry)

    def update_key(self, key_str: str, **fields) -> Optional[dict]:
        allowed = {"name", "group", "rate", "monthlyQuota", "enabled"}
        with self._lock:
            entry = self._key_index.get(key_str)
            if not entry:
                return None
            for k, v in fields.items():
                if k in allowed:
                    entry[k] = v
            self._dirty = True
        self._save()
        return dict(entry)

    def regenerate_key(self, old_key: str) -> Optional[dict]:
        new_key = _gen_key()
        with self._lock:
            entry = self._key_index.get(old_key)
            if not entry:
                return None
            del self._key_index[old_key]
            entry["key"] = new_key
            self._key_index[new_key] = entry
            self._dirty = True
        self._save()
        return dict(entry)

    def delete_key(self, key_str: str) -> bool:
        with self._lock:
            entry = self._key_index.pop(key_str, None)
            if not entry:
                return False
            self._keys.remove(entry)
            self._dirty = True
        self._save()
        return True

    def reset_usage(self, key_str: str) -> bool:
        with self._lock:
            entry = self._key_index.get(key_str)
            if not entry:
                return False
            entry["billedTokens"] = 0
            entry["billedMonth"] = _current_month()
            self._dirty = True
        self._save()
        return True

    # ---- 内部 ----

    def _effective_rate(self, entry: dict) -> float:
        r = entry.get("rate")
        if r is not None:
            return r
        g = self._groups.get(entry.get("group", ""))
        return g["rate"] if g else 1.0

    def _effective_quota(self, entry: dict) -> int:
        q = entry.get("monthlyQuota")
        if q is not None:
            return q
        g = self._groups.get(entry.get("group", ""))
        return g["monthlyQuota"] if g else -1

    def _maybe_reset_month(self, entry: dict):
        month = _current_month()
        if entry.get("billedMonth") != month:
            entry["billedTokens"] = 0
            entry["billedMonth"] = month
            self._dirty = True

    # ---- 持久化 ----

    def _load(self):
        if not self._path or not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("加载 api_keys.json 失败: %s", e)
            return
        self._groups = data.get("groups", {})
        self._keys = data.get("keys", [])
        self._key_index = {e["key"]: e for e in self._keys if "key" in e}
        self._extra_tracking = data.get("extraTracking", {})
        # 一次性迁移 UTC 小时键到本地时间
        for e in list(self._keys) + list(self._extra_tracking.values()):
            if e.get("hourlyUsage") and not e.get("hourlyTzLocal"):
                e["hourlyUsage"] = _migrate_hourly_utc_to_local(e["hourlyUsage"])
                e["hourlyTzLocal"] = True
                self._dirty = True
        logger.info("已加载 %d 个 API key, %d 个分组", len(self._keys), len(self._groups))

    def _save(self):
        if not self._path:
            return
        with self._lock:
            # 清理超过 31 天的 dailyUsage
            cutoff = (datetime.now() - timedelta(days=31)).strftime("%Y-%m-%d")
            for e in list(self._keys) + list(self._extra_tracking.values()):
                du = e.get("dailyUsage")
                if du:
                    old_keys = [k for k in du if k < cutoff]
                    for k in old_keys:
                        del du[k]
            data = {"groups": self._groups, "keys": self._keys, "extraTracking": self._extra_tracking}
            self._dirty = False
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning("保存 api_keys.json 失败: %s", e)

    _last_save: float = 0

    def _save_debounced(self):
        now = time.monotonic()
        if now - self._last_save >= 3:
            self._save()
            self._last_save = now

    def flush(self):
        if self._dirty:
            self._save()


# ---- 模块级单例 ----

_instance: Optional[ApiKeyManager] = None


def init_api_key_manager(data_dir: Optional[Path] = None) -> ApiKeyManager:
    global _instance
    _instance = ApiKeyManager(data_dir)
    return _instance


def get_api_key_manager() -> Optional[ApiKeyManager]:
    return _instance
