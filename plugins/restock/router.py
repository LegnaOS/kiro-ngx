"""restock 插件路由"""

from fastapi import APIRouter

from plugins.restock.handlers import (
    get_restock_inventory, get_restock_orders,
    get_restock_order_detail, check_restock_ban,
    restock_login, get_restock_config, save_restock_config,
    restock_deliver, restock_batch_replace, analyze_restock_orders,
    start_auto_replace, stop_auto_replace, get_auto_replace_status,
)


def create_restock_router() -> APIRouter:
    router = APIRouter()
    router.add_api_route("/login", restock_login, methods=["POST"])
    router.add_api_route("/config", get_restock_config, methods=["GET"])
    router.add_api_route("/config", save_restock_config, methods=["PUT"])
    router.add_api_route("/inventory", get_restock_inventory, methods=["GET"])
    router.add_api_route("/orders", get_restock_orders, methods=["GET"])
    router.add_api_route("/orders/analyze", analyze_restock_orders, methods=["GET"])
    router.add_api_route("/orders/{order_id}", get_restock_order_detail, methods=["GET"])
    router.add_api_route("/orders/{order_id}/check-ban", check_restock_ban, methods=["POST"])
    router.add_api_route("/orders/{order_id}/deliver", restock_deliver, methods=["POST"])
    router.add_api_route("/orders/{order_id}/batch-replace", restock_batch_replace, methods=["POST"])
    router.add_api_route("/auto-replace/start", start_auto_replace, methods=["POST"])
    router.add_api_route("/auto-replace/stop", stop_auto_replace, methods=["POST"])
    router.add_api_route("/auto-replace/status", get_auto_replace_status, methods=["GET"])
    return router
