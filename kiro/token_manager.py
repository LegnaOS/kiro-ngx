"""Token 管理模块 - 参考 src/kiro/token_manager.rs

负责 Token 过期检测和刷新，支持 Social 和 IdC 认证方式
支持单凭据 (TokenManager) 和多凭据 (MultiTokenManager) 管理
"""

import asyncio
import hashlib
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from http_client import ProxyConfig, build_client, build_sync_client
from kiro.machine_id import generate_from_credentials
from kiro.model.credentials import KiroCredentials
from kiro.model.token_refresh import (
    IdcRefreshRequest,
    IdcRefreshResponse,
    RefreshRequest,
    RefreshResponse,
)
from kiro.model.usage_limits import UsageLimitsResponse
from config import Config

logger = logging.getLogger(__name__)

# 每个凭据最大 API 调用失败次数
MAX_FAILURES_PER_CREDENTIAL = 3
TRANSIENT_FAILURE_COOLDOWN_SECS = 15
# 统计数据持久化防抖间隔（秒）
STATS_SAVE_DEBOUNCE = 30.0
# IdC Token 刷新所需的 x-amz-user-agent header
IDC_AMZ_USER_AGENT = "aws-sdk-js/3.738.0 ua/2.1 os/other lang/js md/browser#unknown_unknown api/sso-oidc#3.738.0 m/E KiroIDE"
# getUsageLimits API 所需的 x-amz-user-agent 前缀
USAGE_LIMITS_AMZ_USER_AGENT_PREFIX = "aws-sdk-js/1.0.0"


def _sha256_hex(input_str: str) -> str:
    return hashlib.sha256(input_str.encode()).hexdigest()


def _parse_rfc3339(s: str) -> Optional[datetime]:
    """解析 RFC3339 时间字符串"""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# --- Token 过期检测 ---

def is_token_expiring_within(credentials: KiroCredentials, minutes: int) -> Optional[bool]:
    """检查 Token 是否在指定分钟内过期"""
    if not credentials.expires_at:
        return None
    expires = _parse_rfc3339(credentials.expires_at)
    if expires is None:
        return None
    return expires <= _utc_now() + timedelta(minutes=minutes)


def is_token_expired(credentials: KiroCredentials) -> bool:
    """检查 Token 是否已过期（提前 5 分钟判断）"""
    result = is_token_expiring_within(credentials, 5)
    return result if result is not None else True


def is_token_expiring_soon(credentials: KiroCredentials) -> bool:
    """检查 Token 是否即将过期（10 分钟内）"""
    result = is_token_expiring_within(credentials, 10)
    return result if result is not None else False


# --- Token 验证 ---

def validate_refresh_token(credentials: KiroCredentials) -> None:
    """验证 refreshToken 的基本有效性"""
    rt = credentials.refresh_token
    if rt is None:
        raise ValueError("缺少 refreshToken")
    if not rt:
        raise ValueError("refreshToken 为空")
    if len(rt) < 100 or rt.endswith("...") or "..." in rt:
        raise ValueError(
            f"refreshToken 已被截断（长度: {len(rt)} 字符）。\n"
            "这通常是 Kiro IDE 为了防止凭证被第三方工具使用而故意截断的。"
        )


# --- Token 刷新 ---

async def refresh_token(
    credentials: KiroCredentials,
    config: Config,
    proxy: Optional[ProxyConfig] = None,
) -> KiroCredentials:
    """刷新 Token，根据 auth_method 选择 Social 或 IdC"""
    validate_refresh_token(credentials)

    auth_method = credentials.auth_method
    if auth_method is None:
        auth_method = "idc" if (credentials.client_id and credentials.client_secret) else "social"

    if auth_method.lower() in ("idc", "builder-id", "iam"):
        return await refresh_idc_token(credentials, config, proxy)
    else:
        return await refresh_social_token(credentials, config, proxy)


async def refresh_social_token(
    credentials: KiroCredentials,
    config: Config,
    proxy: Optional[ProxyConfig] = None,
) -> KiroCredentials:
    """刷新 Social Token"""
    logger.info("正在刷新 Social Token...")

    rt = credentials.refresh_token
    region = credentials.effective_auth_region(config)
    refresh_url = f"https://prod.{region}.auth.desktop.kiro.dev/refreshToken"
    refresh_domain = f"prod.{region}.auth.desktop.kiro.dev"
    machine_id = generate_from_credentials(credentials, config)
    if machine_id is None:
        raise RuntimeError("无法生成 machineId")
    kiro_version = config.kiro_version

    client = build_client(proxy, timeout_secs=60)
    body = RefreshRequest(refresh_token=rt)

    try:
        response = await client.post(
            refresh_url,
            json=body.to_dict(),
            headers={
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "User-Agent": f"KiroIDE-{kiro_version}-{machine_id}",
                "Accept-Encoding": "gzip, compress, deflate, br",
                "host": refresh_domain,
                "Connection": "close",
            },
        )
    finally:
        await client.aclose()

    if response.status_code >= 400:
        body_text = response.text
        error_map = {
            401: "OAuth 凭证已过期或无效，需要重新认证",
            403: "权限不足，无法刷新 Token",
            429: "请求过于频繁，已被限流",
        }
        if 500 <= response.status_code < 600:
            msg = "服务器错误，AWS OAuth 服务暂时不可用"
        else:
            msg = error_map.get(response.status_code, "Token 刷新失败")
        raise RuntimeError(f"{msg}: {response.status_code} {body_text}")

    data = RefreshResponse.from_dict(response.json())
    new_cred = credentials.clone()
    new_cred.access_token = data.access_token
    if data.refresh_token:
        new_cred.refresh_token = data.refresh_token
    if data.profile_arn:
        new_cred.profile_arn = data.profile_arn
    if data.expires_in is not None:
        expires_at = _utc_now() + timedelta(seconds=data.expires_in)
        new_cred.expires_at = expires_at.isoformat()
    return new_cred


async def refresh_idc_token(
    credentials: KiroCredentials,
    config: Config,
    proxy: Optional[ProxyConfig] = None,
) -> KiroCredentials:
    """刷新 IdC Token (AWS SSO OIDC)"""
    logger.info("正在刷新 IdC Token...")

    rt = credentials.refresh_token
    client_id = credentials.client_id
    client_secret = credentials.client_secret
    if not client_id:
        raise ValueError("IdC 刷新需要 clientId")
    if not client_secret:
        raise ValueError("IdC 刷新需要 clientSecret")

    region = credentials.effective_auth_region(config)
    refresh_url = f"https://oidc.{region}.amazonaws.com/token"

    client = build_client(proxy, timeout_secs=60)
    body = IdcRefreshRequest(
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=rt,
    )

    try:
        response = await client.post(
            refresh_url,
            json=body.to_dict(),
            headers={
                "Content-Type": "application/json",
                "Host": f"oidc.{region}.amazonaws.com",
                "Connection": "keep-alive",
                "x-amz-user-agent": IDC_AMZ_USER_AGENT,
                "Accept": "*/*",
                "Accept-Language": "*",
                "sec-fetch-mode": "cors",
                "User-Agent": "node",
                "Accept-Encoding": "br, gzip, deflate",
            },
        )
    finally:
        await client.aclose()

    if response.status_code >= 400:
        body_text = response.text
        error_map = {
            401: "IdC 凭证已过期或无效，需要重新认证",
            403: "权限不足，无法刷新 Token",
            429: "请求过于频繁，已被限流",
        }
        if 500 <= response.status_code < 600:
            msg = "服务器错误，AWS OIDC 服务暂时不可用"
        else:
            msg = error_map.get(response.status_code, "IdC Token 刷新失败")
        raise RuntimeError(f"{msg}: {response.status_code} {body_text}")

    data = IdcRefreshResponse.from_dict(response.json())
    new_cred = credentials.clone()
    new_cred.access_token = data.access_token
    if data.refresh_token:
        new_cred.refresh_token = data.refresh_token
    if data.expires_in is not None:
        expires_at = _utc_now() + timedelta(seconds=data.expires_in)
        new_cred.expires_at = expires_at.isoformat()
    return new_cred


# --- 使用额度查询 ---

async def get_usage_limits(
    credentials: KiroCredentials,
    config: Config,
    token: str,
    proxy: Optional[ProxyConfig] = None,
) -> UsageLimitsResponse:
    """获取使用额度信息"""
    logger.debug("正在获取使用额度信息...")

    region = credentials.effective_api_region(config)
    host = f"q.{region}.amazonaws.com"
    machine_id = generate_from_credentials(credentials, config)
    if machine_id is None:
        raise RuntimeError("无法生成 machineId")
    kiro_version = config.kiro_version

    url = f"https://{host}/getUsageLimits?origin=AI_EDITOR&resourceType=AGENTIC_REQUEST"
    if credentials.profile_arn:
        from urllib.parse import quote
        url += f"&profileArn={quote(credentials.profile_arn, safe='')}"

    user_agent = (
        f"aws-sdk-js/1.0.0 ua/2.1 os/darwin#24.6.0 lang/js md/nodejs#22.21.1 "
        f"api/codewhispererruntime#1.0.0 m/N,E KiroIDE-{kiro_version}-{machine_id}"
    )
    amz_user_agent = f"{USAGE_LIMITS_AMZ_USER_AGENT_PREFIX} KiroIDE-{kiro_version}-{machine_id}"

    client = build_client(proxy, timeout_secs=60)
    try:
        response = await client.get(
            url,
            headers={
                "x-amz-user-agent": amz_user_agent,
                "User-Agent": user_agent,
                "host": host,
                "amz-sdk-invocation-id": str(uuid.uuid4()),
                "amz-sdk-request": "attempt=1; max=1",
                "Authorization": f"Bearer {token}",
                "Connection": "close",
            },
        )
    finally:
        await client.aclose()

    if response.status_code >= 400:
        body_text = response.text
        error_map = {
            401: "认证失败，Token 无效或已过期",
            403: "权限不足，无法获取使用额度",
            429: "请求过于频繁，已被限流",
        }
        if 500 <= response.status_code < 600:
            msg = "服务器错误，AWS 服务暂时不可用"
        else:
            msg = error_map.get(response.status_code, "获取使用额度失败")
        raise RuntimeError(f"{msg}: {response.status_code} {body_text}")

    return UsageLimitsResponse.from_dict(response.json())


# ============================================================================
# 单凭据 Token 管理器
# ============================================================================

class TokenManager:
    """单凭据 Token 管理器"""

    def __init__(self, config: Config, credentials: KiroCredentials, proxy: Optional[ProxyConfig] = None):
        self._config = config
        self._credentials = credentials
        self._proxy = proxy

    @property
    def credentials(self) -> KiroCredentials:
        return self._credentials

    @property
    def config(self) -> Config:
        return self._config

    async def ensure_valid_token(self) -> str:
        """确保获取有效的访问 Token，过期时自动刷新"""
        if is_token_expired(self._credentials) or is_token_expiring_soon(self._credentials):
            self._credentials = await refresh_token(self._credentials, self._config, self._proxy)
            if is_token_expired(self._credentials):
                raise RuntimeError("刷新后的 Token 仍然无效或已过期")
        if not self._credentials.access_token:
            raise RuntimeError("没有可用的 accessToken")
        return self._credentials.access_token

    async def get_usage_limits(self) -> UsageLimitsResponse:
        token = await self.ensure_valid_token()
        return await get_usage_limits(self._credentials, self._config, token, self._proxy)


# ============================================================================
# 多凭据 Token 管理器 - 内部类型
# ============================================================================

class _DisabledReason:
    MANUAL = "manual"
    TOO_MANY_FAILURES = "too_many_failures"
    QUOTA_EXCEEDED = "quota_exceeded"


@dataclass
class _CredentialEntry:
    id: int
    credentials: KiroCredentials
    failure_count: int = 0
    disabled: bool = False
    disabled_reason: Optional[str] = None
    success_count: int = 0
    last_used_at: Optional[str] = None
    session_count: int = 0  # 本次运行的成功次数，重启归零
    balance_score: int = 0  # 均衡点数（实时计算：per_cred_rpm - time_decay）
    _request_ts: list = None  # 单凭据请求时间戳（用于计算单独 RPM）
    transient_disabled_until: Optional[float] = None

    def __post_init__(self):
        if self._request_ts is None:
            self._request_ts = []


@dataclass
class CredentialEntrySnapshot:
    """凭据条目快照（用于 Admin API）"""
    id: int
    priority: int
    disabled: bool
    failure_count: int
    auth_method: Optional[str]
    has_profile_arn: bool
    expires_at: Optional[str]
    refresh_token_hash: Optional[str]
    email: Optional[str]
    success_count: int
    session_count: int
    last_used_at: Optional[str]
    has_proxy: bool
    proxy_url: Optional[str] = None
    subscription_title: Optional[str] = None
    balance_score: int = 0
    balance_decay: int = 0   # 时间减益分量
    balance_rpm: int = 0     # 单凭据 RPM 分量
    balance_current_usage: Optional[float] = None
    balance_usage_limit: Optional[float] = None
    balance_remaining: Optional[float] = None
    balance_usage_percentage: Optional[float] = None
    balance_next_reset_at: Optional[float] = None
    balance_updated_at: Optional[str] = None
    disabled_reason: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "id": self.id, "priority": self.priority, "disabled": self.disabled,
            "failureCount": self.failure_count, "authMethod": self.auth_method,
            "hasProfileArn": self.has_profile_arn, "expiresAt": self.expires_at,
            "refreshTokenHash": self.refresh_token_hash, "email": self.email,
            "successCount": self.success_count, "sessionCount": self.session_count,
            "lastUsedAt": self.last_used_at, "hasProxy": self.has_proxy,
            "subscriptionTitle": self.subscription_title,
            "balanceScore": self.balance_score,
            "balanceDecay": self.balance_decay,
            "balanceRpm": self.balance_rpm,
            "balanceCurrentUsage": self.balance_current_usage,
            "balanceUsageLimit": self.balance_usage_limit,
            "balanceRemaining": self.balance_remaining,
            "balanceUsagePercentage": self.balance_usage_percentage,
            "balanceNextResetAt": self.balance_next_reset_at,
            "balanceUpdatedAt": self.balance_updated_at,
        }
        if self.proxy_url is not None:
            d["proxyUrl"] = self.proxy_url
        return d


@dataclass
class ManagerSnapshot:
    """凭据管理器状态快照"""
    entries: list
    current_id: int
    total: int
    available: int

    def to_dict(self) -> dict:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "currentId": self.current_id,
            "total": self.total,
            "available": self.available,
        }


@dataclass
class CallContext:
    """API 调用上下文，绑定凭据 ID、凭据信息和 Token"""
    id: int
    credentials: KiroCredentials
    token: str


# ============================================================================
# 多凭据 Token 管理器
# ============================================================================

class MultiTokenManager:
    """多凭据 Token 管理器，支持故障转移、负载均衡、统计持久化"""

    def __init__(
        self,
        config: Config,
        credentials: list[KiroCredentials],
        proxy: Optional[ProxyConfig] = None,
        credentials_path: Optional[Path] = None,
        is_multiple_format: bool = False,
    ):
        self._config = config
        self._proxy = proxy
        self._credentials_path = credentials_path
        self._is_multiple_format = is_multiple_format

        # 为没有 ID 的凭据分配新 ID
        max_existing_id = max((c.id for c in credentials if c.id is not None), default=0)
        next_id = max_existing_id + 1
        has_new_ids = False
        has_new_machine_ids = False

        entries: list[_CredentialEntry] = []
        for cred in credentials:
            cred.canonicalize_auth_method()
            if cred.id is None:
                cred.id = next_id
                next_id += 1
                has_new_ids = True
            cid = cred.id
            if cred.machine_id is None:
                mid = generate_from_credentials(cred, config)
                if mid:
                    cred.machine_id = mid
                    has_new_machine_ids = True
            entries.append(_CredentialEntry(
                id=cid,
                credentials=cred.clone(),
                disabled=cred.disabled,
                disabled_reason=_DisabledReason.MANUAL if cred.disabled else None,
            ))

        # 检测重复 ID
        seen_ids = set()
        dup_ids = []
        for e in entries:
            if e.id in seen_ids:
                dup_ids.append(e.id)
            seen_ids.add(e.id)
        if dup_ids:
            raise ValueError(f"检测到重复的凭据 ID: {dup_ids}")

        # 选择初始凭据
        initial_id = min((e for e in entries), key=lambda e: e.credentials.priority).id if entries else 0

        self._entries = entries
        self._current_id = initial_id
        self._lock = threading.Lock()  # 保护同步数据
        self._refresh_lock = asyncio.Lock()  # 保护异步刷新操作
        self._groups: dict[int, str] = {}  # {credential_id: "free"|"pro"|"priority"}
        self._free_models: set[str] = set()  # 免费账号支持的模型 ID 集合
        self._request_timestamps: list[float] = []  # RPM 滑动窗口
        self._peak_rpm: int = 0
        self._model_call_counts: dict[str, int] = {}  # 每模型本次会话调用计数
        self._model_cred_counts: dict[str, dict[int, int]] = {}  # {model: {cred_id: count}} 细粒度
        self._last_stats_save_at: Optional[float] = None
        self._stats_dirty = False

        # 持久化新分配的 ID / machineId
        if has_new_ids or has_new_machine_ids:
            try:
                self.persist_credentials()
                logger.info("已补全凭据 ID/machineId 并写回配置文件")
            except Exception as e:
                logger.warning("补全凭据 ID/machineId 后持久化失败: %s", e)

        self.load_stats()

    def _compute_balance(self, e: "_CredentialEntry", now: float) -> tuple:
        """实时计算均衡三元组 (score, cred_rpm, decay)，保证 score = rpm - decay"""
        cutoff = now - 60
        e._request_ts = [t for t in e._request_ts if t > cutoff]
        cred_rpm = len(e._request_ts)
        # decay：空闲秒数 / 5，活跃期间为 0
        if cred_rpm > 0 or not e.last_used_at:
            decay = 0
        else:
            try:
                last = datetime.fromisoformat(e.last_used_at.replace("Z", "+00:00"))
                idle = max(0, (datetime.now(timezone.utc) - last).total_seconds())
                decay = min(int(idle / 5), 100)
            except Exception:
                decay = 0
        score = max(-100, min(100, cred_rpm - decay))
        e.balance_score = score
        return score, cred_rpm, decay

    def _calculate_credential_score(self, entry: "_CredentialEntry") -> int:
        """返回凭据均衡点数，越小越优先"""
        score, _, _ = self._compute_balance(entry, time.time())
        return score

    def config(self) -> Config:
        return self._config

    def credentials(self) -> KiroCredentials:
        with self._lock:
            for e in self._entries:
                if e.id == self._current_id:
                    return e.credentials.clone()
            return KiroCredentials()

    def total_count(self) -> int:
        with self._lock:
            return len(self._entries)

    def available_count(self) -> int:
        with self._lock:
            return sum(1 for e in self._entries if not e.disabled)

    def _model_is_free(self, model: str) -> bool:
        """判断模型是否在免费列表中（支持 Kiro 内部 ID 和 Anthropic ID 两种格式匹配）"""
        if not self._free_models:
            return False
        # 直接匹配
        if model in self._free_models:
            return True
        # Kiro 内部 ID（如 claude-sonnet-4.5）匹配 Anthropic ID（如 claude-sonnet-4-5-20250929）
        ml = model.lower()
        for fm in self._free_models:
            fl = fm.lower()
            # 提取模型族关键词匹配
            if "sonnet" in ml and "sonnet" in fl:
                # 版本匹配：4.5 ↔ 4-5, 4.6 ↔ 4-6
                if ("4.5" in ml or "4-5" in ml) and ("4.5" in fl or "4-5" in fl):
                    # thinking 后缀匹配
                    m_think = "thinking" in ml
                    f_think = "thinking" in fl
                    if m_think == f_think:
                        return True
                elif ("4.6" in ml or "4-6" in ml) and ("4.6" in fl or "4-6" in fl):
                    if ("thinking" in ml) == ("thinking" in fl):
                        return True
            elif "opus" in ml and "opus" in fl:
                if ("4.5" in ml or "4-5" in ml) and ("4.5" in fl or "4-5" in fl):
                    if ("thinking" in ml) == ("thinking" in fl):
                        return True
                elif ("4.6" in ml or "4-6" in ml) and ("4.6" in fl or "4-6" in fl):
                    if ("thinking" in ml) == ("thinking" in fl):
                        return True
            elif "haiku" in ml and "haiku" in fl:
                if ("thinking" in ml) == ("thinking" in fl):
                    return True
        return False

    @staticmethod
    def _is_transiently_unavailable(entry: "_CredentialEntry", now: Optional[float] = None) -> bool:
        until = entry.transient_disabled_until
        if until is None:
            return False
        current = time.time() if now is None else now
        return until > current

    def _select_next_credential(self, model: Optional[str] = None) -> Optional[tuple[int, KiroCredentials]]:
        """根据动态均衡 + 分组路由选择下一个凭据（需在 _lock 内调用）"""
        now = time.time()

        available = [
            e for e in self._entries
            if not e.disabled
            and not self._is_transiently_unavailable(e, now)
        ]
        if not available:
            return None

        # 分组路由：免费模型优先走 free 组，非免费模型排除 free 组
        if model and self._free_models:
            if self._model_is_free(model):
                free_creds = [e for e in available if self._groups.get(e.id) == "free"]
                if free_creds:
                    entry = min(free_creds, key=lambda e: (
                        self._calculate_credential_score(e),
                        e.credentials.priority,
                        e.id
                    ))
                    logger.debug("模型 %s 路由到 free 组凭据 #%d", model, entry.id)
                    return (entry.id, entry.credentials.clone())
                # free 组耗尽，回退到 pro/priority
                logger.debug("模型 %s 为免费模型但 free 组无可用凭据，回退", model)
            else:
                non_free = [e for e in available if self._groups.get(e.id) != "free"]
                if non_free:
                    available = non_free

        # 动态均衡 + 优先级
        entry = min(available, key=lambda e: (
            self._calculate_credential_score(e),
            e.credentials.priority,
            e.id
        ))
        return (entry.id, entry.credentials.clone())

    def _build_no_candidate_error(self, model: Optional[str], total: int) -> str:
        """在没有候选凭据时给出更准确的诊断信息（需在 _lock 内调用）"""
        enabled_entries = [e for e in self._entries if not e.disabled]
        enabled_count = len(enabled_entries)
        cooled_down_entries = [
            e for e in enabled_entries
            if self._is_transiently_unavailable(e)
        ]

        if enabled_count == 0:
            return f"所有凭据均已禁用（0/{total}）"

        if cooled_down_entries and len(cooled_down_entries) == enabled_count:
            nearest = min(e.transient_disabled_until or 0 for e in cooled_down_entries)
            remaining = max(int(nearest - time.time()), 0)
            return (
                f"当前暂无可用凭据（启用凭据 {enabled_count}/{total}，"
                f"所有启用凭据均处于临时冷却中，约 {remaining}s 后重试）"
            )

        return f"当前模型 {model or '未指定'} 没有可用凭据（启用凭据 {enabled_count}/{total}）"

    def _switch_to_next_by_priority(self):
        """切换到下一个优先级最高的可用凭据（排除当前）"""
        with self._lock:
            candidates = [e for e in self._entries if not e.disabled and e.id != self._current_id]
            if candidates:
                best = min(candidates, key=lambda e: e.credentials.priority)
                self._current_id = best.id
                logger.info("已切换到凭据 #%d（优先级 %d）", best.id, best.credentials.priority)

    def _select_highest_priority(self):
        """选择优先级最高的未禁用凭据作为当前凭据"""
        with self._lock:
            candidates = [e for e in self._entries if not e.disabled]
            if candidates:
                best = min(candidates, key=lambda e: e.credentials.priority)
                if best.id != self._current_id:
                    logger.info("优先级变更后切换凭据: #%d -> #%d（优先级 %d）",
                                self._current_id, best.id, best.credentials.priority)
                    self._current_id = best.id

    async def acquire_context(self, model: Optional[str] = None) -> CallContext:
        """获取 API 调用上下文，自动刷新 Token 并支持故障转移"""
        total = self.total_count()
        tried_count = 0

        while True:
            if tried_count >= total:
                raise RuntimeError(
                    f"所有凭据均无法获取有效 Token（可用: {self.available_count()}/{total}）"
                )

            with self._lock:
                best = self._select_next_credential(model)

                if best:
                    cid, cred = best
                    self._current_id = cid
                else:
                    raise RuntimeError(self._build_no_candidate_error(model, total))

            # 尝试获取/刷新 Token
            try:
                ctx = await self._try_ensure_token(cid, cred)
                return ctx
            except Exception as e:
                logger.warning("凭据 #%d Token 刷新失败，尝试下一个凭据: %s", cid, e)
                self.report_failure(cid)
                tried_count += 1

    async def _try_ensure_token(self, cid: int, credentials: KiroCredentials) -> CallContext:
        """尝试使用指定凭据获取有效 Token（双重检查锁定）"""
        needs_refresh = is_token_expired(credentials) or is_token_expiring_soon(credentials)

        if needs_refresh:
            async with self._refresh_lock:
                # 第二次检查
                with self._lock:
                    current_creds = None
                    for e in self._entries:
                        if e.id == cid:
                            current_creds = e.credentials.clone()
                            break
                    if current_creds is None:
                        raise RuntimeError(f"凭据 #{cid} 不存在")

                if is_token_expired(current_creds) or is_token_expiring_soon(current_creds):
                    effective_proxy = current_creds.effective_proxy(self._proxy)
                    new_creds = await refresh_token(current_creds, self._config, effective_proxy)
                    if is_token_expired(new_creds):
                        raise RuntimeError("刷新后的 Token 仍然无效或已过期")
                    with self._lock:
                        for e in self._entries:
                            if e.id == cid:
                                e.credentials = new_creds.clone()
                                break
                    try:
                        self.persist_credentials()
                    except Exception as e:
                        logger.warning("Token 刷新后持久化失败（不影响本次请求）: %s", e)
                    creds = new_creds
                else:
                    logger.debug("Token 已被其他请求刷新，跳过刷新")
                    creds = current_creds
        else:
            creds = credentials

        if not creds.access_token:
            raise RuntimeError("没有可用的 accessToken")
        return CallContext(id=cid, credentials=creds, token=creds.access_token)

    def report_success(self, cid: int, model: Optional[str] = None):
        """报告指定凭据 API 调用成功"""
        now = time.time()
        with self._lock:
            for e in self._entries:
                if e.id == cid:
                    e.failure_count = 0
                    e.transient_disabled_until = None
                    e.success_count += 1
                    e.session_count += 1
                    e._request_ts.append(now)
                    e.last_used_at = _utc_now().isoformat()
                    logger.debug("凭据 #%d API 调用成功（本次 %d / 累计 %d）", cid, e.session_count, e.success_count)
                    break
            # RPM 追踪
            self._request_timestamps.append(now)
            cutoff = now - 60
            self._request_timestamps = [t for t in self._request_timestamps if t > cutoff]
            current_rpm = len(self._request_timestamps)
            if current_rpm > self._peak_rpm:
                self._peak_rpm = current_rpm
            # 每模型计数
            if model:
                self._model_call_counts[model] = self._model_call_counts.get(model, 0) + 1
                # 细粒度：model + credential_id
                if model not in self._model_cred_counts:
                    self._model_cred_counts[model] = {}
                mc = self._model_cred_counts[model]
                mc[cid] = mc.get(cid, 0) + 1
        self._save_stats_debounced()

    def report_failure(self, cid: int) -> bool:
        """报告指定凭据 API 调用失败，返回是否还有可用凭据"""
        with self._lock:
            entry = None
            for e in self._entries:
                if e.id == cid:
                    entry = e
                    break
            if entry is None:
                return any(not e.disabled for e in self._entries)

            entry.failure_count += 1
            entry.transient_disabled_until = None
            entry.last_used_at = _utc_now().isoformat()
            logger.warning("凭据 #%d API 调用失败（%d/%d）", cid, entry.failure_count, MAX_FAILURES_PER_CREDENTIAL)

            if entry.failure_count >= MAX_FAILURES_PER_CREDENTIAL:
                entry.disabled = True
                entry.disabled_reason = _DisabledReason.TOO_MANY_FAILURES
                logger.error("凭据 #%d 已连续失败 %d 次，已被禁用", cid, entry.failure_count)
                # 切换到优先级最高的可用凭据
                candidates = [e for e in self._entries if not e.disabled]
                if candidates:
                    best = min(candidates, key=lambda e: e.credentials.priority)
                    self._current_id = best.id
                    logger.info("已切换到凭据 #%d（优先级 %d）", best.id, best.credentials.priority)
                else:
                    logger.error("所有凭据均已禁用！")

            result = any(not e.disabled for e in self._entries)
        self._save_stats_debounced()
        return result

    def report_quota_exhausted(self, cid: int) -> bool:
        """报告指定凭据额度已用尽，立即禁用并切换"""
        with self._lock:
            entry = None
            for e in self._entries:
                if e.id == cid:
                    entry = e
                    break
            if entry is None:
                return any(not e.disabled for e in self._entries)
            if entry.disabled:
                return any(not e.disabled for e in self._entries)

            entry.disabled = True
            entry.disabled_reason = _DisabledReason.QUOTA_EXCEEDED
            entry.transient_disabled_until = None
            entry.last_used_at = _utc_now().isoformat()
            entry.failure_count = MAX_FAILURES_PER_CREDENTIAL
            logger.error("凭据 #%d 额度已用尽（MONTHLY_REQUEST_COUNT），已被禁用", cid)

            candidates = [e for e in self._entries if not e.disabled]
            if candidates:
                best = min(candidates, key=lambda e: e.credentials.priority)
                self._current_id = best.id
                logger.info("已切换到凭据 #%d（优先级 %d）", best.id, best.credentials.priority)
                result = True
            else:
                logger.error("所有凭据均已禁用！")
                result = False
        self._save_stats_debounced()
        return result

    def report_transient_failure(self, cid: int, cooldown_secs: int = TRANSIENT_FAILURE_COOLDOWN_SECS) -> bool:
        """报告指定凭据遇到瞬态错误，短暂冷却后再参与调度。"""
        cooldown_secs = max(int(cooldown_secs), 1)
        with self._lock:
            entry = None
            for e in self._entries:
                if e.id == cid:
                    entry = e
                    break
            if entry is None:
                return any(not e.disabled and not self._is_transiently_unavailable(e) for e in self._entries)
            if entry.disabled:
                return any(not e.disabled and not self._is_transiently_unavailable(e) for e in self._entries)

            entry.last_used_at = _utc_now().isoformat()
            entry.transient_disabled_until = time.time() + cooldown_secs
            logger.warning(
                "凭据 #%d 遇到瞬态错误，进入临时冷却 %ds",
                cid,
                cooldown_secs,
            )

            candidates = [
                e for e in self._entries
                if not e.disabled and not self._is_transiently_unavailable(e) and e.id != cid
            ]
            if candidates:
                best = min(candidates, key=lambda e: e.credentials.priority)
                self._current_id = best.id
                logger.info("已切换到凭据 #%d（优先级 %d）", best.id, best.credentials.priority)
                result = True
            else:
                result = any(not e.disabled and not self._is_transiently_unavailable(e) for e in self._entries)
        self._save_stats_debounced()
        return result

    def switch_to_next(self) -> bool:
        """切换到优先级最高的可用凭据（排除当前）"""
        with self._lock:
            candidates = [e for e in self._entries if not e.disabled and e.id != self._current_id]
            if candidates:
                best = min(candidates, key=lambda e: e.credentials.priority)
                self._current_id = best.id
                logger.info("已切换到凭据 #%d（优先级 %d）", best.id, best.credentials.priority)
                return True
            return any(e.id == self._current_id and not e.disabled for e in self._entries)

    async def get_usage_limits(self) -> UsageLimitsResponse:
        ctx = await self.acquire_context(None)
        effective_proxy = ctx.credentials.effective_proxy(self._proxy)
        return await get_usage_limits(ctx.credentials, self._config, ctx.token, effective_proxy)

    # ========================================================================
    # Admin API 方法
    # ========================================================================

    def snapshot(self) -> ManagerSnapshot:
        """获取管理器状态快照"""
        with self._lock:
            available = sum(1 for e in self._entries if not e.disabled)
            snap_entries = []
            now_ts = time.time()
            for e in self._entries:
                am = e.credentials.auth_method
                if am and am.lower() in ("builder-id", "iam"):
                    am = "idc"
                # 用统一方法计算均衡三元组
                score, cred_rpm, decay = self._compute_balance(e, now_ts)
                snap_entries.append(CredentialEntrySnapshot(
                    id=e.id,
                    priority=e.credentials.priority,
                    disabled=e.disabled,
                    failure_count=e.failure_count,
                    auth_method=am,
                    has_profile_arn=e.credentials.profile_arn is not None,
                    expires_at=e.credentials.expires_at,
                    refresh_token_hash=_sha256_hex(e.credentials.refresh_token) if e.credentials.refresh_token else None,
                    email=e.credentials.email,
                    success_count=e.success_count,
                    session_count=e.session_count,
                    last_used_at=e.last_used_at,
                    has_proxy=e.credentials.proxy_url is not None,
                    proxy_url=e.credentials.proxy_url,
                    subscription_title=e.credentials.subscription_title,
                    balance_score=score if not e.disabled else 0,
                    balance_decay=decay if not e.disabled else 0,
                    balance_rpm=cred_rpm if not e.disabled else 0,
                    balance_current_usage=e.credentials.balance_current_usage,
                    balance_usage_limit=e.credentials.balance_usage_limit,
                    balance_remaining=e.credentials.balance_remaining,
                    balance_usage_percentage=e.credentials.balance_usage_percentage,
                    balance_next_reset_at=e.credentials.balance_next_reset_at,
                    balance_updated_at=e.credentials.balance_updated_at,
                    disabled_reason=e.disabled_reason,
                ))
            return ManagerSnapshot(
                entries=snap_entries,
                current_id=self._current_id,
                total=len(self._entries),
                available=available,
            )

    def set_disabled(self, cid: int, disabled: bool):
        with self._lock:
            entry = self._find_entry(cid)
            entry.disabled = disabled
            if not disabled:
                entry.failure_count = 0
                entry.disabled_reason = None
            else:
                entry.disabled_reason = _DisabledReason.MANUAL
        self.persist_credentials()

    def set_priority(self, cid: int, priority: int):
        with self._lock:
            entry = self._find_entry(cid)
            entry.credentials.priority = priority
        self._select_highest_priority()
        self.persist_credentials()

    def reset_and_enable(self, cid: int):
        with self._lock:
            entry = self._find_entry(cid)
            entry.failure_count = 0
            entry.disabled = False
            entry.disabled_reason = None
        self.persist_credentials()

    def reset_all_counters(self):
        """重置所有凭据的均衡点数、会话计数、成功计数、失败计数"""
        with self._lock:
            for e in self._entries:
                e.balance_score = 0
                e._request_ts.clear()
                e.session_count = 0
                e.success_count = 0
                e.failure_count = 0
                e.last_used_at = None
            self._request_timestamps.clear()
            self._peak_rpm = 0
            self._model_call_counts.clear()
            self._model_cred_counts.clear()
        self.persist_credentials()
        self.save_stats()

    async def get_usage_limits_for(self, cid: int) -> UsageLimitsResponse:
        """获取指定凭据的使用额度"""
        with self._lock:
            entry = self._find_entry(cid)
            cred = entry.credentials.clone()

        needs_refresh = is_token_expired(cred) or is_token_expiring_soon(cred)
        if needs_refresh:
            async with self._refresh_lock:
                with self._lock:
                    current_cred = self._find_entry(cid).credentials.clone()
                if is_token_expired(current_cred) or is_token_expiring_soon(current_cred):
                    effective_proxy = current_cred.effective_proxy(self._proxy)
                    new_cred = await refresh_token(current_cred, self._config, effective_proxy)
                    with self._lock:
                        self._find_entry(cid).credentials = new_cred.clone()
                    try:
                        self.persist_credentials()
                    except Exception as e:
                        logger.warning("Token 刷新后持久化失败: %s", e)
                    token = new_cred.access_token
                else:
                    token = current_cred.access_token
        else:
            token = cred.access_token

        if not token:
            raise RuntimeError("凭据无 access_token")

        with self._lock:
            cred = self._find_entry(cid).credentials.clone()
        effective_proxy = cred.effective_proxy(self._proxy)
        usage = await get_usage_limits(cred, self._config, token, effective_proxy)

        # 更新订阅等级 + 余额快照（持久化到凭据文件）
        sub_title = usage.subscription_title()
        current_usage = usage.current_usage_total()
        usage_limit = usage.usage_limit_total()
        remaining = max(usage_limit - current_usage, 0.0)
        usage_percentage = min(current_usage / usage_limit * 100.0, 100.0) if usage_limit > 0 else 0.0
        changed = False
        with self._lock:
            entry = self._find_entry(cid)
            if sub_title and entry.credentials.subscription_title != sub_title:
                entry.credentials.subscription_title = sub_title
                logger.info("凭据 #%d 订阅等级已更新: %s", cid, sub_title)
                changed = True
            entry.credentials.balance_current_usage = current_usage
            entry.credentials.balance_usage_limit = usage_limit
            entry.credentials.balance_remaining = remaining
            entry.credentials.balance_usage_percentage = usage_percentage
            entry.credentials.balance_next_reset_at = usage.next_date_reset
            entry.credentials.balance_updated_at = _utc_now().isoformat()
            changed = True
        if changed:
            try:
                self.persist_credentials()
            except Exception as e:
                logger.warning("余额快照更新后持久化失败: %s", e)
        return usage

    async def add_credential(self, new_cred: KiroCredentials) -> int:
        """添加新凭据，验证有效性后分配 ID 并持久化"""
        validate_refresh_token(new_cred)

        # 重复检测
        new_rt = new_cred.refresh_token
        if not new_rt:
            raise ValueError("缺少 refreshToken")
        new_hash = _sha256_hex(new_rt)
        with self._lock:
            for e in self._entries:
                if e.credentials.refresh_token and _sha256_hex(e.credentials.refresh_token) == new_hash:
                    raise ValueError("凭据已存在（refreshToken 重复）")

        # 验证凭据有效性
        effective_proxy = new_cred.effective_proxy(self._proxy)
        validated = await refresh_token(new_cred, self._config, effective_proxy)

        with self._lock:
            new_id = max((e.id for e in self._entries), default=0) + 1

        validated.id = new_id
        validated.priority = new_cred.priority
        validated.auth_method = new_cred.auth_method
        if validated.auth_method and validated.auth_method.lower() in ("builder-id", "iam"):
            validated.auth_method = "idc"
        validated.client_id = new_cred.client_id
        validated.client_secret = new_cred.client_secret
        validated.region = new_cred.region
        validated.auth_region = new_cred.auth_region
        validated.api_region = new_cred.api_region
        validated.machine_id = new_cred.machine_id
        validated.email = new_cred.email
        validated.proxy_url = new_cred.proxy_url
        validated.proxy_username = new_cred.proxy_username
        validated.proxy_password = new_cred.proxy_password

        with self._lock:
            self._entries.append(_CredentialEntry(
                id=new_id, credentials=validated,
            ))
        self.persist_credentials()
        logger.info("成功添加凭据 #%d", new_id)
        return new_id

    def delete_credential(self, cid: int):
        """删除凭据（必须先禁用）"""
        with self._lock:
            entry = self._find_entry(cid)
            if not entry.disabled:
                raise ValueError(f"只能删除已禁用的凭据（请先禁用凭据 #{cid}）")
            was_current = self._current_id == cid
            self._entries = [e for e in self._entries if e.id != cid]

        if was_current:
            self._select_highest_priority()

        with self._lock:
            if not self._entries:
                self._current_id = 0
                logger.info("所有凭据已删除，current_id 已重置为 0")

        self.persist_credentials()
        # 立即回写统计数据，清除已删除凭据的残留条目
        self.save_stats()
        logger.info("已删除凭据 #%d", cid)

    # ========================================================================
    # 持久化方法
    # ========================================================================

    def _find_entry(self, cid: int) -> _CredentialEntry:
        """查找凭据条目（需在 _lock 内调用）"""
        for e in self._entries:
            if e.id == cid:
                return e
        raise ValueError(f"凭据不存在: {cid}")

    def persist_credentials(self) -> bool:
        """将凭据列表回写到源文件"""
        if not self._is_multiple_format:
            return False
        if not self._credentials_path:
            return False

        with self._lock:
            creds = []
            for e in self._entries:
                c = e.credentials.clone()
                c.canonicalize_auth_method()
                c.disabled = e.disabled
                creds.append(c)

        data = [c.to_dict() for c in creds]
        json_str = json.dumps(data, indent=2, ensure_ascii=False)
        self._credentials_path.write_text(json_str, encoding="utf-8")
        logger.debug("已回写凭据到文件: %s", self._credentials_path)
        return True

    def cache_dir(self) -> Optional[Path]:
        if self._credentials_path:
            return self._credentials_path.parent
        return None

    @property
    def credentials_path(self) -> Optional[Path]:
        return self._credentials_path

    def _stats_path(self) -> Optional[Path]:
        d = self.cache_dir()
        return d / "kiro_stats.json" if d else None

    def load_stats(self):
        """从磁盘加载统计数据"""
        path = self._stats_path()
        if not path or not path.exists():
            return
        try:
            content = path.read_text(encoding="utf-8")
            stats = json.loads(content)
        except Exception as e:
            logger.warning("解析统计缓存失败，将忽略: %s", e)
            return

        with self._lock:
            for e in self._entries:
                s = stats.get(str(e.id))
                if s:
                    e.success_count = s.get("success_count", 0)
                    e.last_used_at = s.get("last_used_at")
            self._last_stats_save_at = time.monotonic()
            self._stats_dirty = False
        logger.info("已从缓存加载 %d 条统计数据", len(stats))

    def save_stats(self):
        """将当前统计数据持久化到磁盘"""
        path = self._stats_path()
        if not path:
            return
        with self._lock:
            stats = {
                str(e.id): {"success_count": e.success_count, "last_used_at": e.last_used_at}
                for e in self._entries
            }
        try:
            path.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
            with self._lock:
                self._last_stats_save_at = time.monotonic()
                self._stats_dirty = False
        except Exception as e:
            logger.warning("保存统计缓存失败: %s", e)

    def _save_stats_debounced(self):
        """按 debounce 策略决定是否立即落盘"""
        with self._lock:
            self._stats_dirty = True
            should_flush = (
                self._last_stats_save_at is None
                or (time.monotonic() - self._last_stats_save_at) >= STATS_SAVE_DEBOUNCE
            )
        if should_flush:
            self.save_stats()

    def get_stats(self) -> dict:
        """获取统计数据快照"""
        now = time.time()
        with self._lock:
            cutoff = now - 60
            self._request_timestamps = [t for t in self._request_timestamps if t > cutoff]
            rpm = len(self._request_timestamps)
            total_requests = sum(e.success_count for e in self._entries)
            session_requests = sum(e.session_count for e in self._entries)
            # 细粒度：{model: {cred_id_str: count}}
            model_cred = {
                m: {str(cid): cnt for cid, cnt in creds.items()}
                for m, creds in self._model_cred_counts.items()
            }
            return {
                "totalRequests": total_requests,
                "sessionRequests": session_requests,
                "rpm": rpm,
                "peakRpm": self._peak_rpm,
                "modelCounts": dict(self._model_call_counts),
                "modelCredCounts": model_cred,
                "credentialCount": len(self._entries),
                "availableCount": sum(1 for e in self._entries if not e.disabled),
            }

    # ========================================================================
    # 分组与路由
    # ========================================================================

    def update_groups(self, groups: dict[int, str]):
        """从 AdminService 同步分组信息"""
        with self._lock:
            self._groups = dict(groups)

    def update_free_models(self, models: set[str]):
        """更新免费模型列表"""
        with self._lock:
            self._free_models = set(models)

    def get_free_models(self) -> set[str]:
        with self._lock:
            return set(self._free_models)

    def __del__(self):
        if getattr(self, '_stats_dirty', False):
            self.save_stats()
