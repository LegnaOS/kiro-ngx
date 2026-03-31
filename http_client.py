"""HTTP 客户端构建 - 参考 src/http_client.rs"""

from dataclasses import dataclass
from typing import Optional

import certifi
import httpx


DEFAULT_LIMITS = httpx.Limits(
    max_keepalive_connections=200,
    max_connections=400,
    keepalive_expiry=120.0,
)
DEFAULT_POOL_TIMEOUT_SECS = 8.0
DEFAULT_CONNECT_TIMEOUT_SECS = 30.0
DEFAULT_WRITE_TIMEOUT_SECS = 30.0
# Transport 层连接重试次数（TCP/TLS 握手失败时自动重试，不消耗应用层重试配额）
TRANSPORT_RETRIES = 2


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
    """构建 httpx AsyncClient，启用 HTTP/2 + transport 层自动重试"""
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

    timeout = httpx.Timeout(
        read=float(timeout_secs),
        connect=min(DEFAULT_CONNECT_TIMEOUT_SECS, float(timeout_secs)),
        write=min(DEFAULT_WRITE_TIMEOUT_SECS, float(timeout_secs)),
        pool=min(DEFAULT_POOL_TIMEOUT_SECS, float(timeout_secs)),
    )

    # Transport 层重试：TCP/TLS 握手失败时自动重试，不消耗应用层重试配额
    transport = httpx.AsyncHTTPTransport(
        retries=TRANSPORT_RETRIES,
        verify=certifi.where(),
        http2=True,
    )

    return httpx.AsyncClient(
        timeout=timeout,
        proxy=proxy_url,
        limits=DEFAULT_LIMITS,
        follow_redirects=True,
        transport=transport,
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

    timeout = httpx.Timeout(
        read=float(timeout_secs),
        connect=min(DEFAULT_CONNECT_TIMEOUT_SECS, float(timeout_secs)),
        write=min(DEFAULT_WRITE_TIMEOUT_SECS, float(timeout_secs)),
        pool=min(DEFAULT_POOL_TIMEOUT_SECS, float(timeout_secs)),
    )

    transport = httpx.HTTPTransport(
        retries=TRANSPORT_RETRIES,
        verify=certifi.where(),
        http2=True,
    )

    return httpx.Client(
        timeout=timeout,
        proxy=proxy_url,
        limits=DEFAULT_LIMITS,
        follow_redirects=True,
        transport=transport,
    )
