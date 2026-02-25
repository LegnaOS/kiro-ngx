"""Kiro API Provider - 参考 src/kiro/provider.rs

核心组件，负责与 Kiro API 通信
支持流式和非流式请求，支持多凭据故障转移和重试
"""

import asyncio
import json
import logging
import random
import threading
import uuid
from typing import Optional

import httpx

from http_client import ProxyConfig, build_client
from kiro.machine_id import generate_from_credentials
from kiro.model.credentials import KiroCredentials
from kiro.token_manager import CallContext, MultiTokenManager

logger = logging.getLogger(__name__)

# 每个凭据的最大重试次数
MAX_RETRIES_PER_CREDENTIAL = 3
# 总重试次数硬上限
MAX_TOTAL_RETRIES = 9


class KiroProvider:
    """Kiro API Provider，支持多凭据故障转移和重试"""

    def __init__(self, token_manager: MultiTokenManager, proxy: Optional[ProxyConfig] = None):
        self._token_manager = token_manager
        self._global_proxy = proxy
        self._client_cache: dict[Optional[ProxyConfig], httpx.AsyncClient] = {}
        self._cache_lock = threading.Lock()

        # 预热：构建全局代理对应的 Client
        initial_client = build_client(proxy, timeout_secs=720)
        self._client_cache[proxy] = initial_client

    @property
    def token_manager(self) -> MultiTokenManager:
        return self._token_manager

    def client_for(self, credentials: KiroCredentials) -> httpx.AsyncClient:
        """按代理配置缓存 httpx.AsyncClient"""
        effective = credentials.effective_proxy(self._global_proxy)
        with self._cache_lock:
            if effective in self._client_cache:
                return self._client_cache[effective]
            client = build_client(effective, timeout_secs=720)
            self._client_cache[effective] = client
            return client

    # --- URL 构建 ---

    def base_url(self) -> str:
        return f"https://q.{self._token_manager.config().effective_api_region()}.amazonaws.com/generateAssistantResponse"

    def mcp_url(self) -> str:
        return f"https://q.{self._token_manager.config().effective_api_region()}.amazonaws.com/mcp"

    def base_domain(self) -> str:
        return f"q.{self._token_manager.config().effective_api_region()}.amazonaws.com"

    def base_url_for(self, credentials: KiroCredentials) -> str:
        return f"https://q.{credentials.effective_api_region(self._token_manager.config())}.amazonaws.com/generateAssistantResponse"

    def mcp_url_for(self, credentials: KiroCredentials) -> str:
        return f"https://q.{credentials.effective_api_region(self._token_manager.config())}.amazonaws.com/mcp"

    def base_domain_for(self, credentials: KiroCredentials) -> str:
        return f"q.{credentials.effective_api_region(self._token_manager.config())}.amazonaws.com"

    @staticmethod
    def extract_model_from_request(request_body: str) -> Optional[str]:
        """从请求体中提取模型信息"""
        try:
            data = json.loads(request_body)
            return (data.get("conversationState", {})
                    .get("currentMessage", {})
                    .get("userInputMessage", {})
                    .get("modelId"))
        except (json.JSONDecodeError, AttributeError):
            return None

    def build_headers(self, ctx: CallContext) -> dict[str, str]:
        """构建 API 请求头"""
        config = self._token_manager.config()
        machine_id = generate_from_credentials(ctx.credentials, config)
        if not machine_id:
            raise RuntimeError("无法生成 machine_id，请检查凭证配置")

        kv = config.kiro_version
        os_name = config.system_version
        nv = config.node_version
        x_amz_ua = f"aws-sdk-js/1.0.27 KiroIDE-{kv}-{machine_id}"
        ua = f"aws-sdk-js/1.0.27 ua/2.1 os/{os_name} lang/js md/nodejs#{nv} api/codewhispererstreaming#1.0.27 m/E KiroIDE-{kv}-{machine_id}"

        return {
            "Content-Type": "application/json",
            "x-amzn-codewhisperer-optout": "true",
            "x-amzn-kiro-agent-mode": "vibe",
            "x-amz-user-agent": x_amz_ua,
            "User-Agent": ua,
            "Host": self.base_domain_for(ctx.credentials),
            "amz-sdk-invocation-id": str(uuid.uuid4()),
            "amz-sdk-request": "attempt=1; max=3",
            "Authorization": f"Bearer {ctx.token}",
            "Connection": "keep-alive",
        }

    def build_mcp_headers(self, ctx: CallContext) -> dict[str, str]:
        """构建 MCP 请求头"""
        config = self._token_manager.config()
        machine_id = generate_from_credentials(ctx.credentials, config)
        if not machine_id:
            raise RuntimeError("无法生成 machine_id，请检查凭证配置")

        kv = config.kiro_version
        os_name = config.system_version
        nv = config.node_version
        x_amz_ua = f"aws-sdk-js/1.0.27 KiroIDE-{kv}-{machine_id}"
        ua = f"aws-sdk-js/1.0.27 ua/2.1 os/{os_name} lang/js md/nodejs#{nv} api/codewhispererstreaming#1.0.27 m/E KiroIDE-{kv}-{machine_id}"

        return {
            "content-type": "application/json",
            "x-amz-user-agent": x_amz_ua,
            "user-agent": ua,
            "host": self.base_domain_for(ctx.credentials),
            "amz-sdk-invocation-id": str(uuid.uuid4()),
            "amz-sdk-request": "attempt=1; max=3",
            "Authorization": f"Bearer {ctx.token}",
            "Connection": "keep-alive",
        }

    # --- API 调用 ---

    async def call_api(self, request_body: str) -> httpx.Response:
        """发送非流式 API 请求"""
        return await self._call_api_with_retry(request_body, is_stream=False)

    async def call_api_stream(self, request_body: str) -> httpx.Response:
        """发送流式 API 请求"""
        return await self._call_api_with_retry(request_body, is_stream=True)

    async def call_mcp(self, request_body: str) -> httpx.Response:
        """发送 MCP API 请求"""
        return await self._call_mcp_with_retry(request_body)

    @staticmethod
    def retry_delay(attempt: int) -> float:
        """指数退避 + 抖动（秒）"""
        base_ms = 200
        max_ms = 2000
        exp = base_ms * (2 ** min(attempt, 6))
        backoff = min(exp, max_ms)
        jitter_max = max(backoff // 4, 1)
        jitter = random.randint(0, jitter_max)
        return (backoff + jitter) / 1000.0

    @staticmethod
    def is_monthly_request_limit(body: str) -> bool:
        """检测额度用尽"""
        if "MONTHLY_REQUEST_COUNT" in body:
            return True
        try:
            value = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            return False
        if value.get("reason") == "MONTHLY_REQUEST_COUNT":
            return True
        error = value.get("error", {})
        if isinstance(error, dict) and error.get("reason") == "MONTHLY_REQUEST_COUNT":
            return True
        return False

    async def _call_api_with_retry(self, request_body: str, is_stream: bool) -> httpx.Response:
        """带重试逻辑的 API 调用"""
        total_creds = self._token_manager.total_count()
        max_retries = min(total_creds * MAX_RETRIES_PER_CREDENTIAL, MAX_TOTAL_RETRIES)
        last_error: Optional[Exception] = None
        api_type = "流式" if is_stream else "非流式"
        model = self.extract_model_from_request(request_body)

        for attempt in range(max_retries):
            try:
                ctx = await self._token_manager.acquire_context(model)
            except Exception as e:
                last_error = e
                continue

            url = self.base_url_for(ctx.credentials)
            try:
                headers = self.build_headers(ctx)
            except Exception as e:
                last_error = e
                continue

            client = self.client_for(ctx.credentials)
            try:
                if is_stream:
                    req = client.build_request("POST", url, headers=headers, content=request_body.encode("utf-8"))
                    response = await client.send(req, stream=True)
                else:
                    response = await client.post(url, headers=headers, content=request_body.encode("utf-8"))
            except Exception as e:
                logger.warning("API 请求发送失败（尝试 %d/%d）: %s", attempt + 1, max_retries, e)
                last_error = e
                if attempt + 1 < max_retries:
                    await asyncio.sleep(self.retry_delay(attempt))
                continue

            status = response.status_code

            if 200 <= status < 300:
                self._token_manager.report_success(ctx.id, model)
                return response

            # 错误时需要读取 body（流式模式下 body 尚未读取）
            if is_stream:
                await response.aread()
                await response.aclose()
            body = response.text

            # 402 额度用尽
            if status == 402 and self.is_monthly_request_limit(body):
                logger.warning("API 请求失败（额度已用尽，尝试 %d/%d）: %d %s", attempt + 1, max_retries, status, body)
                if not self._token_manager.report_quota_exhausted(ctx.id):
                    raise RuntimeError(f"{api_type} API 请求失败（所有凭据已用尽）: {status} {body}")
                last_error = RuntimeError(f"{api_type} API 请求失败: {status} {body}")
                continue

            # 400 Bad Request
            if status == 400:
                raise RuntimeError(f"{api_type} API 请求失败: {status} {body}")

            # 401/403 凭据问题
            if status in (401, 403):
                logger.warning("API 请求失败（可能为凭据错误，尝试 %d/%d）: %d %s", attempt + 1, max_retries, status, body)
                if not self._token_manager.report_failure(ctx.id):
                    raise RuntimeError(f"{api_type} API 请求失败（所有凭据已用尽）: {status} {body}")
                last_error = RuntimeError(f"{api_type} API 请求失败: {status} {body}")
                continue

            # 瞬态错误
            if status in (408, 429) or 500 <= status < 600:
                logger.warning("API 请求失败（上游瞬态错误，尝试 %d/%d）: %d %s", attempt + 1, max_retries, status, body)
                last_error = RuntimeError(f"{api_type} API 请求失败: {status} {body}")
                if attempt + 1 < max_retries:
                    await asyncio.sleep(self.retry_delay(attempt))
                continue

            # 其他 4xx
            if 400 <= status < 500:
                raise RuntimeError(f"{api_type} API 请求失败: {status} {body}")

            # 兜底
            last_error = RuntimeError(f"{api_type} API 请求失败: {status} {body}")
            if attempt + 1 < max_retries:
                await asyncio.sleep(self.retry_delay(attempt))

        raise last_error or RuntimeError(f"{api_type} API 请求失败：已达到最大重试次数（{max_retries}次）")

    async def _call_mcp_with_retry(self, request_body: str) -> httpx.Response:
        """带重试逻辑的 MCP API 调用"""
        total_creds = self._token_manager.total_count()
        max_retries = min(total_creds * MAX_RETRIES_PER_CREDENTIAL, MAX_TOTAL_RETRIES)
        last_error: Optional[Exception] = None

        for attempt in range(max_retries):
            try:
                ctx = await self._token_manager.acquire_context(None)
            except Exception as e:
                last_error = e
                continue

            url = self.mcp_url_for(ctx.credentials)
            try:
                headers = self.build_mcp_headers(ctx)
            except Exception as e:
                last_error = e
                continue

            client = self.client_for(ctx.credentials)
            try:
                response = await client.post(url, headers=headers, content=request_body.encode("utf-8"))
            except Exception as e:
                logger.warning("MCP 请求发送失败（尝试 %d/%d）: %s", attempt + 1, max_retries, e)
                last_error = e
                if attempt + 1 < max_retries:
                    await asyncio.sleep(self.retry_delay(attempt))
                continue

            status = response.status_code

            if 200 <= status < 300:
                self._token_manager.report_success(ctx.id)
                return response

            body = response.text

            if status == 402 and self.is_monthly_request_limit(body):
                if not self._token_manager.report_quota_exhausted(ctx.id):
                    raise RuntimeError(f"MCP 请求失败（所有凭据已用尽）: {status} {body}")
                last_error = RuntimeError(f"MCP 请求失败: {status} {body}")
                continue

            if status == 400:
                raise RuntimeError(f"MCP 请求失败: {status} {body}")

            if status in (401, 403):
                if not self._token_manager.report_failure(ctx.id):
                    raise RuntimeError(f"MCP 请求失败（所有凭据已用尽）: {status} {body}")
                last_error = RuntimeError(f"MCP 请求失败: {status} {body}")
                continue

            if status in (408, 429) or 500 <= status < 600:
                logger.warning("MCP 请求失败（上游瞬态错误，尝试 %d/%d）: %d %s", attempt + 1, max_retries, status, body)
                last_error = RuntimeError(f"MCP 请求失败: {status} {body}")
                if attempt + 1 < max_retries:
                    await asyncio.sleep(self.retry_delay(attempt))
                continue

            if 400 <= status < 500:
                raise RuntimeError(f"MCP 请求失败: {status} {body}")

            last_error = RuntimeError(f"MCP 请求失败: {status} {body}")
            if attempt + 1 < max_retries:
                await asyncio.sleep(self.retry_delay(attempt))

        raise last_error or RuntimeError(f"MCP 请求失败：已达到最大重试次数（{max_retries}次）")
