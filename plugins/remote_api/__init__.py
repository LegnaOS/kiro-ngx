"""远程 API 插件。"""

from fastapi import FastAPI

from plugins.remote_api.handlers import (
    RemoteApiConfigStore,
    RemotePluginAuthMiddleware,
    build_remote_api_config_path,
)
from plugins.remote_api.router import create_remote_admin_router, create_remote_public_router

PLUGIN_MANIFEST = {
    "id": "remote-api",
    "name": "远程 API",
    "description": "提供可控开关的远程调用接口（凭据、统计、重启等）",
    "version": "1.0.0",
    "icon": "Cloud",
    "has_frontend": True,
    "api_prefix": "/plugins/remote-api",
    "public_mount": "/api/remote",
}


def create_router():
    return create_remote_admin_router()


def create_public_app(*, admin_service, admin_api_key: str) -> FastAPI:
    app = FastAPI()
    config_store = RemoteApiConfigStore(
        build_remote_api_config_path(admin_service),
    )
    app.state.admin_service = admin_service
    app.state.remote_api_config_store = config_store
    app.include_router(create_remote_public_router())
    app.add_middleware(
        RemotePluginAuthMiddleware,
        admin_api_key=admin_api_key,
    )
    return app

