"""Kiroshop 补货代理 handlers — 转发请求到 kiroshop.xyz"""

import math
import time
import asyncio
import logging
from pathlib import Path
from urllib.request import Request as UrlRequest, urlopen
from urllib.error import HTTPError, URLError
import json
from datetime import datetime, timedelta
import re
from concurrent.futures import ThreadPoolExecutor

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

KIROSHOP_BASE = "https://kiroshop.xyz/shop/api"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"


def _kiroshop_headers(token: str, referer: str = "https://kiroshop.xyz/shop") -> dict:
    return {
        "Accept": "*/*",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Referer": referer,
        "User-Agent": _UA,
    }


def _extract_token(request: Request) -> str | None:
    return request.headers.get("x-kiroshop-token")


def _kiroshop_login_sync(email: str, password: str) -> str:
    """同步登录 kiroshop，返回新 token；失败抛异常"""
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Origin": "https://kiroshop.xyz",
        "Referer": "https://kiroshop.xyz/shop",
        "User-Agent": _UA,
    }
    data = json.dumps({"email": email, "password": password}).encode("utf-8")
    req = UrlRequest("https://kiroshop.xyz/shop/api/auth/login",
                     data=data, headers=headers, method="POST")
    with urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    token = result.get("token", "")
    if not token:
        raise RuntimeError("登录失败，未返回 token")
    return token


def _relogin_and_save() -> str:
    """从配置读取账密重新登录，更新 config 中的 token 并返回"""
    cfg = {}
    if _CONFIG_PATH.exists():
        try:
            cfg = json.loads(_CONFIG_PATH.read_text("utf-8"))
        except Exception:
            pass
    email = cfg.get("email", "").strip()
    password = cfg.get("password", "").strip()
    if not email or not password:
        raise RuntimeError("配置中缺少 email 或 password，无法重新登录")
    token = _kiroshop_login_sync(email, password)
    cfg["token"] = token
    _CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
    return token


def _kiroshop_get(url: str, token: str, referer: str = "https://kiroshop.xyz/shop") -> dict | list:
    headers = _kiroshop_headers(token, referer)
    req = UrlRequest(url, headers=headers, method="GET")
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _kiroshop_post(url: str, token: str, body: dict, referer: str = "https://kiroshop.xyz/shop/orders") -> dict:
    headers = _kiroshop_headers(token, referer)
    data = json.dumps(body).encode("utf-8")
    req = UrlRequest(url, data=data, headers=headers, method="POST")
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


# --- 登录获取 Token ---
async def restock_login(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "无效的请求体"})

    email = body.get("email", "").strip()
    password = body.get("password", "").strip()
    if not email or not password:
        return JSONResponse(status_code=400, content={"error": "缺少 email 或 password"})

    try:
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Origin": "https://kiroshop.xyz",
            "Referer": "https://kiroshop.xyz/shop",
            "User-Agent": _UA,
        }
        data = json.dumps({"email": email, "password": password}).encode("utf-8")
        req = UrlRequest("https://kiroshop.xyz/shop/api/auth/login",
                         data=data, headers=headers, method="POST")
        with urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        token = result.get("token", "")
        if not token:
            return JSONResponse(status_code=401, content={"error": "登录失败，未返回 token"})
        return JSONResponse(content={"token": token})
    except HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        return JSONResponse(status_code=e.code, content={"error": body_text})
    except (URLError, Exception) as e:
        return JSONResponse(status_code=502, content={"error": str(e)})
async def get_restock_inventory(request: Request) -> JSONResponse:
    token = _extract_token(request)
    if not token:
        return JSONResponse(status_code=400, content={"error": "缺少 x-kiroshop-token"})
    try:
        data = _kiroshop_get(f"{KIROSHOP_BASE}/products", token)
        return JSONResponse(content=data)
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return JSONResponse(status_code=e.code, content={"error": body})
    except (URLError, Exception) as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


# --- 订单列表（自动分页，仅过滤已取消）---
async def get_restock_orders(request: Request) -> JSONResponse:
    token = _extract_token(request)
    if not token:
        return JSONResponse(status_code=400, content={"error": "缺少 x-kiroshop-token"})
    try:
        all_orders = []
        page, page_size, total_pages = 1, 20, 1
        while page <= total_pages:
            result = _kiroshop_get(
                f"{KIROSHOP_BASE}/orders?page={page}&page_size={page_size}",
                token, "https://kiroshop.xyz/shop/orders",
            )
            all_orders.extend(result.get("items", []))
            if page == 1:
                total = result.get("total", 0)
                total_pages = math.ceil(total / page_size) if total > 0 else 1
            page += 1
        filtered = [o for o in all_orders if o.get("status") != "cancelled"]
        return JSONResponse(content={"orders": filtered, "total": len(filtered)})
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return JSONResponse(status_code=e.code, content={"error": body})
    except (URLError, Exception) as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


# --- 订单详情 ---
async def get_restock_order_detail(request: Request, order_id: int) -> JSONResponse:
    token = _extract_token(request)
    if not token:
        return JSONResponse(status_code=400, content={"error": "缺少 x-kiroshop-token"})
    try:
        data = _kiroshop_get(
            f"{KIROSHOP_BASE}/orders/{order_id}",
            token, "https://kiroshop.xyz/shop/orders",
        )
        return JSONResponse(content=data)
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return JSONResponse(status_code=e.code, content={"error": body})
    except (URLError, Exception) as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


# --- 封禁检测 ---
async def check_restock_ban(request: Request, order_id: int) -> JSONResponse:
    token = _extract_token(request)
    if not token:
        return JSONResponse(status_code=400, content={"error": "缺少 x-kiroshop-token"})
    try:
        detail = _kiroshop_get(
            f"{KIROSHOP_BASE}/orders/{order_id}",
            token, "https://kiroshop.xyz/shop/orders",
        )
        deliveries = detail.get("deliveries", [])
        if not deliveries:
            return JSONResponse(content={"deliveries": [], "message": "该订单暂无发货信息"})

        results = []
        for delivery in deliveries:
            delivery_id = delivery.get("id")
            if not delivery_id:
                continue
            check_data = _kiroshop_post(
                f"{KIROSHOP_BASE}/orders/{order_id}/check-ban",
                token, {"delivery_id": delivery_id},
            )
            results.append({
                "delivery_id": delivery_id,
                "success": check_data.get("success", False),
                "total": check_data.get("total", 0),
                "banned_count": check_data.get("banned_count", 0),
                "results": check_data.get("results", []),
            })
        return JSONResponse(content={"deliveries": results})
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return JSONResponse(status_code=e.code, content={"error": body})
    except (URLError, Exception) as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


# --- 配置读写 ---
async def get_restock_config(request: Request) -> JSONResponse:
    try:
        if _CONFIG_PATH.exists():
            data = json.loads(_CONFIG_PATH.read_text("utf-8"))
        else:
            data = {}
        return JSONResponse(content={
            "email": data.get("email", ""),
            "password": data.get("password", ""),
            "token": data.get("token", ""),
            "ar_interval": data.get("ar_interval", "1"),
            "restock_interval": data.get("restock_interval", "30"),
        })
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


async def save_restock_config(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "无效的请求体"})

    existing = {}
    if _CONFIG_PATH.exists():
        try:
            existing = json.loads(_CONFIG_PATH.read_text("utf-8"))
        except Exception:
            pass

    for key in ("email", "password", "token", "ar_interval", "restock_interval"):
        if key in body:
            existing[key] = body[key]

    _CONFIG_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2), "utf-8")
    return JSONResponse(content={"success": True})


# --- 提货 ---
async def restock_deliver(request: Request, order_id: int) -> JSONResponse:
    token = _extract_token(request)
    if not token:
        return JSONResponse(status_code=400, content={"error": "缺少 x-kiroshop-token"})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "无效的请求体"})

    count = body.get("count", 0)
    if not isinstance(count, int) or count <= 0:
        return JSONResponse(status_code=400, content={"error": "count 必须为正整数"})

    try:
        data = _kiroshop_post(
            f"{KIROSHOP_BASE}/orders/{order_id}/deliver",
            token, {"count": count},
        )
        return JSONResponse(content=data)
    except HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        return JSONResponse(status_code=e.code, content={"error": body_text})
    except (URLError, Exception) as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


# --- 一键换号 ---
async def restock_batch_replace(request: Request, order_id: int) -> JSONResponse:
    token = _extract_token(request)
    if not token:
        return JSONResponse(status_code=400, content={"error": "缺少 x-kiroshop-token"})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "无效的请求体"})

    delivery_id = body.get("delivery_id")
    if not delivery_id:
        return JSONResponse(status_code=400, content={"error": "缺少 delivery_id"})

    try:
        data = _kiroshop_post(
            f"{KIROSHOP_BASE}/orders/{order_id}/batch-replace-banned",
            token, {"delivery_id": delivery_id},
        )
        return JSONResponse(content=data)
    except HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        return JSONResponse(status_code=e.code, content={"error": body_text})
    except (URLError, Exception) as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


# --- 过保分析 + 封号检测 ---

def _parse_time(time_str: str) -> datetime | None:
    if not time_str:
        return None
    try:
        return datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _check_warranty(delivered_at_str: str, warranty_hours: int):
    """返回 (is_expired, warranty_msg)"""
    if warranty_hours <= 0:
        return False, "无质保"
    delivered_at = _parse_time(delivered_at_str)
    if not delivered_at:
        return False, "无发货时间"
    expire_time = delivered_at + timedelta(hours=warranty_hours)
    now = datetime.now()
    if now > expire_time:
        return True, f"已过保 ({expire_time.strftime('%m-%d %H:%M')})"
    remaining_h = (expire_time - now).total_seconds() / 3600
    return False, f"剩余 {remaining_h:.1f}h ({expire_time.strftime('%m-%d %H:%M')})"


def _check_ban_sync(order_id: int, delivery_id: int, token: str) -> dict:
    """对单个 delivery 执行封号检测，返回检测结果"""
    try:
        data = _kiroshop_post(
            f"{KIROSHOP_BASE}/orders/{order_id}/check-ban",
            token, {"delivery_id": delivery_id},
        )
        return {
            "success": True,
            "total": data.get("total", 0),
            "banned_count": data.get("banned_count", 0),
            "results": data.get("results", []),
        }
    except Exception as e:
        return {"success": False, "total": 0, "banned_count": 0, "results": [], "error": str(e)}


async def analyze_restock_orders(request: Request) -> JSONResponse:
    """分析所有 paid 订单：逐个 delivery 检测封号 + 质保状态，封号且在保 = 待补号"""
    token = _extract_token(request)
    if not token:
        return JSONResponse(status_code=400, content={"error": "缺少 x-kiroshop-token"})

    try:
        # 获取所有订单
        all_orders = []
        page, page_size, total_pages = 1, 20, 1
        while page <= total_pages:
            result = _kiroshop_get(
                f"{KIROSHOP_BASE}/orders?page={page}&page_size={page_size}",
                token, "https://kiroshop.xyz/shop/orders",
            )
            all_orders.extend(result.get("items", []))
            if page == 1:
                total = result.get("total", 0)
                total_pages = math.ceil(total / page_size) if total > 0 else 1
            page += 1

        active_orders = [o for o in all_orders if o.get("status") in ("paid", "completed")]
        if not active_orders:
            return JSONResponse(content={"pending_tasks": [], "summaries": []})

        pending_tasks = []
        summaries = []

        for order in active_orders:
            oid = order["id"]
            warranty_hours = order.get("warranty_hours", 0)

            try:
                detail = _kiroshop_get(
                    f"{KIROSHOP_BASE}/orders/{oid}",
                    token, "https://kiroshop.xyz/shop/orders",
                )
            except Exception:
                continue

            deliveries = detail.get("deliveries", [])
            if not deliveries:
                summaries.append({
                    "order_id": oid, "order_no": order.get("order_no", ""),
                    "product_name": order.get("product_name", ""),
                    "deliveries": [],
                })
                continue

            d_infos = []
            for d in deliveries:
                did = d.get("id")
                delivered_at = d.get("delivered_at", "")
                account_count = d.get("account_count", 0)
                is_expired, warranty_msg = _check_warranty(delivered_at, warranty_hours)

                # 执行封号检测
                ban_info = _check_ban_sync(oid, did, token)
                banned_count = ban_info.get("banned_count", 0)
                total_accounts = ban_info.get("total", 0)

                # 封号 + 在保 = 待补号
                need_replace = banned_count > 0 and not is_expired

                d_info = {
                    "delivery_id": did, "delivered_at": delivered_at,
                    "account_count": account_count,
                    "is_expired": is_expired, "warranty_msg": warranty_msg,
                    "banned_count": banned_count, "total_accounts": total_accounts,
                    "need_replace": need_replace,
                }
                d_infos.append(d_info)

                if need_replace:
                    pending_tasks.append({
                        "order_id": oid, "delivery_id": did,
                        "banned_count": banned_count,
                        "warranty_msg": warranty_msg,
                    })

            summaries.append({
                "order_id": oid, "order_no": order.get("order_no", ""),
                "product_name": order.get("product_name", ""),
                "deliveries": d_infos,
            })

        return JSONResponse(content={
            "pending_tasks": pending_tasks,
            "summaries": summaries,
        })
    except HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        return JSONResponse(status_code=e.code, content={"error": body_text})
    except (URLError, Exception) as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


# --- 自动补号后台任务 ---
# 流程：启动时 analyze 获取待补号列表 → 按间隔直接调用 batch-replace → 成功后导入凭据

_auto_replace_state: dict = {
    "running": False,
    "task": None,
    "app": None,
    "pending_tasks": [],   # [{order_id, delivery_id, banned_count, warranty_msg}]
    "replaced_deliveries": [],  # 成功补号的 (order_id, delivery_id) 列表，用于后续导入
    "last_check": "",
    "interval": 1,
    "logs": [],
}
_MAX_LOGS = 200


def _ar_log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    _auto_replace_state["logs"].append(line)
    if len(_auto_replace_state["logs"]) > _MAX_LOGS:
        _auto_replace_state["logs"] = _auto_replace_state["logs"][-_MAX_LOGS:]
    logger.info(f"[auto-replace] {msg}")


def _try_replace_sync(order_id, delivery_id, token):
    """同步换号"""
    try:
        data = _kiroshop_post(
            f"{KIROSHOP_BASE}/orders/{order_id}/batch-replace-banned",
            token, {"delivery_id": delivery_id},
        )
        return order_id, delivery_id, 200, data
    except HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        return order_id, delivery_id, e.code, body_text
    except Exception as e:
        return order_id, delivery_id, -1, str(e)


def _analyze_sync(token: str) -> list[dict]:
    """同步版 analyze：paid+completed 订单，check-ban 每个未过保 delivery"""
    all_orders = []
    page, page_size, total_pages = 1, 20, 1
    while page <= total_pages:
        result = _kiroshop_get(
            f"{KIROSHOP_BASE}/orders?page={page}&page_size={page_size}",
            token, "https://kiroshop.xyz/shop/orders",
        )
        all_orders.extend(result.get("items", []))
        if page == 1:
            t = result.get("total", 0)
            total_pages = math.ceil(t / page_size) if t > 0 else 1
        page += 1

    active = [o for o in all_orders if o.get("status") in ("paid", "completed")]
    tasks = []
    for order in active:
        oid = order["id"]
        wh = order.get("warranty_hours", 0)
        try:
            detail = _kiroshop_get(
                f"{KIROSHOP_BASE}/orders/{oid}",
                token, "https://kiroshop.xyz/shop/orders",
            )
        except Exception:
            continue
        for d in detail.get("deliveries", []):
            did = d.get("id")
            delivered_at = d.get("delivered_at", "")
            is_expired, warranty_msg = _check_warranty(delivered_at, wh)
            if is_expired:
                continue
            ban = _check_ban_sync(oid, did, token)
            if ban.get("banned_count", 0) > 0:
                tasks.append({
                    "order_id": oid, "delivery_id": did,
                    "banned_count": ban["banned_count"],
                    "warranty_msg": warranty_msg,
                })
    return tasks


async def _auto_replace_loop():
    """后台循环：按间隔直接调用 batch-replace，成功/无封禁则移除；成功后提取凭据导入"""
    state = _auto_replace_state

    def _read_config():
        if _CONFIG_PATH.exists():
            return json.loads(_CONFIG_PATH.read_text("utf-8"))
        return {}

    # 登录获取新 token
    try:
        token = _relogin_and_save()
    except Exception as e:
        _ar_log(f"登录失败: {e}")
        state["running"] = False
        return

    # 1. analyze 获取待补号列表
    _ar_log("正在分析订单封号状态...")
    try:
        tasks = _analyze_sync(token)
    except Exception as e:
        _ar_log(f"分析失败: {e}")
        state["running"] = False
        return

    state["pending_tasks"] = tasks
    if not tasks:
        _ar_log("没有需要补号的 delivery，停止")
        state["running"] = False
        return

    _ar_log(f"待补号: {len(tasks)} 个")
    for t in tasks:
        _ar_log(f"  订单{t['order_id']}/发货{t['delivery_id']} 封禁{t['banned_count']}个 {t['warranty_msg']}")

    # 2. 按间隔持续调用 batch-replace
    state["replaced_deliveries"] = []
    try:
        while state["running"] and state["pending_tasks"]:
            cfg = _read_config()
            token = cfg.get("token", "")
            interval = state["interval"]
            state["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            pending = list(state["pending_tasks"])
            _ar_log(f"执行 {len(pending)} 个换号")
            loop = asyncio.get_event_loop()
            succeeded = set()
            no_ban = set()
            with ThreadPoolExecutor(max_workers=min(len(pending), 10)) as pool:
                futs = {
                    loop.run_in_executor(pool, _try_replace_sync, t["order_id"], t["delivery_id"], token): t
                    for t in pending
                }
                for coro in asyncio.as_completed(futs):
                    oid, did, code, detail = await coro
                    if code == 200:
                        replaced = detail.get("replaced_count", "?") if isinstance(detail, dict) else "?"
                        _ar_log(f"订单{oid}/发货{did}: 替换 {replaced} 个")
                        succeeded.add((oid, did))
                    elif "No banned" in str(detail) or "no banned" in str(detail).lower():
                        _ar_log(f"订单{oid}/发货{did}: 无封禁账号，移除")
                        no_ban.add((oid, did))
                    elif "库存不足" in str(detail):
                        need = 0
                        m = re.search(r"需要(\d+)个", str(detail))
                        if m:
                            need = int(m.group(1))
                        _ar_log(f"订单{oid}/发货{did}: 库存不足{f' (需{need}个)' if need else ''}")
                    elif "质保期" in str(detail):
                        _ar_log(f"订单{oid}/发货{did}: 已超质保期，移除")
                        no_ban.add((oid, did))
                    else:
                        _ar_log(f"订单{oid}/发货{did}: [{code}] {detail}")

            # 记录成功补号的 delivery
            for oid, did in succeeded:
                state["replaced_deliveries"].append({"order_id": oid, "delivery_id": did})

            # 移除成功和无需换号的
            remove_set = succeeded | no_ban
            state["pending_tasks"] = [
                t for t in state["pending_tasks"]
                if (t["order_id"], t["delivery_id"]) not in remove_set
            ]

            if not state["pending_tasks"]:
                _ar_log("所有补号任务完成!")
                break

            _ar_log(f"剩余待补号: {len(state['pending_tasks'])}")
            await asyncio.sleep(interval)

    except asyncio.CancelledError:
        _ar_log("自动补号已停止")
    except Exception as e:
        _ar_log(f"自动补号异常: {e}")
    finally:
        state["running"] = False


async def start_auto_replace(request: Request) -> JSONResponse:
    state = _auto_replace_state
    if state["running"]:
        return JSONResponse(content={"success": False, "message": "已在运行中"})

    try:
        body = await request.json()
    except Exception:
        body = {}
    state["interval"] = body.get("interval", 1)

    state["running"] = True
    state["app"] = request.app
    state["logs"] = []
    state["pending_tasks"] = []
    state["replaced_deliveries"] = []
    state["task"] = asyncio.create_task(_auto_replace_loop())
    return JSONResponse(content={"success": True})


async def stop_auto_replace(request: Request) -> JSONResponse:
    state = _auto_replace_state
    if not state["running"]:
        return JSONResponse(content={"success": False, "message": "未在运行"})
    state["running"] = False
    task = state.get("task")
    if task and not task.done():
        task.cancel()
    state["task"] = None
    _ar_log("收到停止指令")
    return JSONResponse(content={"success": True})


async def get_auto_replace_status(request: Request) -> JSONResponse:
    state = _auto_replace_state
    return JSONResponse(content={
        "running": state["running"],
        "pending_tasks": state["pending_tasks"],
        "interval": state["interval"],
        "last_check": state["last_check"],
        "logs": state["logs"][-100:],
    })


# --- 自动补货（监控异常禁用凭据 → 触发补货流程）---

_auto_restock_state: dict = {
    "running": False,
    "task": None,
    "app": None,
    "interval": 30,
    "disabled_creds": [],
    "logs": [],
    "warranty_client_ids": set(),
    "last_warranty_refresh": 0.0,
}


def _restock_log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    _auto_restock_state["logs"].append(line)
    if len(_auto_restock_state["logs"]) > _MAX_LOGS:
        _auto_restock_state["logs"] = _auto_restock_state["logs"][-_MAX_LOGS:]
    logger.info(f"[auto-restock] {msg}")


def _refresh_warranty_list(token: str) -> set[str]:
    """拉取所有 paid+completed 订单，收集在保 delivery 中的 client_id"""
    all_orders = []
    page, page_size, total_pages = 1, 20, 1
    while page <= total_pages:
        result = _kiroshop_get(
            f"{KIROSHOP_BASE}/orders?page={page}&page_size={page_size}",
            token, "https://kiroshop.xyz/shop/orders",
        )
        all_orders.extend(result.get("items", []))
        if page == 1:
            t = result.get("total", 0)
            total_pages = math.ceil(t / page_size) if t > 0 else 1
        page += 1

    active = [o for o in all_orders if o.get("status") in ("paid", "completed")]
    client_ids: set[str] = set()
    for order in active:
        oid = order["id"]
        wh = order.get("warranty_hours", 0)
        try:
            detail = _kiroshop_get(
                f"{KIROSHOP_BASE}/orders/{oid}",
                token, "https://kiroshop.xyz/shop/orders",
            )
        except Exception:
            continue
        for d in detail.get("deliveries", []):
            delivered_at = d.get("delivered_at", "")
            is_expired, _ = _check_warranty(delivered_at, wh)
            if is_expired:
                continue
            for acc in d.get("account_data", []):
                cid = acc.get("client_id", "")
                if cid:
                    client_ids.add(cid)
    return client_ids


def _get_abnormal_disabled_creds(app) -> list[dict]:
    """获取 pro/priority 分组中异常禁用的凭据"""
    service = app.state.admin_service
    resp = service.get_all_credentials()
    result = []
    for c in resp.credentials:
        if not c.disabled:
            continue
        if c.disabled_reason not in ("too_many_failures", "quota_exceeded"):
            continue
        if c.group not in ("pro", "priority"):
            continue
        result.append({
            "id": c.id, "email": c.email,
            "group": c.group, "reason": c.disabled_reason,
            "client_id": c.client_id,
        })
    return result


_DEFAULT_IMPORT_REGIONS = ["eu-north-1", "us-east-1"]


async def _import_replaced_credentials(app, token: str, replaced: list[dict]):
    """读取成功补号的 delivery 详情，提取凭据并添加到凭据库"""
    from admin.types import AddCredentialRequest

    service = app.state.admin_service
    total_ok, total_fail = 0, 0

    for item in replaced:
        oid, did = item["order_id"], item["delivery_id"]
        try:
            detail = _kiroshop_get(
                f"{KIROSHOP_BASE}/orders/{oid}",
                token, "https://kiroshop.xyz/shop/orders",
            )
        except Exception as e:
            _restock_log(f"获取订单{oid}详情失败: {e}")
            continue

        # 找到对应 delivery
        target_delivery = None
        for d in detail.get("deliveries", []):
            if d.get("id") == did:
                target_delivery = d
                break
        if not target_delivery:
            _restock_log(f"订单{oid}中未找到发货{did}")
            continue

        for acc in target_delivery.get("account_data", []):
            try:
                cred_json = json.loads(acc.get("account_json", "{}"))
                if isinstance(cred_json, list):
                    cred_json = cred_json[0] if cred_json else {}
                refresh_token = cred_json.get("refreshToken") or cred_json.get("refresh_token", "")
                if not refresh_token:
                    continue
                client_id = (cred_json.get("clientId") or cred_json.get("client_id") or "").strip() or None
                client_secret = (cred_json.get("clientSecret") or cred_json.get("client_secret") or "").strip() or None
                auth_method = "idc" if (client_id and client_secret) else "social"
                specified_region = (acc.get("region") or cred_json.get("region") or "").strip()
                regions = [specified_region] + [r for r in _DEFAULT_IMPORT_REGIONS if r != specified_region] if specified_region else _DEFAULT_IMPORT_REGIONS

                ok = False
                for region in regions:
                    try:
                        req = AddCredentialRequest(
                            refresh_token=refresh_token,
                            auth_method=auth_method,
                            client_id=client_id,
                            client_secret=client_secret,
                            auth_region=region,
                            email=acc.get("email"),
                        )
                        await service.add_credential(req)
                        ok = True
                        break
                    except Exception:
                        continue
                if ok:
                    total_ok += 1
                else:
                    total_fail += 1
            except Exception:
                total_fail += 1

    _restock_log(f"凭据导入完成: 成功 {total_ok}, 失败 {total_fail}")


async def _auto_restock_loop():
    """持续监控异常禁用凭据，仅在保名单内的 client_id 触发补货"""
    state = _auto_restock_state
    app = state["app"]

    _restock_log("自动补货已启动")

    try:
        while state["running"]:
            interval = state["interval"]

            # 刷新在保名单（首次 or 距上次 >= 30 分钟）
            now_ts = time.time()
            if now_ts - state["last_warranty_refresh"] >= 1800:
                try:
                    w_token = _relogin_and_save()
                    ids = _refresh_warranty_list(w_token)
                    state["warranty_client_ids"] = ids
                    state["last_warranty_refresh"] = time.time()
                    _restock_log(f"在保名单已刷新，共 {len(ids)} 个 client_id")
                except Exception as e:
                    _restock_log(f"刷新在保名单失败: {e}")

            try:
                abnormal = _get_abnormal_disabled_creds(app)
            except Exception as e:
                _restock_log(f"获取凭据状态失败: {e}")
                await asyncio.sleep(interval)
                continue

            state["disabled_creds"] = abnormal

            if not abnormal:
                await asyncio.sleep(interval)
                continue

            # 筛选在保名单内的凭据
            warranty_ids = state["warranty_client_ids"]
            warranted = [c for c in abnormal if c.get("client_id") and c["client_id"] in warranty_ids]

            if not warranted:
                await asyncio.sleep(interval)
                continue

            emails = ", ".join(c["email"] or f"#{c['id']}" for c in warranted)
            _restock_log(f"检测到 {len(warranted)} 个在保异常凭据: {emails}")

            # 补货流程已在运行则跳过
            if _auto_replace_state["running"]:
                _restock_log("补货流程已在运行中，跳过")
                await asyncio.sleep(interval)
                continue

            _restock_log("启动补货流程...")
            _auto_replace_state["running"] = True
            _auto_replace_state["app"] = app
            _auto_replace_state["logs"] = []
            _auto_replace_state["pending_tasks"] = []
            _auto_replace_state["replaced_deliveries"] = []
            _auto_replace_state["interval"] = 1
            _auto_replace_state["task"] = asyncio.create_task(_auto_replace_loop())

            # 等待补货完成
            replace_task = _auto_replace_state["task"]
            if replace_task:
                try:
                    await replace_task
                except Exception as e:
                    _restock_log(f"补货流程异常: {e}")

            # 补货完成后：导入成功补号的凭据
            replaced = _auto_replace_state.get("replaced_deliveries", [])
            if replaced:
                _restock_log(f"开始导入 {len(replaced)} 个补号批次的凭据...")
                try:
                    token = _relogin_and_save()
                except Exception as e:
                    _restock_log(f"导入前登录失败: {e}")
                    token = ""
                if token:
                    await _import_replaced_credentials(app, token, replaced)

            # 将触发补货的异常禁用凭据切换为手动禁用
            service = app.state.admin_service
            for c in warranted:
                try:
                    service.set_disabled(c["id"], True)
                    _restock_log(f"凭据 #{c['id']} 已切换为手动禁用")
                except Exception as e:
                    _restock_log(f"切换凭据 #{c['id']} 禁用状态失败: {e}")

            _restock_log("补货流程结束，继续监控...")
            await asyncio.sleep(interval)

    except asyncio.CancelledError:
        _restock_log("自动补货已停止")
    except Exception as e:
        _restock_log(f"自动补货异常: {e}")
    finally:
        state["running"] = False


async def start_auto_restock(request: Request) -> JSONResponse:
    state = _auto_restock_state
    if state["running"]:
        return JSONResponse(content={"success": False, "message": "已在运行中"})

    try:
        body = await request.json()
    except Exception:
        body = {}
    state["interval"] = body.get("interval", 30)
    state["app"] = request.app
    state["running"] = True
    state["logs"] = []
    state["disabled_creds"] = []
    state["warranty_client_ids"] = set()
    state["last_warranty_refresh"] = 0.0
    state["task"] = asyncio.create_task(_auto_restock_loop())
    return JSONResponse(content={"success": True})


async def stop_auto_restock(request: Request) -> JSONResponse:
    state = _auto_restock_state
    if not state["running"]:
        return JSONResponse(content={"success": False, "message": "未在运行"})
    state["running"] = False
    task = state.get("task")
    if task and not task.done():
        task.cancel()
    state["task"] = None
    _restock_log("收到停止指令")
    return JSONResponse(content={"success": True})


async def get_auto_restock_status(request: Request) -> JSONResponse:
    state = _auto_restock_state
    return JSONResponse(content={
        "running": state["running"],
        "disabled_creds": state["disabled_creds"],
        "interval": state["interval"],
        "warranty_count": len(state["warranty_client_ids"]),
        "logs": state["logs"][-100:],
    })


async def refresh_warranty(request: Request) -> JSONResponse:
    """手动刷新在保名单（先重新登录获取新 token）"""
    try:
        token = _relogin_and_save()
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    try:
        ids = _refresh_warranty_list(token)
        state = _auto_restock_state
        state["warranty_client_ids"] = ids
        state["last_warranty_refresh"] = time.time()
        _restock_log(f"手动刷新在保名单，共 {len(ids)} 个 client_id")
        return JSONResponse(content={"warranty_count": len(ids)})
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})
