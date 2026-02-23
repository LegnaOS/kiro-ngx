"""Admin UI 静态文件服务 + SPA fallback"""

import mimetypes
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import Response

# admin-ui 构建产物目录
ADMIN_UI_DIST = Path(__file__).resolve().parent.parent / "admin-ui" / "dist"


def create_admin_ui_router() -> APIRouter:
    router = APIRouter()
    router.add_api_route("/", _index_handler, methods=["GET"])
    router.add_api_route("/{file_path:path}", _static_handler, methods=["GET"])
    return router


async def _index_handler() -> Response:
    return _serve_index()


async def _static_handler(file_path: str) -> Response:
    if ".." in file_path:
        return Response(content="Invalid path", status_code=400)

    target = ADMIN_UI_DIST / file_path
    if target.is_file():
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        cache_control = _get_cache_control(file_path)
        return Response(
            content=target.read_bytes(),
            media_type=content_type,
            headers={"Cache-Control": cache_control},
        )

    if not _is_asset_path(file_path):
        return _serve_index()

    return Response(content="Not found", status_code=404)


def _serve_index() -> Response:
    index = ADMIN_UI_DIST / "index.html"
    if index.is_file():
        return Response(
            content=index.read_bytes(),
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-cache"},
        )
    return Response(
        content="Admin UI not built. Run 'npm run build' in admin-ui directory.",
        status_code=404,
    )


def _get_cache_control(path: str) -> str:
    if path.endswith(".html"):
        return "no-cache"
    if path.startswith("assets/"):
        return "public, max-age=31536000, immutable"
    return "public, max-age=3600"


def _is_asset_path(path: str) -> bool:
    last_segment = path.rsplit("/", 1)[-1]
    return "." in last_segment
