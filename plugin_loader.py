"""插件加载器 — 启动时扫描 plugins/ 目录，自动注册插件路由"""

import importlib
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# 插件 manifest 必须包含的字段
REQUIRED_FIELDS = ("id", "name", "version")


def _iter_plugin_dirs() -> list[Path]:
    plugins_dir = Path(__file__).resolve().parent / "plugins"
    if not plugins_dir.is_dir():
        return []
    result: list[Path] = []
    for entry in sorted(plugins_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        # 仅加载标准 Python 包，避免把运行时目录误判为插件
        if not (entry / "__init__.py").exists():
            continue
        result.append(entry)
    return result


def load_plugins(admin_app: FastAPI) -> list[dict]:
    """扫描 plugins/ 子目录，加载含 PLUGIN_MANIFEST 的包，返回 manifest 列表"""
    loaded: list[dict] = []

    for entry in _iter_plugin_dirs():
        try:
            mod = importlib.import_module(f"plugins.{entry.name}")
            manifest: dict | None = getattr(mod, "PLUGIN_MANIFEST", None)
            if not manifest:
                logger.warning("插件 %s 缺少 PLUGIN_MANIFEST，跳过", entry.name)
                continue

            missing = [f for f in REQUIRED_FIELDS if f not in manifest]
            if missing:
                logger.warning("插件 %s manifest 缺少字段 %s，跳过", entry.name, missing)
                continue

            create_router = getattr(mod, "create_router", None)
            if create_router:
                router = create_router()
                prefix = manifest.get("api_prefix", f"/plugins/{entry.name}")
                admin_app.include_router(router, prefix=prefix)

            loaded.append(manifest)
            logger.info(
                "已加载插件: %s v%s (%s)",
                manifest["name"],
                manifest["version"],
                manifest.get("api_prefix", f"/plugins/{entry.name}"),
            )
        except Exception as e:
            logger.error("加载插件 %s 失败: %s", entry.name, e)

    return loaded


async def get_loaded_plugins(request: Request) -> JSONResponse:
    """GET /plugins — 返回已加载的插件列表"""
    plugins = getattr(request.app.state, "loaded_plugins", [])
    return JSONResponse(content={"plugins": plugins})


def load_public_plugins(app: FastAPI, *, admin_service: Any, admin_api_key: str) -> list[dict]:
    """扫描 plugins/ 子目录，加载包含 create_public_app 的插件并挂载到主应用。"""
    loaded: list[dict] = []

    for entry in _iter_plugin_dirs():
        try:
            mod = importlib.import_module(f"plugins.{entry.name}")
            create_public_app = getattr(mod, "create_public_app", None)
            if not callable(create_public_app):
                continue

            manifest: dict | None = getattr(mod, "PLUGIN_MANIFEST", None)
            mount_path = None
            if isinstance(manifest, dict):
                mount_path = manifest.get("public_mount")
            mount_path = mount_path or f"/api/plugins/{entry.name}"

            public_app = create_public_app(
                admin_service=admin_service,
                admin_api_key=admin_api_key,
            )
            app.mount(mount_path, public_app)
            loaded.append({
                "id": manifest.get("id", entry.name) if isinstance(manifest, dict) else entry.name,
                "mountPath": mount_path,
            })
            logger.info("已加载公共插件路由: %s -> %s", entry.name, mount_path)
        except Exception as e:
            logger.error("加载公共插件 %s 失败: %s", entry.name, e)

    return loaded

