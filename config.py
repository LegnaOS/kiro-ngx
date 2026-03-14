"""配置模型 - 参考 src/model/config.rs"""

import json
import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SYSTEM_VERSIONS = ["darwin#24.6.0", "win32#10.0.22631"]


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 8080
    region: str = "us-east-1"
    auth_region: Optional[str] = None
    api_region: Optional[str] = None
    kiro_version: str = "0.10.0"
    machine_id: Optional[str] = None
    api_key: Optional[str] = None
    system_version: str = field(default_factory=lambda: random.choice(SYSTEM_VERSIONS))
    node_version: str = "22.21.1"
    tls_backend: str = "rustls"
    count_tokens_api_url: Optional[str] = None
    count_tokens_api_key: Optional[str] = None
    count_tokens_auth_type: str = "x-api-key"
    request_max_bytes: int = 8 * 1024 * 1024
    request_max_chars: int = 2_000_000
    request_context_token_limit: int = 184_000
    stream_ping_interval_secs: int = 15
    stream_max_idle_pings: int = 4
    stream_idle_warn_after_pings: int = 2
    tool_result_current_max_chars: int = 16_000
    tool_result_current_max_lines: int = 300
    tool_result_history_max_chars: int = 6_000
    tool_result_history_max_lines: int = 120
    proxy_url: Optional[str] = None
    proxy_username: Optional[str] = None
    proxy_password: Optional[str] = None
    admin_api_key: Optional[str] = None
    load_balancing_mode: str = "priority"
    _config_path: Optional[Path] = field(default=None, repr=False)

    # JSON key 映射 (camelCase -> snake_case)
    _KEY_MAP = {
        "host": "host", "port": "port", "region": "region",
        "authRegion": "auth_region", "apiRegion": "api_region",
        "kiroVersion": "kiro_version", "machineId": "machine_id",
        "apiKey": "api_key", "systemVersion": "system_version",
        "nodeVersion": "node_version", "tlsBackend": "tls_backend",
        "countTokensApiUrl": "count_tokens_api_url",
        "countTokensApiKey": "count_tokens_api_key",
        "countTokensAuthType": "count_tokens_auth_type",
        "requestMaxBytes": "request_max_bytes",
        "requestMaxChars": "request_max_chars",
        "requestContextTokenLimit": "request_context_token_limit",
        "streamPingIntervalSecs": "stream_ping_interval_secs",
        "streamMaxIdlePings": "stream_max_idle_pings",
        "streamIdleWarnAfterPings": "stream_idle_warn_after_pings",
        "toolResultCurrentMaxChars": "tool_result_current_max_chars",
        "toolResultCurrentMaxLines": "tool_result_current_max_lines",
        "toolResultHistoryMaxChars": "tool_result_history_max_chars",
        "toolResultHistoryMaxLines": "tool_result_history_max_lines",
        "proxyUrl": "proxy_url", "proxyUsername": "proxy_username",
        "proxyPassword": "proxy_password", "adminApiKey": "admin_api_key",
        "loadBalancingMode": "load_balancing_mode",
    }
    _REVERSE_KEY_MAP = {v: k for k, v in _KEY_MAP.items()}

    def effective_auth_region(self) -> str:
        return self.auth_region or self.region

    def effective_api_region(self) -> str:
        return self.api_region or self.region

    def config_path(self) -> Optional[Path]:
        return self._config_path

    @staticmethod
    def default_config_path() -> str:
        return "config.json"

    @classmethod
    def load(cls, path: str) -> "Config":
        p = Path(path)
        config = cls()
        config._config_path = p
        if not p.exists():
            return config
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        for json_key, attr_name in cls._KEY_MAP.items():
            if json_key in data:
                setattr(config, attr_name, data[json_key])
        return config

    def save(self) -> None:
        if not self._config_path:
            raise RuntimeError("配置文件路径未知，无法保存配置")
        data = {}
        for attr_name, json_key in self._REVERSE_KEY_MAP.items():
            val = getattr(self, attr_name)
            if val is not None:
                data[json_key] = val
        with open(self._config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def to_dict(self) -> dict:
        data = {}
        for attr_name, json_key in self._REVERSE_KEY_MAP.items():
            val = getattr(self, attr_name)
            if val is not None:
                data[json_key] = val
        return data
