"""Admin API HTTP 处理器"""

import json
import logging
import os
import platform

from fastapi import Request
from fastapi.responses import JSONResponse

from admin.error import AdminServiceError
from admin.types import (
    AddCredentialRequest, SetDisabledRequest,
    SetPriorityRequest, SuccessResponse,
)

logger = logging.getLogger(__name__)


def _error_response(e: AdminServiceError) -> JSONResponse:
    return JSONResponse(status_code=e.status_code(), content=e.to_response().to_dict())


async def get_all_credentials(request: Request) -> JSONResponse:
    """GET /credentials"""
    service = request.app.state.admin_service
    response = service.get_all_credentials()
    return JSONResponse(content=response.to_dict())


async def set_credential_disabled(request: Request, id: int) -> JSONResponse:
    """POST /credentials/{id}/disabled"""
    service = request.app.state.admin_service
    body = await request.json()
    payload = SetDisabledRequest(disabled=body.get("disabled", False))
    try:
        service.set_disabled(id, payload.disabled)
    except AdminServiceError as e:
        return _error_response(e)
    action = "禁用" if payload.disabled else "启用"
    return JSONResponse(content=SuccessResponse.new(f"凭据 #{id} 已{action}").to_dict())


async def set_credential_priority(request: Request, id: int) -> JSONResponse:
    """POST /credentials/{id}/priority"""
    service = request.app.state.admin_service
    body = await request.json()
    payload = SetPriorityRequest(priority=body.get("priority", 0))
    try:
        service.set_priority(id, payload.priority)
    except AdminServiceError as e:
        return _error_response(e)
    return JSONResponse(
        content=SuccessResponse.new(f"凭据 #{id} 优先级已设置为 {payload.priority}").to_dict()
    )


async def reset_failure_count(request: Request, id: int) -> JSONResponse:
    """POST /credentials/{id}/reset"""
    service = request.app.state.admin_service
    try:
        service.reset_and_enable(id)
    except AdminServiceError as e:
        return _error_response(e)
    return JSONResponse(
        content=SuccessResponse.new(f"凭据 #{id} 失败计数已重置并重新启用").to_dict()
    )


async def get_credential_balance(request: Request, id: int) -> JSONResponse:
    """GET /credentials/{id}/balance"""
    service = request.app.state.admin_service
    try:
        response = await service.get_balance(id)
    except AdminServiceError as e:
        return _error_response(e)
    return JSONResponse(content=response.to_dict())


async def add_credential(request: Request) -> JSONResponse:
    """POST /credentials"""
    service = request.app.state.admin_service
    body = await request.json()
    payload = AddCredentialRequest.from_dict(body)
    try:
        response = await service.add_credential(payload)
    except AdminServiceError as e:
        return _error_response(e)
    return JSONResponse(content=response.to_dict())


async def delete_credential(request: Request, id: int) -> JSONResponse:
    """DELETE /credentials/{id}"""
    service = request.app.state.admin_service
    try:
        service.delete_credential(id)
    except AdminServiceError as e:
        return _error_response(e)
    return JSONResponse(content=SuccessResponse.new(f"凭据 #{id} 已删除").to_dict())


async def get_raw_credentials(request: Request) -> JSONResponse:
    """GET /credentials/raw - 读取 credentials.json 原始内容"""
    service = request.app.state.admin_service
    cred_path = service.token_manager.credentials_path
    if not cred_path:
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": "未配置凭据文件路径"},
        )

    try:
        content = cred_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        content = "[]"
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": f"读取文件失败: {e}"},
        )

    return JSONResponse(content={"content": content})


async def save_raw_credentials(request: Request) -> JSONResponse:
    """PUT /credentials/raw - 写入 credentials.json"""
    body = await request.json()
    content = body.get("content")
    if not isinstance(content, str):
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "content 必须是字符串"},
        )

    # 验证是合法 JSON 数组
    try:
        parsed = json.loads(content)
        if not isinstance(parsed, list):
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "内容必须是 JSON 数组"},
            )
    except json.JSONDecodeError as e:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": f"JSON 格式错误: {e}"},
        )

    service = request.app.state.admin_service
    cred_path = service.token_manager.credentials_path
    if not cred_path:
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": "未配置凭据文件路径"},
        )

    try:
        cred_path.write_text(content, encoding="utf-8")
        logger.info("凭据文件已更新 %s（%d 条）", cred_path, len(parsed))
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": f"写入文件失败: {e}"},
        )

    return JSONResponse(content={
        "success": True,
        "message": f"已保存 {len(parsed)} 个凭据，请重启服务生效",
    })


async def set_credential_group(request: Request, id: int) -> JSONResponse:
    """POST /credentials/{id}/group - 设置凭据分组"""
    service = request.app.state.admin_service
    body = await request.json()
    group = body.get("group", "pro")
    try:
        service.set_credential_group(id, group)
    except AdminServiceError as e:
        return _error_response(e)
    return JSONResponse(content=SuccessResponse.new(f"凭据 #{id} 分组已设置为 {group}").to_dict())


async def set_credential_groups_batch(request: Request) -> JSONResponse:
    """PUT /credentials/groups - 批量设置凭据分组"""
    service = request.app.state.admin_service
    body = await request.json()
    groups = body.get("groups", {})
    # 转换 key 为 int
    int_groups = {int(k): v for k, v in groups.items()}
    try:
        service.set_credential_groups_batch(int_groups)
    except AdminServiceError as e:
        return _error_response(e)
    return JSONResponse(content=SuccessResponse.new(f"已更新 {len(int_groups)} 个凭据分组").to_dict())


def _get_process_memory_mb() -> float:
    """获取当前进程内存占用（MB），跨平台"""
    pid = os.getpid()
    try:
        if platform.system() == "Windows":
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            psapi = ctypes.windll.psapi
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x0400 | 0x0010, False, pid)
            if handle:
                counters = PROCESS_MEMORY_COUNTERS()
                counters.cb = ctypes.sizeof(counters)
                if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    kernel32.CloseHandle(handle)
                    return counters.WorkingSetSize / (1024 * 1024)
                kernel32.CloseHandle(handle)
        else:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) / 1024
    except Exception:
        pass
    return 0.0


def _get_cpu_percent() -> float:
    """获取系统 CPU 使用率，跨平台"""
    try:
        if platform.system() == "Windows":
            import ctypes

            class FILETIME(ctypes.Structure):
                _fields_ = [("dwLowDateTime", ctypes.c_uint), ("dwHighDateTime", ctypes.c_uint)]

            kernel32 = ctypes.windll.kernel32
            idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
            kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))

            def ft_to_int(ft):
                return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

            idle1, kernel1, user1 = ft_to_int(idle), ft_to_int(kernel), ft_to_int(user)

            import time
            time.sleep(0.1)

            kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
            idle2, kernel2, user2 = ft_to_int(idle), ft_to_int(kernel), ft_to_int(user)

            idle_d = idle2 - idle1
            total_d = (kernel2 - kernel1) + (user2 - user1)
            if total_d == 0:
                return 0.0
            return round((1.0 - idle_d / total_d) * 100, 1)
        else:
            def read_cpu():
                with open("/proc/stat") as f:
                    parts = f.readline().split()
                vals = [int(x) for x in parts[1:]]
                idle_val = vals[3] + (vals[4] if len(vals) > 4 else 0)
                total_val = sum(vals)
                return idle_val, total_val

            idle1, total1 = read_cpu()
            import time
            time.sleep(0.1)
            idle2, total2 = read_cpu()

            idle_d = idle2 - idle1
            total_d = total2 - total1
            if total_d == 0:
                return 0.0
            return round((1.0 - idle_d / total_d) * 100, 1)
    except Exception:
        return 0.0


async def get_system_stats(request: Request) -> JSONResponse:
    """GET /system/stats - 系统资源监控"""
    import asyncio
    loop = asyncio.get_event_loop()
    cpu = await loop.run_in_executor(None, _get_cpu_percent)
    mem = _get_process_memory_mb()
    return JSONResponse(content={
        "cpuPercent": cpu,
        "memoryMb": round(mem, 1),
    })


def _read_version(source: str = "local") -> str:
    """读取版本号，local 读本地文件，remote 读远程 VERSION"""
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent

    if source == "local":
        try:
            return (root / "VERSION").read_text(encoding="utf-8").strip()
        except Exception:
            return "unknown"
    else:
        try:
            r = subprocess.run(
                ["git", "show", "origin/master:VERSION"],
                cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            )
            if r.returncode == 0:
                return r.stdout.strip()
        except Exception:
            pass
        return "unknown"


async def get_version_info(request: Request) -> JSONResponse:
    """GET /version - 获取当前版本、远程版本、commit 差异"""
    import asyncio
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    loop = asyncio.get_event_loop()

    def _fetch():
        current = _read_version("local")

        # fetch 远程
        try:
            subprocess.run(
                ["git", "fetch", "origin", "--quiet"],
                cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
            )
        except Exception:
            pass

        latest = _read_version("remote")
        version_changed = current != "unknown" and latest != "unknown" and current != latest

        # 检查 commit 差异（即使 VERSION 没变也能发现新提交）
        behind_count = 0
        try:
            r = subprocess.run(
                ["git", "rev-list", "--count", "HEAD..origin/master"],
                cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            )
            if r.returncode == 0:
                behind_count = int(r.stdout.strip())
        except Exception:
            pass

        has_update = version_changed or behind_count > 0

        return {
            "current": current,
            "latest": latest,
            "hasUpdate": has_update,
            "behindCount": behind_count,
        }

    result = await loop.run_in_executor(None, _fetch)
    return JSONResponse(content=result)


async def get_git_status(request: Request) -> JSONResponse:
    """GET /git/status - 检测本地是否有未提交改动"""
    import asyncio
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    loop = asyncio.get_event_loop()

    def _check():
        has_changes = False
        changed_files: list[str] = []
        try:
            r = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                has_changes = True
                changed_files = [line.strip() for line in r.stdout.strip().splitlines()[:20]]
        except Exception:
            pass
        return {"hasLocalChanges": has_changes, "changedFiles": changed_files}

    result = await loop.run_in_executor(None, _check)
    return JSONResponse(content=result)


async def get_git_log(request: Request) -> JSONResponse:
    """GET /git/log - 获取远程 commit 列表（含当前 HEAD 标记）"""
    import asyncio
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    loop = asyncio.get_event_loop()

    def _fetch():
        run_kw = dict(cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace")

        # fetch 远程
        try:
            subprocess.run(["git", "fetch", "origin", "--quiet"], timeout=15, **run_kw)
        except Exception:
            pass

        # 当前 HEAD commit hash
        current_hash = ""
        try:
            r = subprocess.run(["git", "rev-parse", "HEAD"], timeout=5, **run_kw)
            if r.returncode == 0:
                current_hash = r.stdout.strip()
        except Exception:
            pass

        # 远程 commit 列表（最近 30 条）
        commits: list[dict] = []
        try:
            r = subprocess.run(
                ["git", "log", "origin/master", "--pretty=format:%H|%h|%s|%ai", "-30"],
                timeout=10, **run_kw,
            )
            if r.returncode == 0 and r.stdout.strip():
                for line in r.stdout.strip().splitlines():
                    parts = line.split("|", 3)
                    if len(parts) == 4:
                        commits.append({
                            "hash": parts[0],
                            "short": parts[1],
                            "message": parts[2],
                            "date": parts[3],
                            "isCurrent": parts[0] == current_hash,
                        })
        except Exception:
            pass

        return {"currentHash": current_hash, "commits": commits}

    result = await loop.run_in_executor(None, _fetch)
    return JSONResponse(content=result)


async def restart_server(request: Request) -> JSONResponse:
    """POST /restart - 重启服务（Windows 下先重新编译前端）"""
    import asyncio
    import subprocess
    import sys
    from pathlib import Path

    project_root = Path(__file__).resolve().parent.parent

    async def _do_restart():
        loop = asyncio.get_event_loop()

        # Windows 开发环境：重启前重新编译前端
        if platform.system() == "Windows" and _has_command("npm"):
            admin_ui = project_root / "admin-ui"
            if (admin_ui / "package.json").exists():
                logger.info("[restart] npm run build ...")
                r = await loop.run_in_executor(
                    None, lambda: _npm_run(["npm", "run", "build"], str(admin_ui)),
                )
                if r.returncode == 0:
                    logger.info("[restart] npm build 完成")
                else:
                    logger.warning("[restart] npm build 失败 (rc=%d): %s", r.returncode, r.stderr[:500])

        await asyncio.sleep(0.5)
        if platform.system() == "Windows":
            subprocess.Popen([sys.executable] + sys.argv, close_fds=True)
            os._exit(0)
        else:
            os.execv(sys.executable, [sys.executable] + sys.argv)

    asyncio.ensure_future(_do_restart())
    return JSONResponse(content={"success": True, "message": "正在重启..."})


_update_log: list[str] = []
"""更新过程实时日志，供前端轮询"""


def _append_update_log(msg: str):
    _update_log.append(msg)
    logger.info("[update] %s", msg)


def _find_venv_pip() -> str | None:
    """查找 venv 中的 pip，兼容 Linux/Windows"""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    candidates = [
        root / "venv" / "bin" / "pip",
        root / "venv" / "Scripts" / "pip.exe",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def _has_command(name: str) -> bool:
    import shutil
    return shutil.which(name) is not None


def _npm_run(args: list[str], cwd: str, timeout: int = 120) -> "subprocess.CompletedProcess":
    """跨平台执行 npm 命令（Windows 需要 shell=True 才能找到 npm.cmd）"""
    import subprocess
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
        shell=(platform.system() == "Windows"),
    )


async def get_update_status(request: Request) -> JSONResponse:
    """GET /update/status - 获取更新进度日志"""
    return JSONResponse(content={"log": list(_update_log)})


async def update_and_restart(request: Request) -> JSONResponse:
    """POST /update - stash → pull/checkout → npm(可选) → pip → 重启"""
    import asyncio
    import subprocess
    import sys
    from pathlib import Path

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    target_commit = body.get("targetCommit")

    project_root = Path(__file__).resolve().parent.parent
    _update_log.clear()

    async def _do_update():
        loop = asyncio.get_event_loop()

        def _run():
            run_kw = dict(cwd=str(project_root), capture_output=True, text=True, encoding="utf-8", errors="replace")

            # stash 本地改动
            _append_update_log("git stash --include-untracked ...")
            subprocess.run(["git", "stash", "--include-untracked"], timeout=30, **run_kw)

            if target_commit:
                # 切换到指定 commit
                _append_update_log(f"git checkout {target_commit[:8]} ...")
                r = subprocess.run(["git", "checkout", target_commit], timeout=30, **run_kw)
                if r.returncode != 0:
                    _append_update_log(f"git checkout 失败: {r.stderr.strip()}")
                    return False
                _append_update_log("git checkout 完成")
            else:
                # git pull --ff-only，失败则 fallback
                _append_update_log("git pull --ff-only ...")
                r = subprocess.run(["git", "pull", "--ff-only"], timeout=60, **run_kw)
                if r.returncode != 0:
                    _append_update_log(f"ff-only 失败 (rc={r.returncode})，尝试 git pull --no-edit ...")
                    r = subprocess.run(["git", "pull", "--no-edit"], timeout=60, **run_kw)
                    if r.returncode != 0:
                        _append_update_log(f"git pull 失败: {r.stderr.strip()}")
                        return False
                _append_update_log("git pull 完成")

            # stash pop（忽略错误）
            subprocess.run(["git", "stash", "pop"], timeout=15, **run_kw)

            # npm build（可选）
            admin_ui = project_root / "admin-ui"
            if _has_command("npm") and (admin_ui / "package.json").exists():
                _append_update_log("npm install ...")
                _npm_run(["npm", "install", "--silent"], str(admin_ui))
                _append_update_log("npm run build ...")
                r = _npm_run(["npm", "run", "build"], str(admin_ui))
                if r.returncode != 0:
                    _append_update_log(f"npm build 失败 (rc={r.returncode})，使用已有 dist")
            else:
                _append_update_log("npm 不可用，跳过前端构建")

            # pip install（可选）
            venv_pip = _find_venv_pip()
            if venv_pip:
                _append_update_log("pip install -r requirements.txt ...")
                subprocess.run(
                    [venv_pip, "install", "-q", "-r", "requirements.txt"],
                    timeout=60, **run_kw,
                )
                _append_update_log("pip install 完成")

            return True

        success = await loop.run_in_executor(None, _run)
        if not success:
            _append_update_log("更新失败，已中止")
            return

        _append_update_log("更新完成，正在重启...")
        await asyncio.sleep(0.5)
        if platform.system() == "Windows":
            subprocess.Popen([sys.executable] + sys.argv, close_fds=True)
            os._exit(0)
        else:
            os.execv(sys.executable, [sys.executable] + sys.argv)

    asyncio.ensure_future(_do_update())
    return JSONResponse(content={"success": True, "message": "正在更新并重启..."})


# ============ 统计 & 路由 ============

async def get_request_stats(request: Request) -> JSONResponse:
    """GET /stats - 获取请求统计数据"""
    service = request.app.state.admin_service
    stats = service.get_stats()
    return JSONResponse(content=stats)


async def get_model_list(request: Request) -> JSONResponse:
    """GET /models - 获取支持的模型列表"""
    from anthropic_api.handlers import MODELS
    models = [{"id": m.id, "displayName": m.display_name} for m in MODELS]
    return JSONResponse(content={"models": models})


async def get_routing_config(request: Request) -> JSONResponse:
    """GET /routing - 获取路由配置"""
    service = request.app.state.admin_service
    free_models = service.get_free_models()
    return JSONResponse(content={"freeModels": free_models})


async def set_routing_config(request: Request) -> JSONResponse:
    """PUT /routing - 更新路由配置"""
    service = request.app.state.admin_service
    body = await request.json()
    free_models = body.get("freeModels", [])
    if not isinstance(free_models, list):
        return JSONResponse(status_code=400, content={"success": False, "message": "freeModels 必须是数组"})
    service.set_free_models(free_models)
    return JSONResponse(content=SuccessResponse.new(f"路由配置已更新（{len(free_models)} 个免费模型）").to_dict())


async def get_log_status(request: Request) -> JSONResponse:
    """GET /log - 获取日志开关状态"""
    from anthropic_api.message_log import get_message_logger
    ml = get_message_logger()
    return JSONResponse(content={"enabled": ml.enabled if ml else False})


async def set_log_status(request: Request) -> JSONResponse:
    """PUT /log - 设置日志开关"""
    from anthropic_api.message_log import get_message_logger
    body = await request.json()
    enabled = body.get("enabled", False)
    ml = get_message_logger()
    if not ml:
        return JSONResponse(status_code=500, content={"success": False, "message": "日志模块未初始化"})
    ml.set_enabled(enabled)
    return JSONResponse(content=SuccessResponse.new(f"消息日志已{'开启' if enabled else '关闭'}").to_dict())