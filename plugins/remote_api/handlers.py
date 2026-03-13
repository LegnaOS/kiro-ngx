"""远程 API 插件 handlers。"""

import json
import threading
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers

from admin.error import AdminServiceError
from admin.handlers import _error_response, restart_server
from admin.types import AddCredentialRequest, BatchImportRequest
from common.auth import extract_api_key, sha256_hex

_DEFAULT_ENABLED_APIS = {
    "availableCredentials": True,
    "batchImport": True,
    "restart": False,
    "refreshQuota": True,
    "totalRemainingQuota": True,
    "todayTokenTotal": True,
    "totalCalls": True,
}


def build_remote_api_config_path(admin_service) -> Path:
    """构建远程 API 插件配置文件路径。"""
    cache_dir = None
    try:
        cache_dir_getter = getattr(admin_service.token_manager, "cache_dir", None)
        if callable(cache_dir_getter):
            cache_dir = cache_dir_getter()
    except Exception:
        cache_dir = None

    if cache_dir:
        return Path(cache_dir) / "remote_api_plugin.json"
    return Path(__file__).resolve().parent / "config.json"


class RemoteApiConfigStore:
    """远程 API 插件开关配置存储。"""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._config = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"enabledApis": dict(_DEFAULT_ENABLED_APIS)}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"enabledApis": dict(_DEFAULT_ENABLED_APIS)}

        raw = data.get("enabledApis", {})
        enabled = dict(_DEFAULT_ENABLED_APIS)
        if isinstance(raw, dict):
            for key in enabled.keys():
                if key in raw:
                    enabled[key] = bool(raw[key])
        return {"enabledApis": enabled}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _reload_unlocked(self):
        self._config = self._load()

    def get_config(self) -> dict:
        with self._lock:
            self._reload_unlocked()
            return {"enabledApis": dict(self._config["enabledApis"])}

    def update_config(self, payload: dict) -> dict:
        enabled_payload = payload.get("enabledApis", {})
        if not isinstance(enabled_payload, dict):
            raise ValueError("enabledApis 必须是对象")

        with self._lock:
            current = self._config["enabledApis"]
            for key in _DEFAULT_ENABLED_APIS.keys():
                if key in enabled_payload:
                    current[key] = bool(enabled_payload[key])
            self._save()
            return {"enabledApis": dict(current)}

    def is_enabled(self, api_name: str) -> bool:
        with self._lock:
            self._reload_unlocked()
            return bool(self._config["enabledApis"].get(api_name, False))


class RemotePluginAuthMiddleware:
    """远程 API 插件鉴权中间件（token 为 adminApiKey 的 SHA-256）。"""

    def __init__(self, app, admin_api_key: str):
        self.app = app
        self.expected = sha256_hex(admin_api_key)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        token = extract_api_key(Headers(scope=scope))
        if token == self.expected:
            await self.app(scope, receive, send)
            return

        response = JSONResponse(
            status_code=401,
            content={"error": {"type": "authentication_error", "message": "Invalid or missing admin API key"}},
        )
        await response(scope, receive, send)


def _config_store_from_request(request: Request) -> RemoteApiConfigStore:
    store = getattr(request.app.state, "remote_api_config_store", None)
    if store:
        return store
    admin_service = request.app.state.admin_service
    store = RemoteApiConfigStore(build_remote_api_config_path(admin_service))
    request.app.state.remote_api_config_store = store
    return store


def _require_enabled(request: Request, api_name: str) -> JSONResponse | None:
    store = _config_store_from_request(request)
    if store.is_enabled(api_name):
        return None
    return JSONResponse(
        status_code=403,
        content={
            "error": {
                "type": "forbidden",
                "message": f"API 已被禁用: {api_name}",
            }
        },
    )


def _parse_regions(value) -> list[str]:
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return ["eu-north-1", "us-east-1"]


def _parse_batch_import_request(body) -> BatchImportRequest:
    if isinstance(body, list):
        return BatchImportRequest(
            credentials=[AddCredentialRequest.from_dict(item or {}) for item in body],
        )

    if not isinstance(body, dict):
        return BatchImportRequest()

    if "credentials" in body:
        raw_credentials = body.get("credentials") or []
        if not isinstance(raw_credentials, list):
            raw_credentials = [raw_credentials]
        return BatchImportRequest(
            credentials=[AddCredentialRequest.from_dict(item or {}) for item in raw_credentials],
            skip_verify=bool(body.get("skipVerify", True)),
            regions=_parse_regions(body.get("regions")),
        )

    if "refreshToken" in body:
        return BatchImportRequest(
            credentials=[AddCredentialRequest.from_dict(body)],
            skip_verify=bool(body.get("skipVerify", True)),
            regions=_parse_regions(body.get("regions")),
        )

    return BatchImportRequest(
        skip_verify=bool(body.get("skipVerify", True)),
        regions=_parse_regions(body.get("regions")),
    )


async def get_remote_api_config(request: Request) -> JSONResponse:
    """GET /config - 获取远程 API 插件配置。"""
    store = _config_store_from_request(request)
    admin_api_key = request.headers.get("x-plugin-admin-key", "")
    token_hint = sha256_hex(admin_api_key) if admin_api_key else None
    config = store.get_config()
    if token_hint:
        config["tokenPreview"] = token_hint
    return JSONResponse(content=config)


async def update_remote_api_config(request: Request) -> JSONResponse:
    """PUT /config - 更新远程 API 开关配置。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": {"type": "invalid_request", "message": "无效的请求体"}},
        )

    store = _config_store_from_request(request)
    try:
        updated = store.update_config(body if isinstance(body, dict) else {})
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={"error": {"type": "invalid_request", "message": str(e)}},
        )
    return JSONResponse(content=updated)


async def get_available_credentials(request: Request) -> JSONResponse:
    gate = _require_enabled(request, "availableCredentials")
    if gate:
        return gate
    service = request.app.state.admin_service
    return JSONResponse(content=service.get_available_credential_counts())


async def batch_import_credentials(request: Request) -> JSONResponse:
    gate = _require_enabled(request, "batchImport")
    if gate:
        return gate

    try:
        body = await request.json()
    except Exception:
        body = {}

    payload = _parse_batch_import_request(body)
    service = request.app.state.admin_service
    try:
        response = await service.batch_import_credentials(payload)
    except AdminServiceError as e:
        return _error_response(e)
    return JSONResponse(content=response.to_dict())


async def remote_restart_server(request: Request) -> JSONResponse:
    gate = _require_enabled(request, "restart")
    if gate:
        return gate
    return await restart_server(request)


async def refresh_quota(request: Request) -> JSONResponse:
    gate = _require_enabled(request, "refreshQuota")
    if gate:
        return gate
    service = request.app.state.admin_service
    data = await service.get_total_remaining_quota(force_refresh=True)
    return JSONResponse(content=data)


async def get_total_remaining_quota(request: Request) -> JSONResponse:
    gate = _require_enabled(request, "totalRemainingQuota")
    if gate:
        return gate
    service = request.app.state.admin_service
    data = await service.get_total_remaining_quota(force_refresh=False)
    return JSONResponse(content=data)


async def get_today_token_total(request: Request) -> JSONResponse:
    gate = _require_enabled(request, "todayTokenTotal")
    if gate:
        return gate
    service = request.app.state.admin_service
    stats = service.get_stats()
    token_usage = stats.get("tokenUsage", {})
    today = token_usage.get("today", {}) if isinstance(token_usage, dict) else {}
    in_tokens = int(today.get("input", 0) or 0)
    out_tokens = int(today.get("output", 0) or 0)
    return JSONResponse(content={
        "input": in_tokens,
        "output": out_tokens,
        "total": in_tokens + out_tokens,
    })


async def get_total_calls(request: Request) -> JSONResponse:
    gate = _require_enabled(request, "totalCalls")
    if gate:
        return gate
    service = request.app.state.admin_service
    stats = service.get_stats()
    return JSONResponse(content={
        "totalRequests": stats.get("totalRequests", 0),
        "sessionRequests": stats.get("sessionRequests", 0),
        "rpm": stats.get("rpm", 0),
        "peakRpm": stats.get("peakRpm", 0),
    })
