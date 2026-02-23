"""Admin API 业务逻辑服务 - 参考 src/admin/service.rs"""

import json
import logging
import time
import threading
from pathlib import Path
from typing import Optional

from admin.types import (
    AddCredentialRequest, AddCredentialResponse, BalanceResponse,
    CredentialStatusItem, CredentialsStatusResponse,
    LoadBalancingModeResponse, SetLoadBalancingModeRequest,
)
from admin.error import (
    AdminServiceError, NotFoundError, UpstreamError,
    InternalError, InvalidCredentialError,
)
from kiro.model.credentials import KiroCredentials

logger = logging.getLogger(__name__)

# 余额缓存过期时间（秒）
BALANCE_CACHE_TTL_SECS = 300


class AdminService:
    """Admin 服务，封装所有 Admin API 的业务逻辑"""

    def __init__(self, token_manager):
        self.token_manager = token_manager
        self._balance_cache: dict[int, dict] = {}  # {id: {"cached_at": float, "data": BalanceResponse}}
        self._cache_lock = threading.Lock()
        self._cache_path: Optional[Path] = None

        cache_dir = getattr(token_manager, "cache_dir", None)
        if callable(cache_dir):
            d = cache_dir()
            if d:
                self._cache_path = Path(d) / "kiro_balance_cache.json"
        self._load_balance_cache()

    def get_all_credentials(self) -> CredentialsStatusResponse:
        """获取所有凭据状态"""
        snapshot = self.token_manager.snapshot()
        credentials = []
        for entry in snapshot.entries:
            credentials.append(CredentialStatusItem(
                id=entry.id,
                priority=entry.priority,
                disabled=entry.disabled,
                failure_count=entry.failure_count,
                is_current=entry.id == snapshot.current_id,
                expires_at=entry.expires_at,
                auth_method=entry.auth_method,
                has_profile_arn=entry.has_profile_arn,
                refresh_token_hash=entry.refresh_token_hash,
                email=entry.email,
                success_count=entry.success_count,
                session_count=entry.session_count,
                last_used_at=entry.last_used_at,
                has_proxy=entry.has_proxy,
                proxy_url=entry.proxy_url,
            ))
        credentials.sort(key=lambda c: c.priority)
        return CredentialsStatusResponse(
            total=snapshot.total,
            available=snapshot.available,
            current_id=snapshot.current_id,
            credentials=credentials,
        )

    def set_disabled(self, id: int, disabled: bool) -> None:
        """设置凭据禁用状态"""
        snapshot = self.token_manager.snapshot()
        current_id = snapshot.current_id
        try:
            self.token_manager.set_disabled(id, disabled)
        except Exception as e:
            raise self._classify_error(e, id)
        # 禁用当前凭据时切换到下一个
        if disabled and id == current_id:
            try:
                self.token_manager.switch_to_next()
            except Exception:
                pass

    def set_priority(self, id: int, priority: int) -> None:
        """设置凭据优先级"""
        try:
            self.token_manager.set_priority(id, priority)
        except Exception as e:
            raise self._classify_error(e, id)

    def reset_and_enable(self, id: int) -> None:
        """重置失败计数并重新启用"""
        try:
            self.token_manager.reset_and_enable(id)
        except Exception as e:
            raise self._classify_error(e, id)

    async def get_balance(self, id: int) -> BalanceResponse:
        """获取凭据余额（带缓存）"""
        with self._cache_lock:
            cached = self._balance_cache.get(id)
            if cached:
                if (time.time() - cached["cached_at"]) < BALANCE_CACHE_TTL_SECS:
                    logger.debug("凭据 #%d 余额命中缓存", id)
                    return cached["data"]

        balance = await self._fetch_balance(id)

        with self._cache_lock:
            self._balance_cache[id] = {
                "cached_at": time.time(),
                "data": balance,
            }
        self._save_balance_cache()
        return balance

    async def _fetch_balance(self, id: int) -> BalanceResponse:
        """从上游获取余额"""
        try:
            usage = await self.token_manager.get_usage_limits_for(id)
        except Exception as e:
            raise self._classify_balance_error(e, id)

        current_usage = usage.current_usage_total()
        usage_limit = usage.usage_limit_total()
        remaining = max(usage_limit - current_usage, 0.0)
        usage_percentage = min(current_usage / usage_limit * 100.0, 100.0) if usage_limit > 0 else 0.0

        return BalanceResponse(
            id=id,
            subscription_title=usage.subscription_title(),
            current_usage=current_usage,
            usage_limit=usage_limit,
            remaining=remaining,
            usage_percentage=usage_percentage,
            next_reset_at=usage.next_date_reset,
        )

    async def add_credential(self, req: AddCredentialRequest) -> AddCredentialResponse:
        """添加新凭据"""
        email = req.email
        new_cred = KiroCredentials(
            refresh_token=req.refresh_token,
            auth_method=req.auth_method,
            client_id=req.client_id,
            client_secret=req.client_secret,
            priority=req.priority,
            region=req.region,
            auth_region=req.auth_region,
            api_region=req.api_region,
            machine_id=req.machine_id,
            email=req.email,
            proxy_url=req.proxy_url,
            proxy_username=req.proxy_username,
            proxy_password=req.proxy_password,
            disabled=False,
        )
        try:
            credential_id = await self.token_manager.add_credential(new_cred)
        except Exception as e:
            raise self._classify_add_error(e)

        # 主动获取订阅等级
        try:
            await self.token_manager.get_usage_limits_for(credential_id)
        except Exception as e:
            logger.warning("添加凭据后获取订阅等级失败（不影响凭据添加）: %s", e)

        return AddCredentialResponse(
            success=True,
            message=f"凭据添加成功，ID: {credential_id}",
            credential_id=credential_id,
            email=email,
        )

    def delete_credential(self, id: int) -> None:
        """删除凭据"""
        try:
            self.token_manager.delete_credential(id)
        except Exception as e:
            raise self._classify_delete_error(e, id)
        with self._cache_lock:
            self._balance_cache.pop(id, None)
        self._save_balance_cache()

    def get_load_balancing_mode(self) -> LoadBalancingModeResponse:
        return LoadBalancingModeResponse(mode=self.token_manager.get_load_balancing_mode())
    def set_load_balancing_mode(self, req: SetLoadBalancingModeRequest) -> LoadBalancingModeResponse:
        if req.mode not in ("priority", "balanced"):
            raise InvalidCredentialError("mode 必须是 'priority' 或 'balanced'")
        try:
            self.token_manager.set_load_balancing_mode(req.mode)
        except Exception as e:
            raise InternalError(str(e))
        return LoadBalancingModeResponse(mode=req.mode)

    # ============ 余额缓存持久化 ============

    def _load_balance_cache(self):
        if not self._cache_path or not self._cache_path.exists():
            return
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            now = time.time()
            for k, v in data.items():
                if (now - v.get("cached_at", 0)) < BALANCE_CACHE_TTL_SECS:
                    bd = v["data"]
                    self._balance_cache[int(k)] = {
                        "cached_at": v["cached_at"],
                        "data": BalanceResponse(
                            id=bd.get("id", 0),
                            subscription_title=bd.get("subscriptionTitle"),
                            current_usage=bd.get("currentUsage", 0.0),
                            usage_limit=bd.get("usageLimit", 0.0),
                            remaining=bd.get("remaining", 0.0),
                            usage_percentage=bd.get("usagePercentage", 0.0),
                            next_reset_at=bd.get("nextResetAt"),
                        ),
                    }
        except Exception as e:
            logger.warning("解析余额缓存失败，将忽略: %s", e)

    def _save_balance_cache(self):
        if not self._cache_path:
            return
        with self._cache_lock:
            data = {}
            for k, v in self._balance_cache.items():
                data[str(k)] = {
                    "cached_at": v["cached_at"],
                    "data": v["data"].to_dict(),
                }
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("保存余额缓存失败: %s", e)

    # ============ 错误分类 ============

    def _classify_error(self, e: Exception, id: int) -> AdminServiceError:
        msg = str(e)
        if "不存在" in msg:
            return NotFoundError(id)
        return InternalError(msg)

    def _classify_balance_error(self, e: Exception, id: int) -> AdminServiceError:
        msg = str(e)
        if "不存在" in msg:
            return NotFoundError(id)
        upstream_keywords = [
            "凭证已过期或无效", "权限不足", "已被限流", "服务器错误",
            "Token 刷新失败", "暂时不可用", "error trying to connect",
            "connection", "timeout", "timed out",
        ]
        if any(kw in msg for kw in upstream_keywords):
            return UpstreamError(msg)
        return InternalError(msg)

    def _classify_add_error(self, e: Exception) -> AdminServiceError:
        msg = str(e)
        invalid_keywords = [
            "缺少 refreshToken", "refreshToken 为空", "refreshToken 已被截断",
            "凭据已存在", "refreshToken 重复", "凭证已过期或无效",
            "权限不足", "已被限流",
        ]
        if any(kw in msg for kw in invalid_keywords):
            return InvalidCredentialError(msg)
        if any(kw in msg for kw in ("error trying to connect", "connection", "timeout")):
            return UpstreamError(msg)
        return InternalError(msg)

    def _classify_delete_error(self, e: Exception, id: int) -> AdminServiceError:
        msg = str(e)
        if "不存在" in msg:
            return NotFoundError(id)
        if "只能删除已禁用的凭据" in msg or "请先禁用凭据" in msg:
            return InvalidCredentialError(msg)
        return InternalError(msg)
