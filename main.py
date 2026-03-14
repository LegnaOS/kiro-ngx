"""主入口 - 参考 src/main.rs"""

import logging
import os
import sys

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from pathlib import Path

from args import get_args
from config import Config
from http_client import ProxyConfig
from kiro.model.credentials import KiroCredentials, CredentialsConfig
from kiro.token_manager import MultiTokenManager
from kiro.provider import KiroProvider
from anthropic_api.router import create_router_with_provider
from anthropic_api.middleware import AppState, AuthMiddleware, add_cors_middleware
from anthropic_api.converter import configure_converter_limits
from anthropic_api.handlers import configure_request_limits, configure_stream_limits
from admin import AdminService, AdminAuthMiddleware, create_admin_router
from admin.ui_router import create_admin_ui_router
from admin.runtime_log import init_runtime_log_buffer
from plugin_loader import load_plugins, load_public_plugins, get_loaded_plugins
from anthropic_api.message_log import init_message_logger
from token_usage import init_token_usage_tracker
import token_counter

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

init_runtime_log_buffer()


def main():
    # 解析命令行参数
    config_path, credentials_path = get_args()
    config_path = config_path or Config.default_config_path()

    # 加载配置
    try:
        config = Config.load(config_path)
    except Exception as e:
        logger.error("加载配置失败: %s", e)
        sys.exit(1)

    # 加载凭证
    cred_path = credentials_path or "credentials.json"
    try:
        credentials_list, is_multiple_format = CredentialsConfig.load(cred_path)
    except Exception as e:
        logger.error("加载凭证失败: %s", e)
        sys.exit(1)

    # 按优先级排序
    credentials_list.sort(key=lambda c: c.priority)
    logger.info("已加载 %d 个凭据配置", len(credentials_list))

    first_credentials = credentials_list[0] if credentials_list else KiroCredentials()

    # 获取 API Key
    api_key = config.api_key
    if not api_key:
        logger.error("配置文件中未设置 apiKey")
        sys.exit(1)

    # 构建代理配置
    proxy_config = None
    if config.proxy_url:
        proxy_config = ProxyConfig(url=config.proxy_url)
        if config.proxy_username and config.proxy_password:
            proxy_config = proxy_config.with_auth(config.proxy_username, config.proxy_password)
        logger.info("已配置 HTTP 代理: %s", config.proxy_url)

    # 创建 MultiTokenManager 和 KiroProvider
    try:
        token_manager = MultiTokenManager(
            config=config,
            credentials=credentials_list,
            proxy=proxy_config,
            credentials_path=Path(cred_path),
            is_multiple_format=is_multiple_format,
        )
    except Exception as e:
        logger.error("创建 Token 管理器失败: %s", e)
        sys.exit(1)

    kiro_provider = KiroProvider(token_manager=token_manager, proxy=proxy_config)

    token_counter.init_config(token_counter.CountTokensConfig(
        api_url=config.count_tokens_api_url,
        api_key=config.count_tokens_api_key,
        auth_type=config.count_tokens_auth_type,
        proxy=proxy_config,
    ))
    configure_request_limits(
        max_bytes=config.request_max_bytes,
        max_chars=config.request_max_chars,
        context_token_limit=config.request_context_token_limit,
    )
    configure_stream_limits(
        ping_interval_secs=config.stream_ping_interval_secs,
        max_idle_pings=config.stream_max_idle_pings,
        warn_after_idle_pings=config.stream_idle_warn_after_pings,
    )
    configure_converter_limits(
        current_tool_result_max_chars=config.tool_result_current_max_chars,
        current_tool_result_max_lines=config.tool_result_current_max_lines,
        history_tool_result_max_chars=config.tool_result_history_max_chars,
        history_tool_result_max_lines=config.tool_result_history_max_lines,
    )

    # 初始化消息日志
    log_dir = Path(__file__).resolve().parent / "logs"
    init_message_logger(log_dir)

    # 初始化 token 用量追踪
    init_token_usage_tracker(token_manager.cache_dir())

    # 构建 FastAPI 应用
    app = FastAPI()

    # 挂载 Anthropic API 路由
    anthropic_state = AppState(
        api_key=api_key,
        kiro_provider=kiro_provider,
        profile_arn=first_credentials.profile_arn,
    )
    anthropic_router = create_router_with_provider(
        api_key=api_key,
        provider=kiro_provider,
        profile_arn=first_credentials.profile_arn,
    )
    app.include_router(anthropic_router)
    app.add_middleware(AuthMiddleware, state=anthropic_state)
    add_cors_middleware(app)

    # 挂载 Admin API（如果配置了非空的 admin_api_key）
    admin_key = config.admin_api_key
    admin_key_valid = bool(admin_key and admin_key.strip())

    if admin_key and admin_key.strip():
        admin_service = AdminService(token_manager)
        app.state.admin_service = admin_service

        admin_router = create_admin_router()
        admin_app = FastAPI()
        admin_app.include_router(admin_router)
        admin_app.state.admin_service = admin_service

        # 加载插件（路由注册到 admin_app，共享 admin 认证）
        loaded_plugins = load_plugins(admin_app)
        admin_app.state.loaded_plugins = loaded_plugins
        # 插件发现端点
        admin_app.add_api_route("/plugins", get_loaded_plugins, methods=["GET"])

        admin_app.add_middleware(AdminAuthMiddleware, admin_api_key=admin_key)
        app.mount("/api/admin", admin_app)

        public_plugins = load_public_plugins(
            app,
            admin_service=admin_service,
            admin_api_key=admin_key,
        )

        # Admin UI
        admin_ui_router = create_admin_ui_router()
        admin_ui_app = FastAPI()
        admin_ui_app.include_router(admin_ui_router)
        app.mount("/admin", admin_ui_app)

        logger.info("Admin API 已启用")
        if public_plugins:
            logger.info("公共插件路由已启用: %d 个", len(public_plugins))
        logger.info("Admin UI 已启用: /admin")
    elif admin_key is not None:
        logger.warning("admin_api_key 配置为空，Admin API 未启用")

    # 启动日志
    addr = f"{config.host}:{config.port}"
    logger.info("启动 Anthropic API 端点: %s", addr)
    half = len(api_key) // 2
    logger.info("API Key: %s***", api_key[:half])
    logger.info("可用 API:")
    logger.info("  GET  /v1/models")
    logger.info("  POST /v1/messages")
    logger.info("  POST /v1/messages/count_tokens")
    if admin_key_valid:
        logger.info("Admin API:")
        logger.info("  GET  /api/admin/credentials")
        logger.info("  POST /api/admin/credentials/:index/disabled")
        logger.info("  POST /api/admin/credentials/:index/priority")
        logger.info("  POST /api/admin/credentials/:index/reset")
        logger.info("  GET  /api/admin/credentials/:index/balance")
        logger.info("Admin UI:")
        logger.info("  GET  /admin")

    uvicorn.run(app, host=config.host, port=config.port, log_level="info", timeout_keep_alive=120)


if __name__ == "__main__":
    main()
