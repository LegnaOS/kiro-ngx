"""Kiro OAuth 凭据模型 - 参考 src/kiro/model/credentials.rs"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List

from http_client import ProxyConfig


@dataclass
class KiroCredentials:
    id: Optional[int] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    profile_arn: Optional[str] = None
    expires_at: Optional[str] = None
    auth_method: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    priority: int = 0
    region: Optional[str] = None
    auth_region: Optional[str] = None
    api_region: Optional[str] = None
    machine_id: Optional[str] = None
    email: Optional[str] = None
    subscription_title: Optional[str] = None
    proxy_url: Optional[str] = None
    proxy_username: Optional[str] = None
    proxy_password: Optional[str] = None
    disabled: bool = False

    # JSON key 映射
    _KEY_MAP = {
        "id": "id", "accessToken": "access_token", "refreshToken": "refresh_token",
        "profileArn": "profile_arn", "expiresAt": "expires_at",
        "authMethod": "auth_method", "clientId": "client_id",
        "clientSecret": "client_secret", "priority": "priority",
        "region": "region", "authRegion": "auth_region", "apiRegion": "api_region",
        "machineId": "machine_id", "email": "email",
        "subscriptionTitle": "subscription_title",
        "proxyUrl": "proxy_url", "proxyUsername": "proxy_username",
        "proxyPassword": "proxy_password", "disabled": "disabled",
    }
    _REVERSE_KEY_MAP = {v: k for k, v in _KEY_MAP.items()}

    def canonicalize_auth_method(self):
        """规范化 auth_method: builder-id/iam → idc"""
        if self.auth_method and self.auth_method.lower() in ("builder-id", "iam"):
            self.auth_method = "idc"

    def effective_auth_region(self, config) -> str:
        """获取有效的 Auth Region
        优先级: credential.auth_region > credential.region > config.auth_region > config.region
        """
        return (
            self.auth_region
            or self.region
            or config.effective_auth_region()
        )

    def effective_api_region(self, config) -> str:
        """获取有效的 API Region
        优先级: credential.api_region > config.api_region > config.region
        """
        return self.api_region or config.effective_api_region()

    def effective_proxy(self, global_proxy: Optional[ProxyConfig] = None) -> Optional[ProxyConfig]:
        """获取有效的代理配置
        优先级: credential proxy > global proxy > None
        "direct" 表示显式不使用代理
        """
        if self.proxy_url:
            if self.proxy_url.lower() == "direct":
                return None
            proxy = ProxyConfig(url=self.proxy_url)
            if self.proxy_username and self.proxy_password:
                proxy = proxy.with_auth(self.proxy_username, self.proxy_password)
            return proxy
        return global_proxy

    def supports_opus(self) -> bool:
        """FREE 账户不能使用 Opus 模型"""
        if self.subscription_title:
            return "FREE" not in self.subscription_title.upper()
        return True

    def clone(self) -> "KiroCredentials":
        import copy
        return copy.deepcopy(self)

    @classmethod
    def from_dict(cls, data: dict) -> "KiroCredentials":
        cred = cls()
        for json_key, attr_name in cls._KEY_MAP.items():
            if json_key in data:
                setattr(cred, attr_name, data[json_key])
        return cred

    def to_dict(self) -> dict:
        data = {}
        for attr_name, json_key in self._REVERSE_KEY_MAP.items():
            val = getattr(self, attr_name)
            # priority=0 不序列化（与 Rust serde skip_serializing_if = "is_zero" 一致）
            if attr_name == "priority" and val == 0:
                continue
            # disabled 始终序列化（与 Rust serde 一致，无 skip 注解）
            if attr_name == "disabled":
                data[json_key] = val
                continue
            # None 不序列化
            if val is not None:
                data[json_key] = val
        return data


class CredentialsConfig:
    """凭据配置，支持单凭据和多凭据格式"""

    @staticmethod
    def load(path: str) -> tuple[list[KiroCredentials], bool]:
        """加载凭据文件，返回 (凭据列表, 是否为多凭据格式)"""
        p = Path(path)
        if not p.exists():
            return [], False

        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            # 多凭据格式
            credentials = [KiroCredentials.from_dict(item) for item in data]
            for c in credentials:
                c.canonicalize_auth_method()
            credentials.sort(key=lambda c: c.priority)
            return credentials, True
        elif isinstance(data, dict):
            # 单凭据格式
            cred = KiroCredentials.from_dict(data)
            cred.canonicalize_auth_method()
            return [cred], False
        else:
            return [], False

    @staticmethod
    def save(path: str, credentials: list[KiroCredentials]) -> None:
        """保存凭据到文件（多凭据格式）"""
        data = [cred.to_dict() for cred in credentials]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
