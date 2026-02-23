"""Admin API 路由配置 - 参考 src/admin/router.rs"""

from fastapi import APIRouter

from admin.handlers import (
    add_credential, delete_credential, get_all_credentials,
    get_credential_balance, get_load_balancing_mode, get_raw_credentials,
    get_system_stats, reset_failure_count, restart_server, save_raw_credentials,
    set_credential_disabled, set_credential_priority, set_load_balancing_mode,
)


def create_admin_router() -> APIRouter:
    """创建 Admin API 路由

    端点:
    - GET    /credentials              获取所有凭据状态
    - POST   /credentials              添加新凭据
    - DELETE  /credentials/{id}         删除凭据
    - POST   /credentials/{id}/disabled 设置凭据禁用状态
    - POST   /credentials/{id}/priority 设置凭据优先级
    - POST   /credentials/{id}/reset    重置失败计数
    - GET    /credentials/{id}/balance  获取凭据余额
    - GET    /config/load-balancing     获取负载均衡模式
    - PUT    /config/load-balancing     设置负载均衡模式
    """
    router = APIRouter()
    router.add_api_route("/credentials", get_all_credentials, methods=["GET"])
    router.add_api_route("/credentials", add_credential, methods=["POST"])
    # raw 必须在 {id} 路由之前，且用独立前缀避免被 {id} 吞掉
    router.add_api_route("/credentials-raw", get_raw_credentials, methods=["GET"])
    router.add_api_route("/credentials-raw", save_raw_credentials, methods=["PUT"])
    router.add_api_route("/credentials/{id}", delete_credential, methods=["DELETE"])
    router.add_api_route("/credentials/{id}/disabled", set_credential_disabled, methods=["POST"])
    router.add_api_route("/credentials/{id}/priority", set_credential_priority, methods=["POST"])
    router.add_api_route("/credentials/{id}/reset", reset_failure_count, methods=["POST"])
    router.add_api_route("/credentials/{id}/balance", get_credential_balance, methods=["GET"])
    router.add_api_route("/config/load-balancing", get_load_balancing_mode, methods=["GET"])
    router.add_api_route("/config/load-balancing", set_load_balancing_mode, methods=["PUT"])
    router.add_api_route("/system/stats", get_system_stats, methods=["GET"])
    router.add_api_route("/restart", restart_server, methods=["POST"])
    return router
