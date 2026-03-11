"""Admin API 路由配置 - 参考 src/admin/router.rs"""

from fastapi import APIRouter

from admin.handlers import (
    add_credential, delete_credential, get_all_credentials,
    get_credential_balance, get_git_log, get_git_status,
    get_log_status, get_model_list, get_raw_credentials,
    get_request_stats, get_routing_config, get_runtime_logs, get_system_stats,
    get_update_status, get_version_info, reset_all_counters,
    reset_failure_count, restart_server, save_raw_credentials,
    set_credential_disabled, set_credential_group,
    set_credential_groups_batch, set_credential_priority,
    set_log_status, set_routing_config, update_and_restart,
)


def create_admin_router() -> APIRouter:
    router = APIRouter()
    router.add_api_route("/credentials", get_all_credentials, methods=["GET"])
    router.add_api_route("/credentials", add_credential, methods=["POST"])
    # 固定路径必须在 {id} 路由之前
    router.add_api_route("/credentials-raw", get_raw_credentials, methods=["GET"])
    router.add_api_route("/credentials-raw", save_raw_credentials, methods=["PUT"])
    router.add_api_route("/credentials/groups", set_credential_groups_batch, methods=["PUT"])
    router.add_api_route("/credentials/reset-all", reset_all_counters, methods=["POST"])
    router.add_api_route("/credentials/{id}", delete_credential, methods=["DELETE"])
    router.add_api_route("/credentials/{id}/disabled", set_credential_disabled, methods=["POST"])
    router.add_api_route("/credentials/{id}/priority", set_credential_priority, methods=["POST"])
    router.add_api_route("/credentials/{id}/reset", reset_failure_count, methods=["POST"])
    router.add_api_route("/credentials/{id}/balance", get_credential_balance, methods=["GET"])
    router.add_api_route("/credentials/{id}/group", set_credential_group, methods=["POST"])
    router.add_api_route("/stats", get_request_stats, methods=["GET"])
    router.add_api_route("/models", get_model_list, methods=["GET"])
    router.add_api_route("/routing", get_routing_config, methods=["GET"])
    router.add_api_route("/routing", set_routing_config, methods=["PUT"])
    router.add_api_route("/log", get_log_status, methods=["GET"])
    router.add_api_route("/log", set_log_status, methods=["PUT"])
    router.add_api_route("/logs/runtime", get_runtime_logs, methods=["GET"])
    router.add_api_route("/system/stats", get_system_stats, methods=["GET"])
    router.add_api_route("/version", get_version_info, methods=["GET"])
    router.add_api_route("/restart", restart_server, methods=["POST"])
    router.add_api_route("/update", update_and_restart, methods=["POST"])
    router.add_api_route("/update/status", get_update_status, methods=["GET"])
    router.add_api_route("/git/status", get_git_status, methods=["GET"])
    router.add_api_route("/git/log", get_git_log, methods=["GET"])
    return router
