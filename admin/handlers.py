"""Admin API HTTP 处理器"""

import json
import logging
import os
import platform

from fastapi import Request
from fastapi.responses import JSONResponse

from admin.error import AdminServiceError
from admin.types import (
    AddCredentialRequest, SetDisabledRequest, SetLoadBalancingModeRequest,
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


async def get_load_balancing_mode(request: Request) -> JSONResponse:
    """GET /config/load-balancing"""
    service = request.app.state.admin_service
    response = service.get_load_balancing_mode()
    return JSONResponse(content=response.to_dict())


async def set_load_balancing_mode(request: Request) -> JSONResponse:
    """PUT /config/load-balancing"""
    service = request.app.state.admin_service
    body = await request.json()
    payload = SetLoadBalancingModeRequest.from_dict(body)
    try:
        response = service.set_load_balancing_mode(payload)
    except AdminServiceError as e:
        return _error_response(e)
    return JSONResponse(content=response.to_dict())


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
                cwd=str(root), capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                return r.stdout.strip()
        except Exception:
            pass
        return "unknown"


async def get_version_info(request: Request) -> JSONResponse:
    """GET /version - 获取当前版本和远程最新版本"""
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
                cwd=str(root), capture_output=True, text=True, timeout=15,
            )
        except Exception:
            pass

        latest = _read_version("remote")
        has_update = current != "unknown" and latest != "unknown" and current != latest

        return {
            "current": current,
            "latest": latest,
            "hasUpdate": has_update,
        }

    result = await loop.run_in_executor(None, _fetch)
    return JSONResponse(content=result)


async def restart_server(request: Request) -> JSONResponse:
    """POST /restart - 重启服务"""
    import asyncio
    import subprocess
    import sys

    async def _do_restart():
        await asyncio.sleep(0.5)
        if platform.system() == "Windows":
            subprocess.Popen([sys.executable] + sys.argv, close_fds=True)
            os._exit(0)
        else:
            os.execv(sys.executable, [sys.executable] + sys.argv)

    asyncio.ensure_future(_do_restart())
    return JSONResponse(content={"success": True, "message": "正在重启..."})


async def update_and_restart(request: Request) -> JSONResponse:
    """POST /update - 拉取最新代码、构建前端、重启服务"""
    import asyncio
    import subprocess
    import sys
    from pathlib import Path

    project_root = Path(__file__).resolve().parent.parent

    # 同步执行 git pull + npm build，收集输出
    async def _do_update():
        loop = asyncio.get_event_loop()

        def _run():
            steps_log = []

            # git pull
            r = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=str(project_root), capture_output=True, text=True, timeout=60,
            )
            steps_log.append(f"[git pull] rc={r.returncode}\n{r.stdout}{r.stderr}")
            if r.returncode != 0:
                return False, "\n".join(steps_log)

            # npm install + build
            admin_ui = project_root / "admin-ui"
            if (admin_ui / "package.json").exists():
                r = subprocess.run(
                    ["npm", "install", "--silent"],
                    cwd=str(admin_ui), capture_output=True, text=True, timeout=120,
                )
                steps_log.append(f"[npm install] rc={r.returncode}\n{r.stdout}{r.stderr}")

                r = subprocess.run(
                    ["npm", "run", "build"],
                    cwd=str(admin_ui), capture_output=True, text=True, timeout=120,
                )
                steps_log.append(f"[npm build] rc={r.returncode}\n{r.stdout}{r.stderr}")
                if r.returncode != 0:
                    return False, "\n".join(steps_log)

            # pip install
            venv_pip = project_root / "venv" / "bin" / "pip"
            if venv_pip.exists():
                r = subprocess.run(
                    [str(venv_pip), "install", "-q", "-r", "requirements.txt"],
                    cwd=str(project_root), capture_output=True, text=True, timeout=60,
                )
                steps_log.append(f"[pip install] rc={r.returncode}\n{r.stdout}{r.stderr}")

            return True, "\n".join(steps_log)

        success, log = await loop.run_in_executor(None, _run)
        if not success:
            logger.error("更新失败:\n%s", log)
            return

        logger.info("更新完成，正在重启...\n%s", log)
        await asyncio.sleep(0.5)
        if platform.system() == "Windows":
            subprocess.Popen([sys.executable] + sys.argv, close_fds=True)
            os._exit(0)
        else:
            os.execv(sys.executable, [sys.executable] + sys.argv)

    asyncio.ensure_future(_do_update())
    return JSONResponse(content={"success": True, "message": "正在更新并重启..."})