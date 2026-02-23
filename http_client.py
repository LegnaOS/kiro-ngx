"""HTTP 客户端构建 - 参考 src/http_client.rs"""

from dataclasses import dataclass
from typing import Optional

import httpx


@dataclass(frozen=True)
class ProxyConfig:
    url: str
    username: Optional[str] = None
    password: Optional[str] = None

    def with_auth(self, username: str, password: str) -> "ProxyConfig":
        return ProxyConfig(url=self.url, username=username, password=password)


def build_client(
    proxy: Optional[ProxyConfig] = None,
    timeout_secs: int = 30,
) -> httpx.AsyncClient:
    """构建 httpx AsyncClient"""
    transport_kwargs = {}
    proxy_url = None

    if proxy:
        auth_prefix = ""
        if proxy.username and proxy.password:
            auth_prefix = f"{proxy.username}:{proxy.password}@"
        # 解析 proxy URL 并插入认证信息
        if auth_prefix and "://" in proxy.url:
            scheme, rest = proxy.url.split("://", 1)
            proxy_url = f"{scheme}://{auth_prefix}{rest}"
        else:
            proxy_url = proxy.url

    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_secs, connect=30.0),
        proxy=proxy_url,
        follow_redirects=True,
        **transport_kwargs,
    )


def build_sync_client(
    proxy: Optional[ProxyConfig] = None,
    timeout_secs: int = 30,
) -> httpx.Client:
    """构建 httpx 同步 Client"""
    proxy_url = None
    if proxy:
        auth_prefix = ""
        if proxy.username and proxy.password:
            auth_prefix = f"{proxy.username}:{proxy.password}@"
        if auth_prefix and "://" in proxy.url:
            scheme, rest = proxy.url.split("://", 1)
            proxy_url = f"{scheme}://{auth_prefix}{rest}"
        else:
            proxy_url = proxy.url

    return httpx.Client(
        timeout=httpx.Timeout(timeout_secs, connect=30.0),
        proxy=proxy_url,
        follow_redirects=True,
    )
