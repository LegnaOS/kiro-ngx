"""Admin API 业务逻辑服务 - 参考 src/admin/service.rs"""

import asyncio
import json
import logging
import time
import threading
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from admin.types import (
    AddCredentialRequest, AddCredentialResponse, BalanceResponse,
    BatchImportItemResult, BatchImportRequest, BatchImportResponse,
    CredentialStatusItem, CredentialsStatusResponse,
)
from admin.error import (
    AdminServiceError, NotFoundError, UpstreamError,
    InternalError, InvalidCredentialError,
)
from common.auth import sha256_hex
from kiro.model.credentials import KiroCredentials

logger = logging.getLogger(__name__)

# 余额缓存过期时间（秒）
BALANCE_CACHE_TTL_SECS = 300
AUTO_BALANCE_REFRESH_INTERVAL_SECS = 600


class AdminService:
    """Admin 服务，封装所有 Admin API 的业务逻辑"""

    def __init__(self, token_manager):
        self.token_manager = token_manager
        self._balance_cache: dict[int, dict] = {}  # {id: {"cached_at": float, "data": BalanceResponse}}
        self._cache_lock = threading.Lock()
        self._cache_path: Optional[Path] = None
        self._groups: dict[int, str] = {}  # {credential_id: "free"|"pro"|"priority"}
        self._groups_path: Optional[Path] = None
        self._routing_path: Optional[Path] = None
        self._custom_models: list[str] = []
        self._auto_balance_task: Optional[asyncio.Task] = None
        self._auto_balance_stop: Optional[asyncio.Event] = None

        cache_dir = getattr(token_manager, "cache_dir", None)
        if callable(cache_dir):
            d = cache_dir()
            if d:
                self._cache_path = Path(d) / "kiro_balance_cache.json"
                self._groups_path = Path(d) / "kiro_groups.json"
                self._routing_path = Path(d) / "kiro_routing.json"
        self._load_balance_cache()
        self._load_groups()
        self._load_routing()
        self._sync_groups_to_manager()

    def get_all_credentials(self) -> CredentialsStatusResponse:
        """获取所有凭据状态"""
        snapshot = self.token_manager.snapshot()
        credentials = []
        with self._cache_lock:
            cached_map = {cid: {"cached_at": v["cached_at"], "data": v["data"]} for cid, v in self._balance_cache.items()}

        for entry in snapshot.entries:
            # 自动分组：FREE → free，其他 → pro（除非手动设为 priority）
            cached = cached_map.get(entry.id)
            cached_balance = cached["data"] if cached else None
            sub_title = entry.subscription_title or (cached_balance.subscription_title if cached_balance else None)
            saved_group = self._groups.get(entry.id)
            is_free = sub_title and "FREE" in sub_title.upper()
            if is_free:
                group = "free"
            elif saved_group in ("pro", "priority"):
                group = saved_group
            else:
                group = "pro"

            # 均衡分量直接从 snapshot 读取
            balance_score = entry.balance_score if not entry.disabled else None
            balance_decay = entry.balance_decay if not entry.disabled else None
            balance_rpm = entry.balance_rpm if not entry.disabled else None
            balance_current_usage = entry.balance_current_usage
            balance_usage_limit = entry.balance_usage_limit
            balance_remaining = entry.balance_remaining
            balance_usage_percentage = entry.balance_usage_percentage
            balance_next_reset_at = entry.balance_next_reset_at
            balance_updated_at = entry.balance_updated_at

            if cached_balance:
                if balance_current_usage is None:
                    balance_current_usage = cached_balance.current_usage
                if balance_usage_limit is None:
                    balance_usage_limit = cached_balance.usage_limit
                if balance_remaining is None:
                    balance_remaining = cached_balance.remaining
                if balance_usage_percentage is None:
                    balance_usage_percentage = cached_balance.usage_percentage
                if balance_next_reset_at is None:
                    balance_next_reset_at = cached_balance.next_reset_at
                if balance_updated_at is None:
                    balance_updated_at = datetime.fromtimestamp(cached["cached_at"], timezone.utc).isoformat()

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
                subscription_title=sub_title,
                group=group,
                balance_score=balance_score,
                balance_decay=balance_decay,
                balance_rpm=balance_rpm,
                balance_current_usage=balance_current_usage,
                balance_usage_limit=balance_usage_limit,
                balance_remaining=balance_remaining,
                balance_usage_percentage=balance_usage_percentage,
                balance_next_reset_at=balance_next_reset_at,
                balance_updated_at=balance_updated_at,
                disabled_reason=entry.disabled_reason,
            ))

        credentials.sort(key=lambda c: c.priority)
        self._sync_groups_to_manager()
        stats = self.token_manager.get_stats()
        return CredentialsStatusResponse(
            total=snapshot.total,
            available=snapshot.available,
            current_id=snapshot.current_id,
            rpm=stats.get("rpm", 0),
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

    def reset_all_counters(self) -> None:
        """重置所有凭据的均衡点数和计数器"""
        self.token_manager.reset_all_counters()

    async def get_balance(self, id: int, force_refresh: bool = False) -> BalanceResponse:
        """获取凭据余额（带缓存）"""
        if not force_refresh:
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

    async def get_total_remaining_quota(self, force_refresh: bool = False) -> dict:
        """汇总当前所有启用凭据的剩余额度。"""
        snapshot = self.token_manager.snapshot()
        enabled_ids = [entry.id for entry in snapshot.entries if not entry.disabled]

        total_remaining = 0.0
        details: list[dict] = []
        failed: list[dict] = []

        for cid in enabled_ids:
            try:
                balance = await self.get_balance(cid, force_refresh=force_refresh)
                total_remaining += balance.remaining
                details.append({
                    "id": cid,
                    "remaining": balance.remaining,
                    "subscriptionTitle": balance.subscription_title,
                })
            except Exception as e:
                failed.append({"id": cid, "error": str(e)})

        return {
            "credentialCount": len(enabled_ids),
            "succeededCount": len(details),
            "failedCount": len(failed),
            "totalRemaining": round(total_remaining, 4),
            "details": details,
            "failed": failed,
        }

    async def refresh_all_balances(self, include_disabled: bool = True, force_refresh: bool = True) -> dict:
        """刷新全部凭据余额并更新缓存/持久化快照。"""
        snapshot = self.token_manager.snapshot()
        ids = [entry.id for entry in snapshot.entries if include_disabled or not entry.disabled]
        succeeded = 0
        failed: list[dict] = []

        for cid in ids:
            try:
                await self.get_balance(cid, force_refresh=force_refresh)
                succeeded += 1
            except Exception as e:
                failed.append({"id": cid, "error": str(e)})

        return {
            "credentialCount": len(ids),
            "succeededCount": succeeded,
            "failedCount": len(failed),
            "failed": failed,
        }

    def start_auto_balance_refresh(self) -> None:
        """启动后台余额自动刷新（10 分钟一次）。"""
        if self._auto_balance_task and not self._auto_balance_task.done():
            return
        self._auto_balance_stop = asyncio.Event()
        self._auto_balance_task = asyncio.create_task(self._auto_balance_refresh_loop())
        logger.info("已启动余额自动刷新任务（间隔 %d 秒）", AUTO_BALANCE_REFRESH_INTERVAL_SECS)

    async def stop_auto_balance_refresh(self) -> None:
        """停止后台余额自动刷新任务。"""
        if self._auto_balance_stop:
            self._auto_balance_stop.set()
        if self._auto_balance_task:
            self._auto_balance_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._auto_balance_task
        self._auto_balance_task = None
        self._auto_balance_stop = None

    async def _auto_balance_refresh_loop(self) -> None:
        while True:
            if self._auto_balance_stop and self._auto_balance_stop.is_set():
                break

            try:
                result = await self.refresh_all_balances(include_disabled=True, force_refresh=True)
                logger.info(
                    "自动余额刷新完成：成功 %d/%d，失败 %d",
                    result["succeededCount"],
                    result["credentialCount"],
                    result["failedCount"],
                )
            except Exception as e:
                logger.warning("自动余额刷新失败: %s", e)

            if not self._auto_balance_stop:
                await asyncio.sleep(AUTO_BALANCE_REFRESH_INTERVAL_SECS)
                continue

            try:
                await asyncio.wait_for(self._auto_balance_stop.wait(), timeout=AUTO_BALANCE_REFRESH_INTERVAL_SECS)
                break
            except asyncio.TimeoutError:
                continue

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

    def get_available_credential_counts(self) -> dict:
        """获取总凭据数和可用凭据数。"""
        snapshot = self.token_manager.snapshot()
        return {
            "total": snapshot.total,
            "available": snapshot.available,
        }

    async def batch_import_credentials(self, req: BatchImportRequest) -> BatchImportResponse:
        """服务端批量导入，行为对齐前端“批量导入”手动操作。"""
        snapshot = self.token_manager.snapshot()
        existing_hashes = {
            entry.refresh_token_hash
            for entry in snapshot.entries
            if entry.refresh_token_hash
        }
        fallback_regions = [r for r in req.regions if isinstance(r, str) and r.strip()]
        results: list[BatchImportItemResult] = []

        success_count = 0
        duplicate_count = 0
        fail_count = 0
        rollback_success_count = 0
        rollback_failed_count = 0
        rollback_skipped_count = 0

        for idx, cred in enumerate(req.credentials, start=1):
            refresh_token = cred.refresh_token.strip()
            token_hash = sha256_hex(refresh_token) if refresh_token else ""

            if not refresh_token:
                fail_count += 1
                results.append(BatchImportItemResult(
                    index=idx,
                    status="failed",
                    message="refreshToken 为空",
                    rollback_status="skipped",
                ))
                rollback_skipped_count += 1
                continue

            if token_hash in existing_hashes:
                duplicate_count += 1
                results.append(BatchImportItemResult(
                    index=idx,
                    status="duplicate",
                    message="该凭据已存在",
                ))
                continue

            client_id = cred.client_id.strip() if isinstance(cred.client_id, str) else cred.client_id
            client_secret = cred.client_secret.strip() if isinstance(cred.client_secret, str) else cred.client_secret
            if (client_id and not client_secret) or (client_secret and not client_id):
                fail_count += 1
                rollback_skipped_count += 1
                results.append(BatchImportItemResult(
                    index=idx,
                    status="failed",
                    message="idc 模式需要同时提供 clientId 和 clientSecret",
                    rollback_status="skipped",
                ))
                continue

            auth_method = "idc" if client_id and client_secret else "social"
            specified_region = (cred.auth_region or cred.region or "").strip() if (cred.auth_region or cred.region) else ""
            regions_to_try = []
            if specified_region:
                regions_to_try.append(specified_region)
            regions_to_try.extend([r for r in fallback_regions if r != specified_region])
            if not regions_to_try:
                regions_to_try = ["eu-north-1", "us-east-1"]

            added_credential = None
            added_credential_id = None
            last_error = None

            for region in regions_to_try:
                try:
                    add_req = AddCredentialRequest(
                        refresh_token=refresh_token,
                        auth_method=auth_method,
                        client_id=client_id,
                        client_secret=client_secret,
                        priority=cred.priority,
                        region=cred.region,
                        auth_region=region,
                        api_region=cred.api_region,
                        machine_id=cred.machine_id,
                        email=cred.email,
                        proxy_url=cred.proxy_url,
                        proxy_username=cred.proxy_username,
                        proxy_password=cred.proxy_password,
                    )
                    added_credential = await self.add_credential(add_req)
                    added_credential_id = added_credential.credential_id
                    break
                except Exception as e:
                    last_error = e

            if added_credential is None or added_credential_id is None:
                fail_count += 1
                rollback_skipped_count += 1
                message = str(last_error) if last_error else "所有区域均失败"
                results.append(BatchImportItemResult(
                    index=idx,
                    status="failed",
                    message=message,
                    rollback_status="skipped",
                ))
                continue

            if req.skip_verify:
                success_count += 1
                existing_hashes.add(token_hash)
                results.append(BatchImportItemResult(
                    index=idx,
                    status="verified",
                    message="导入成功",
                    email=added_credential.email,
                    credential_id=added_credential_id,
                ))
                continue

            try:
                balance = await self.get_balance(added_credential_id)
                success_count += 1
                existing_hashes.add(token_hash)
                results.append(BatchImportItemResult(
                    index=idx,
                    status="verified",
                    message="导入并验活成功",
                    email=added_credential.email,
                    credential_id=added_credential_id,
                    usage=f"{balance.current_usage}/{balance.usage_limit}",
                ))
            except Exception as e:
                fail_count += 1
                rollback_status = "skipped"
                rollback_error = None
                if added_credential_id is not None:
                    rollback_status, rollback_error = self._rollback_credential(added_credential_id)
                    if rollback_status == "success":
                        rollback_success_count += 1
                    elif rollback_status == "failed":
                        rollback_failed_count += 1
                    else:
                        rollback_skipped_count += 1
                else:
                    rollback_skipped_count += 1
                results.append(BatchImportItemResult(
                    index=idx,
                    status="failed",
                    message=str(e),
                    credential_id=added_credential_id,
                    rollback_status=rollback_status,
                    rollback_error=rollback_error,
                ))

        return BatchImportResponse(
            success=True,
            total=len(req.credentials),
            success_count=success_count,
            duplicate_count=duplicate_count,
            fail_count=fail_count,
            rollback_success_count=rollback_success_count,
            rollback_failed_count=rollback_failed_count,
            rollback_skipped_count=rollback_skipped_count,
            results=results,
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

    def _rollback_credential(self, cid: int) -> tuple[str, Optional[str]]:
        """回滚失败导入的凭据：先禁用，再删除。"""
        try:
            self.set_disabled(cid, True)
        except AdminServiceError as e:
            return "failed", f"禁用失败: {e}"

        try:
            self.delete_credential(cid)
            return "success", None
        except AdminServiceError as e:
            return "failed", f"删除失败: {e}"

    def set_credential_group(self, cid: int, group: str) -> None:
        """设置凭据分组"""
        if group not in ("free", "pro", "priority"):
            raise InvalidCredentialError(f"无效的分组: {group}")
        self._groups[cid] = group
        self._save_groups()

    def set_credential_groups_batch(self, groups: dict[int, str]) -> None:
        """批量设置凭据分组"""
        for cid, group in groups.items():
            if group not in ("free", "pro", "priority"):
                raise InvalidCredentialError(f"无效的分组: {group}")
            self._groups[cid] = group
        self._save_groups()
        self._sync_groups_to_manager()

    # ============ 余额缓存持久化 ============

    def _load_balance_cache(self):
        if not self._cache_path or not self._cache_path.exists():
            return
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            for k, v in data.items():
                bd = v["data"]
                self._balance_cache[int(k)] = {
                    "cached_at": v.get("cached_at", 0),
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

    # ============ 分组持久化 ============

    def _load_groups(self):
        if not self._groups_path or not self._groups_path.exists():
            return
        try:
            data = json.loads(self._groups_path.read_text(encoding="utf-8"))
            self._groups = {int(k): v for k, v in data.items()}
        except Exception as e:
            logger.warning("解析分组缓存失败，将忽略: %s", e)

    def _save_groups(self):
        if not self._groups_path:
            return
        try:
            self._groups_path.parent.mkdir(parents=True, exist_ok=True)
            self._groups_path.write_text(
                json.dumps({str(k): v for k, v in self._groups.items()}, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("保存分组缓存失败: %s", e)

    def _sync_groups_to_manager(self):
        """将当前分组信息同步到 token_manager 用于路由决策"""
        try:
            # 构建完整分组映射（包含自动分组）
            snapshot = self.token_manager.snapshot()
            full_groups: dict[int, str] = {}
            for entry in snapshot.entries:
                sub_title = entry.subscription_title
                saved_group = self._groups.get(entry.id)
                is_free = sub_title and "FREE" in sub_title.upper()
                if is_free:
                    full_groups[entry.id] = "free"
                elif saved_group in ("pro", "priority"):
                    full_groups[entry.id] = saved_group
                else:
                    full_groups[entry.id] = "pro"
            self.token_manager.update_groups(full_groups)
        except Exception as e:
            logger.warning("同步分组到 token_manager 失败: %s", e)

    # ============ 路由配置持久化 ============

    def _load_routing(self):
        if not self._routing_path or not self._routing_path.exists():
            return
        try:
            data = json.loads(self._routing_path.read_text(encoding="utf-8"))
            free_models = set(data.get("freeModels", []))
            self.token_manager.update_free_models(free_models)
            self._custom_models = list(data.get("customModels", []))
        except Exception as e:
            logger.warning("解析路由配置失败，将忽略: %s", e)

    def _save_routing(self):
        if not self._routing_path:
            return
        try:
            free_models = sorted(self.token_manager.get_free_models())
            self._routing_path.parent.mkdir(parents=True, exist_ok=True)
            self._routing_path.write_text(
                json.dumps({
                    "freeModels": free_models,
                    "customModels": self._custom_models,
                }, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("保存路由配置失败: %s", e)

    def get_free_models(self) -> list[str]:
        return sorted(self.token_manager.get_free_models())

    def set_free_models(self, models: list[str]) -> None:
        self.token_manager.update_free_models(set(models))
        self._save_routing()

    def get_custom_models(self) -> list[str]:
        return list(self._custom_models)

    def set_custom_models(self, models: list[str]) -> None:
        self._custom_models = list(models)
        self._save_routing()

    def get_stats(self) -> dict:
        from token_usage import get_token_usage_tracker
        stats = self.token_manager.get_stats()
        tracker = get_token_usage_tracker()
        if tracker:
            stats["tokenUsage"] = tracker.get_stats()
        else:
            stats["tokenUsage"] = {
                "today": {"input": 0, "output": 0},
                "yesterday": {"input": 0, "output": 0},
                "models": {},
            }
        return stats

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
