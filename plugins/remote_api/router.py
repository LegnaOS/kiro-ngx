"""远程 API 插件路由。"""

from fastapi import APIRouter

from plugins.remote_api.handlers import (
    batch_import_credentials,
    get_available_credentials,
    get_remote_api_config,
    get_today_token_total,
    get_total_calls,
    get_total_remaining_quota,
    refresh_quota,
    remote_restart_server,
    update_remote_api_config,
)


def create_remote_admin_router() -> APIRouter:
    """插件管理页使用的 admin 路由。"""
    router = APIRouter()
    router.add_api_route("/config", get_remote_api_config, methods=["GET"])
    router.add_api_route("/config", update_remote_api_config, methods=["PUT"])
    return router


def create_remote_public_router() -> APIRouter:
    """对外远程调用路由。"""
    router = APIRouter()
    router.add_api_route("/credentials/available", get_available_credentials, methods=["GET"])
    router.add_api_route("/credentials/batch-import", batch_import_credentials, methods=["POST"])
    router.add_api_route("/server/restart", remote_restart_server, methods=["POST"])
    router.add_api_route("/quota/refresh", refresh_quota, methods=["POST"])
    router.add_api_route("/quota/total-remaining", get_total_remaining_quota, methods=["GET"])
    router.add_api_route("/stats/today-tokens", get_today_token_total, methods=["GET"])
    router.add_api_route("/stats/total-calls", get_total_calls, methods=["GET"])
    return router

