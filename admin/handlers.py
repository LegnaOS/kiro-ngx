"""Admin API HTTP 处理器"""

import json
import logging
import os
import platform
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from admin.error import AdminServiceError
from admin.types import (
    AddCredentialRequest, SetDisabledRequest,
    SetPriorityRequest, SuccessResponse,
)

logger = logging.getLogger(__name__)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MEMORY_BREAKDOWN_TOP_N = 8
_MEMORY_BREAKDOWN_CACHE_TTL_SEC = 15.0
_MEMORY_BREAKDOWN_CACHE_LOCK = threading.Lock()
_MEMORY_BREAKDOWN_CACHE_AT = 0.0
_MEMORY_BREAKDOWN_CACHE_ITEMS: list[dict[str, Any]] = []
_MEMORY_BREAKDOWN_CACHE_TOTAL_MB = 0.0
_TRACEMALLOC_INITIALIZED = False


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


async def reset_all_counters(request: Request) -> JSONResponse:
    """POST /credentials/reset-all"""
    service = request.app.state.admin_service
    service.reset_all_counters()
    return JSONResponse(
        content=SuccessResponse.new("所有凭据计数器已重置").to_dict()
    )

async def get_credential_balance(request: Request, id: int) -> JSONResponse:
    """GET /credentials/{id}/balance"""
    service = request.app.state.admin_service
    force_refresh = request.query_params.get("forceRefresh", "").lower() in ("1", "true", "yes", "on")
    try:
        response = await service.get_balance(id, force_refresh=force_refresh)
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
        system = platform.system()
        if system == "Windows":
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
        elif system == "Darwin":
            import subprocess
            r = subprocess.run(
                ["ps", "-o", "rss=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                return int(r.stdout.strip()) / 1024  # KB → MB
        else:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) / 1024
    except Exception:
        pass
    return 0.0


def _normalize_memory_module(filename: str) -> tuple[str, str]:
    """把 tracemalloc 的文件路径映射为更稳定的模块名与展示路径。"""
    path = Path(filename)
    try:
        path = path.resolve()
    except Exception:
        pass

    try:
        relative = path.relative_to(_PROJECT_ROOT)
        display_path = str(relative).replace("\\", "/")
        if relative.suffix == ".py":
            module = ".".join(relative.with_suffix("").parts)
        else:
            module = ".".join(relative.parts)
        return module, display_path
    except Exception:
        pass

    parts = [p for p in path.parts if p]
    if "site-packages" in parts:
        index = parts.index("site-packages")
        if index + 1 < len(parts):
            package = parts[index + 1]
            return package, f".../site-packages/{package}"

    if path.suffix == ".py":
        return path.stem, path.name
    return path.name, path.name


def _collect_memory_breakdown(limit: int) -> tuple[list[dict[str, Any]], float]:
    """抓取 Python 内存分配明细（按模块聚合）。"""
    global _TRACEMALLOC_INITIALIZED
    try:
        import tracemalloc
    except Exception:
        return [], 0.0

    if not _TRACEMALLOC_INITIALIZED:
        try:
            tracemalloc.start(1)
            _TRACEMALLOC_INITIALIZED = True
        except Exception:
            return [], 0.0

    if not tracemalloc.is_tracing():
        return [], 0.0

    try:
        snapshot = tracemalloc.take_snapshot()
    except Exception:
        return [], 0.0

    module_totals: dict[str, dict[str, Any]] = {}
    traced_total_bytes = 0

    for stat in snapshot.statistics("filename"):
        if not stat.traceback:
            continue

        size_bytes = int(stat.size)
        if size_bytes <= 0:
            continue

        traced_total_bytes += size_bytes
        filename = stat.traceback[0].filename
        module, display_path = _normalize_memory_module(filename)
        existing = module_totals.get(module)
        if existing is None:
            existing = {"module": module, "path": display_path, "sizeBytes": 0}
            module_totals[module] = existing
        existing["sizeBytes"] += size_bytes

    if traced_total_bytes <= 0 or not module_totals:
        return [], 0.0

    top_items = sorted(
        module_totals.values(),
        key=lambda item: item["sizeBytes"],
        reverse=True,
    )[:max(1, limit)]

    breakdown: list[dict[str, Any]] = []
    for item in top_items:
        size_bytes = int(item["sizeBytes"])
        breakdown.append({
            "module": str(item["module"]),
            "path": str(item["path"]),
            "memoryMb": round(size_bytes / (1024 * 1024), 2),
            "sharePercent": round((size_bytes / traced_total_bytes) * 100, 1),
        })

    return breakdown, round(traced_total_bytes / (1024 * 1024), 2)


def _get_memory_breakdown(limit: int = _MEMORY_BREAKDOWN_TOP_N) -> tuple[list[dict[str, Any]], float]:
    """获取缓存后的内存明细，降低高频轮询开销。"""
    global _MEMORY_BREAKDOWN_CACHE_AT, _MEMORY_BREAKDOWN_CACHE_ITEMS, _MEMORY_BREAKDOWN_CACHE_TOTAL_MB

    now = time.time()
    with _MEMORY_BREAKDOWN_CACHE_LOCK:
        if now - _MEMORY_BREAKDOWN_CACHE_AT < _MEMORY_BREAKDOWN_CACHE_TTL_SEC:
            cached_items = [dict(item) for item in _MEMORY_BREAKDOWN_CACHE_ITEMS]
            return cached_items, _MEMORY_BREAKDOWN_CACHE_TOTAL_MB

    items, total_mb = _collect_memory_breakdown(limit=limit)

    with _MEMORY_BREAKDOWN_CACHE_LOCK:
        _MEMORY_BREAKDOWN_CACHE_AT = now
        _MEMORY_BREAKDOWN_CACHE_ITEMS = [dict(item) for item in items]
        _MEMORY_BREAKDOWN_CACHE_TOTAL_MB = total_mb

    return items, total_mb


def _get_cpu_percent() -> float:
    """获取系统 CPU 使用率，跨平台"""
    try:
        system = platform.system()
        if system == "Windows":
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
        elif system == "Darwin":
            import subprocess
            r = subprocess.run(
                ["top", "-l", "1", "-n", "0", "-s", "0"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                for line in r.stdout.split("\n"):
                    if "CPU usage" in line:
                        # "CPU usage: 5.26% user, 10.52% sys, 84.21% idle"
                        for part in line.split(","):
                            if "idle" in part:
                                idle_pct = float(part.strip().split("%")[0].strip().split()[-1])
                                return round(100.0 - idle_pct, 1)
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
    memory_breakdown, traced_memory_mb = _get_memory_breakdown()
    return JSONResponse(content={
        "cpuPercent": cpu,
        "memoryMb": round(mem, 1),
        "memoryBreakdown": memory_breakdown,
        "tracedMemoryMb": traced_memory_mb,
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

            # 丢弃本地改动
            _append_update_log("丢弃本地改动 ...")
            subprocess.run(["git", "checkout", "--", "."], timeout=30, **run_kw)

            if target_commit:
                # 切换到指定 commit
                _append_update_log(f"git checkout {target_commit[:8]} ...")
                r = subprocess.run(["git", "checkout", target_commit], timeout=30, **run_kw)
                if r.returncode != 0:
                    _append_update_log(f"git checkout 失败: {r.stderr.strip()}")
                    return False
                _append_update_log("git checkout 完成")
            else:
                # git pull --ff-only，失败则 reset 到远程
                _append_update_log("git pull --ff-only ...")
                r = subprocess.run(["git", "pull", "--ff-only"], timeout=60, **run_kw)
                if r.returncode != 0:
                    _append_update_log("ff-only 失败，reset 到 origin/master ...")
                    r = subprocess.run(["git", "reset", "--hard", "origin/master"], timeout=30, **run_kw)
                    if r.returncode != 0:
                        _append_update_log(f"git reset 失败: {r.stderr.strip()}")
                        return False
                _append_update_log("代码更新完成")

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


async def get_token_usage_history(request: Request) -> JSONResponse:
    """GET /token-usage/history - 获取历史 token 用量"""
    from token_usage import get_token_usage_tracker
    days = int(request.query_params.get("days", "7"))
    tracker = get_token_usage_tracker()
    if not tracker:
        return JSONResponse(content={"history": {}})
    return JSONResponse(content={"history": tracker.get_history(days)})


async def get_token_usage_hourly(request: Request) -> JSONResponse:
    """GET /token-usage/hourly - 获取今日小时级用量"""
    from token_usage import get_token_usage_tracker
    tracker = get_token_usage_tracker()
    if not tracker:
        return JSONResponse(content={"hourly": {}})
    return JSONResponse(content={"hourly": tracker.get_hourly()})


async def get_model_list(request: Request) -> JSONResponse:
    """GET /models - 获取支持的模型列表（内置 + 自定义）"""
    from anthropic_api.handlers import MODELS
    builtin_ids = {m.id for m in MODELS}
    models = [{"id": m.id, "displayName": m.display_name, "custom": False} for m in MODELS]
    # 追加自定义模型
    service = request.app.state.admin_service
    for mid in service.get_custom_models():
        if mid not in builtin_ids:
            models.append({"id": mid, "displayName": mid, "custom": True})
    return JSONResponse(content={"models": models})


async def get_routing_config(request: Request) -> JSONResponse:
    """GET /routing - 获取路由配置"""
    service = request.app.state.admin_service
    free_models = service.get_free_models()
    custom_models = service.get_custom_models()
    return JSONResponse(content={"freeModels": free_models, "customModels": custom_models})


async def set_routing_config(request: Request) -> JSONResponse:
    """PUT /routing - 更新路由配置"""
    service = request.app.state.admin_service
    body = await request.json()
    free_models = body.get("freeModels", [])
    if not isinstance(free_models, list):
        return JSONResponse(status_code=400, content={"success": False, "message": "freeModels 必须是数组"})
    custom_models = body.get("customModels")
    if custom_models is not None:
        if not isinstance(custom_models, list):
            return JSONResponse(status_code=400, content={"success": False, "message": "customModels 必须是数组"})
        service.set_custom_models(custom_models)
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


async def get_runtime_logs(request: Request) -> JSONResponse:
    """GET /logs/runtime - 获取运行时日志尾部/增量片段"""
    from admin.runtime_log import (
        DEFAULT_RUNTIME_LOG_LIMIT,
        MAX_RUNTIME_LOG_LIMIT,
        get_runtime_log_buffer,
    )

    def _parse_int(name: str, default: int) -> int:
        raw = request.query_params.get(name)
        if raw is None or raw == "":
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    limit = _parse_int("limit", DEFAULT_RUNTIME_LOG_LIMIT)
    limit = max(1, min(limit, MAX_RUNTIME_LOG_LIMIT))
    cursor = _parse_int("cursor", 0)
    level = request.query_params.get("level") or None
    keyword = (request.query_params.get("q") or "").strip() or None

    buf = get_runtime_log_buffer()
    if not buf:
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": "运行时日志缓冲区未初始化"},
        )

    if cursor > 0:
        result = buf.since(cursor=cursor, limit=limit, level=level, keyword=keyword)
    else:
        result = buf.tail(limit=limit, level=level, keyword=keyword)
    return JSONResponse(content=result)


# ============ Claude Code 配置管理 ============

def _claude_home() -> Path:
    """跨平台获取 ~/.claude 目录"""
    if platform.system() == "Windows":
        return Path(os.environ.get("USERPROFILE", "~")) / ".claude"
    return Path.home() / ".claude"


async def get_claude_settings(request: Request) -> JSONResponse:
    """GET /claude/settings - 读取 Claude Code settings.json"""
    settings_path = _claude_home() / "settings.json"
    if not settings_path.exists():
        return JSONResponse(content={"settings": {}, "path": str(settings_path), "exists": False})
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"读取失败: {e}"})
    return JSONResponse(content={"settings": data, "path": str(settings_path), "exists": True})


async def save_claude_settings(request: Request) -> JSONResponse:
    """PUT /claude/settings - 写入 Claude Code settings.json"""
    settings_path = _claude_home() / "settings.json"
    body = await request.json()
    settings = body.get("settings")
    if not isinstance(settings, dict):
        return JSONResponse(status_code=400, content={"success": False, "message": "settings 必须是 JSON 对象"})
    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"写入失败: {e}"})
    return JSONResponse(content={"success": True, "message": "已保存"})


async def get_claude_profiles(request: Request) -> JSONResponse:
    """GET /claude/profiles - 列出所有 settings 配置文件（含备用配置）"""
    claude_dir = _claude_home()
    profiles: list[dict] = []
    if not claude_dir.exists():
        return JSONResponse(content={"profiles": profiles})
    for f in sorted(claude_dir.iterdir()):
        if f.suffix == ".json" and "settings" in f.name.lower():
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                base_url = data.get("env", {}).get("ANTHROPIC_BASE_URL", "")
                model = data.get("model", "")
                profiles.append({
                    "filename": f.name,
                    "path": str(f),
                    "baseUrl": base_url,
                    "model": model,
                    "isActive": f.name == "settings.json",
                })
            except Exception:
                profiles.append({"filename": f.name, "path": str(f), "baseUrl": "", "model": "", "isActive": False})
    return JSONResponse(content={"profiles": profiles})


async def switch_claude_profile(request: Request) -> JSONResponse:
    """POST /claude/profiles/switch - 切换配置文件（交换 settings.json 与目标文件）"""
    body = await request.json()
    target = body.get("filename", "")
    if not target or target == "settings.json":
        return JSONResponse(status_code=400, content={"success": False, "message": "无效的目标文件"})
    claude_dir = _claude_home()
    active_path = claude_dir / "settings.json"
    target_path = claude_dir / target
    if not target_path.exists():
        return JSONResponse(status_code=404, content={"success": False, "message": f"文件不存在: {target}"})
    # 把当前 settings.json 备份为 settings-backup.json，再把目标复制过来
    try:
        if active_path.exists():
            # 生成备份名：settings-{原目标去掉前缀}.json 或 settings-prev.json
            backup_name = f"settings-prev.json"
            backup_path = claude_dir / backup_name
            # 如果 backup 已存在，覆盖
            import shutil
            shutil.copy2(str(active_path), str(backup_path))
        shutil.copy2(str(target_path), str(active_path))
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"切换失败: {e}"})
    return JSONResponse(content={"success": True, "message": f"已切换到 {target}"})


async def get_claude_sessions(request: Request) -> JSONResponse:
    """GET /claude/sessions - 读取 Claude Code 会话列表"""
    history_path = _claude_home() / "history.jsonl"
    if not history_path.exists():
        return JSONResponse(content={"sessions": []})

    limit = int(request.query_params.get("limit", "50"))
    project_filter = request.query_params.get("project", "")

    sessions_map: dict[str, dict] = {}
    try:
        for line in history_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            sid = entry.get("sessionId", "")
            if not sid:
                continue
            project = entry.get("project", "")
            if project_filter and project_filter not in project:
                continue
            ts = entry.get("timestamp", 0)
            if sid not in sessions_map or ts > sessions_map[sid].get("lastTimestamp", 0):
                if sid not in sessions_map:
                    sessions_map[sid] = {
                        "sessionId": sid,
                        "project": project,
                        "firstPrompt": entry.get("display", ""),
                        "firstTimestamp": ts,
                        "lastTimestamp": ts,
                        "promptCount": 0,
                    }
                sessions_map[sid]["lastTimestamp"] = max(sessions_map[sid]["lastTimestamp"], ts)
                sessions_map[sid]["promptCount"] += 1
            else:
                sessions_map[sid]["promptCount"] += 1
                sessions_map[sid]["lastTimestamp"] = max(sessions_map[sid]["lastTimestamp"], ts)
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": f"读取失败: {e}"})

    # 按最后活跃时间倒序
    sessions = sorted(sessions_map.values(), key=lambda s: s["lastTimestamp"], reverse=True)[:limit]
    return JSONResponse(content={"sessions": sessions})


# ============ 多 API Key 管理 ============

def _get_key_mgr():
    from api_keys import get_api_key_manager
    return get_api_key_manager()


async def get_api_keys(request: Request) -> JSONResponse:
    """GET /keys"""
    mgr = _get_key_mgr()
    keys = mgr.get_all_keys() if mgr else []
    groups = mgr.get_groups() if mgr else {}

    # 把代理主 Key（Claude 使用的 apiKey）作为管理员条目插入列表头部
    proxy_key = getattr(request.app.state, "proxy_api_key", None)
    if proxy_key:
        masked = proxy_key[:7] + "..." + proxy_key[-4:] if len(proxy_key) > 12 else proxy_key
        keys.insert(0, {
            "key": proxy_key,
            "maskedKey": masked,
            "name": "管理员",
            "group": "admin",
            "rate": None,
            "monthlyQuota": None,
            "effectiveRate": 0,
            "effectiveQuota": -1,
            "billedTokens": 0,
            "billedMonth": "",
            "totalRawTokens": 0,
            "requestCount": 0,
            "enabled": True,
            "createdAt": "",
            "isAdmin": True,
        })

    return JSONResponse(content={"keys": keys, "groups": groups})


async def add_api_key(request: Request) -> JSONResponse:
    """POST /keys"""
    mgr = _get_key_mgr()
    if not mgr:
        return JSONResponse(status_code=503, content={"success": False, "message": "Key manager not initialized"})
    body = await request.json()
    name = body.get("name", "").strip()
    group = body.get("group", "").strip()
    if not name:
        return JSONResponse(status_code=400, content={"success": False, "message": "name is required"})
    entry = mgr.add_key(
        name=name,
        group=group,
        rate=body.get("rate"),
        monthly_quota=body.get("monthlyQuota"),
    )
    return JSONResponse(content={"success": True, "key": entry})


async def update_api_key(request: Request) -> JSONResponse:
    """PUT /keys/{key}"""
    key_str = request.path_params["key"]
    mgr = _get_key_mgr()
    if not mgr:
        return JSONResponse(status_code=503, content={"success": False, "message": "Key manager not initialized"})
    body = await request.json()
    result = mgr.update_key(key_str, **{k: v for k, v in body.items() if k in ("name", "group", "rate", "monthlyQuota", "enabled")})
    if not result:
        return JSONResponse(status_code=404, content={"success": False, "message": "Key not found"})
    return JSONResponse(content={"success": True, "key": result})


async def delete_api_key(request: Request) -> JSONResponse:
    """DELETE /keys/{key}"""
    key_str = request.path_params["key"]
    mgr = _get_key_mgr()
    if not mgr:
        return JSONResponse(status_code=503, content={"success": False, "message": "Key manager not initialized"})
    if not mgr.delete_key(key_str):
        return JSONResponse(status_code=404, content={"success": False, "message": "Key not found"})
    return JSONResponse(content={"success": True})


async def regenerate_api_key(request: Request) -> JSONResponse:
    """POST /keys/{key}/regenerate"""
    key_str = request.path_params["key"]
    mgr = _get_key_mgr()
    if not mgr:
        return JSONResponse(status_code=503, content={"success": False, "message": "Key manager not initialized"})
    result = mgr.regenerate_key(key_str)
    if not result:
        return JSONResponse(status_code=404, content={"success": False, "message": "Key not found"})
    return JSONResponse(content={"success": True, "key": result})


async def reset_api_key_usage(request: Request) -> JSONResponse:
    """POST /keys/{key}/reset"""
    key_str = request.path_params["key"]
    mgr = _get_key_mgr()
    if not mgr:
        return JSONResponse(status_code=503, content={"success": False, "message": "Key manager not initialized"})
    if not mgr.reset_usage(key_str):
        return JSONResponse(status_code=404, content={"success": False, "message": "Key not found"})
    return JSONResponse(content={"success": True})


async def set_api_key_group(request: Request) -> JSONResponse:
    """PUT /keys/groups/{name}"""
    name = request.path_params["name"]
    mgr = _get_key_mgr()
    if not mgr:
        return JSONResponse(status_code=503, content={"success": False, "message": "Key manager not initialized"})
    body = await request.json()
    rate = body.get("rate", 1.0)
    monthly_quota = body.get("monthlyQuota", -1)
    mgr.set_group(name, rate, monthly_quota)
    return JSONResponse(content={"success": True})


async def delete_api_key_group(request: Request) -> JSONResponse:
    """DELETE /keys/groups/{name}"""
    name = request.path_params["name"]
    mgr = _get_key_mgr()
    if not mgr:
        return JSONResponse(status_code=503, content={"success": False, "message": "Key manager not initialized"})
    if not mgr.delete_group(name):
        return JSONResponse(status_code=400, content={"success": False, "message": "Group not found or still in use"})
    return JSONResponse(content={"success": True})


async def get_key_usage_stats(request: Request) -> JSONResponse:
    """GET /keys/usage-stats - 获取所有 key 的用量统计（不含 key 字符串）"""
    mgr = _get_key_mgr()
    if not mgr:
        return JSONResponse(content={"keys": [], "groups": {}})
    return JSONResponse(content={
        "keys": mgr.get_usage_stats(),
        "groups": mgr.get_groups(),
    })
