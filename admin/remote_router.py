"""Remote API 路由配置。"""

from fastapi import APIRouter

from admin.remote_handlers import (
    batch_import_credentials,
    get_available_credentials,
)


def create_remote_router() -> APIRouter:
    router = APIRouter()
    router.add_api_route("/credentials/available", get_available_credentials, methods=["GET"])
    router.add_api_route("/credentials/batch-import", batch_import_credentials, methods=["POST"])
    return router
