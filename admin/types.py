"""Admin API 类型定义 - 参考 src/admin/types.rs"""

from dataclasses import dataclass, field
from typing import Optional


# ============ 凭据状态 ============

@dataclass
class CredentialStatusItem:
    """单个凭据的状态信息"""
    id: int = 0
    priority: int = 0
    disabled: bool = False
    failure_count: int = 0
    is_current: bool = False
    expires_at: Optional[str] = None
    auth_method: Optional[str] = None
    has_profile_arn: bool = False
    refresh_token_hash: Optional[str] = None
    email: Optional[str] = None
    success_count: int = 0
    last_used_at: Optional[str] = None
    has_proxy: bool = False
    proxy_url: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "priority": self.priority,
            "disabled": self.disabled,
            "failureCount": self.failure_count,
            "isCurrent": self.is_current,
            "expiresAt": self.expires_at,
            "authMethod": self.auth_method,
            "hasProfileArn": self.has_profile_arn,
            "refreshTokenHash": self.refresh_token_hash,
            "email": self.email,
            "successCount": self.success_count,
            "lastUsedAt": self.last_used_at,
            "hasProxy": self.has_proxy,
        }
        if self.proxy_url is not None:
            d["proxyUrl"] = self.proxy_url
        return d


@dataclass
class CredentialsStatusResponse:
    """所有凭据状态响应"""
    total: int = 0
    available: int = 0
    current_id: int = 0
    credentials: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "available": self.available,
            "currentId": self.current_id,
            "credentials": [c.to_dict() for c in self.credentials],
        }


# ============ 操作请求 ============

@dataclass
class SetDisabledRequest:
    disabled: bool = False


@dataclass
class SetPriorityRequest:
    priority: int = 0


@dataclass
class AddCredentialRequest:
    refresh_token: str = ""
    auth_method: str = "social"
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    priority: int = 0
    region: Optional[str] = None
    auth_region: Optional[str] = None
    api_region: Optional[str] = None
    machine_id: Optional[str] = None
    email: Optional[str] = None
    proxy_url: Optional[str] = None
    proxy_username: Optional[str] = None
    proxy_password: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "AddCredentialRequest":
        return cls(
            refresh_token=data.get("refreshToken", ""),
            auth_method=data.get("authMethod", "social"),
            client_id=data.get("clientId"),
            client_secret=data.get("clientSecret"),
            priority=data.get("priority", 0),
            region=data.get("region"),
            auth_region=data.get("authRegion"),
            api_region=data.get("apiRegion"),
            machine_id=data.get("machineId"),
            email=data.get("email"),
            proxy_url=data.get("proxyUrl"),
            proxy_username=data.get("proxyUsername"),
            proxy_password=data.get("proxyPassword"),
        )


# ============ 余额查询 ============

@dataclass
class BalanceResponse:
    id: int = 0
    subscription_title: Optional[str] = None
    current_usage: float = 0.0
    usage_limit: float = 0.0
    remaining: float = 0.0
    usage_percentage: float = 0.0
    next_reset_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "subscriptionTitle": self.subscription_title,
            "currentUsage": self.current_usage,
            "usageLimit": self.usage_limit,
            "remaining": self.remaining,
            "usagePercentage": self.usage_percentage,
            "nextResetAt": self.next_reset_at,
        }


# ============ 负载均衡配置 ============

@dataclass
class LoadBalancingModeResponse:
    mode: str = "priority"

    def to_dict(self) -> dict:
        return {"mode": self.mode}


@dataclass
class SetLoadBalancingModeRequest:
    mode: str = "priority"

    @classmethod
    def from_dict(cls, data: dict) -> "SetLoadBalancingModeRequest":
        return cls(mode=data.get("mode", "priority"))


# ============ 通用响应 ============

@dataclass
class SuccessResponse:
    success: bool = True
    message: str = ""

    def to_dict(self) -> dict:
        return {"success": self.success, "message": self.message}

    @classmethod
    def new(cls, message: str) -> "SuccessResponse":
        return cls(success=True, message=message)


@dataclass
class AddCredentialResponse:
    success: bool = True
    message: str = ""
    credential_id: int = 0
    email: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "success": self.success,
            "message": self.message,
            "credentialId": self.credential_id,
        }
        if self.email is not None:
            d["email"] = self.email
        return d


class AdminErrorResponse:
    """错误响应"""
    def __init__(self, error_type: str, message: str):
        self.error_type = error_type
        self.message = message

    def to_dict(self) -> dict:
        return {"error": {"type": self.error_type, "message": self.message}}

    @classmethod
    def invalid_request(cls, message: str) -> "AdminErrorResponse":
        return cls("invalid_request", message)

    @classmethod
    def authentication_error(cls) -> "AdminErrorResponse":
        return cls("authentication_error", "Invalid or missing admin API key")

    @classmethod
    def not_found(cls, message: str) -> "AdminErrorResponse":
        return cls("not_found", message)

    @classmethod
    def api_error(cls, message: str) -> "AdminErrorResponse":
        return cls("api_error", message)

    @classmethod
    def internal_error(cls, message: str) -> "AdminErrorResponse":
        return cls("internal_error", message)
