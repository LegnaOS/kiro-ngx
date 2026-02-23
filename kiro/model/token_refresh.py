"""Token 刷新类型 - 参考 src/kiro/model/token_refresh.rs"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class RefreshRequest:
    """Social 认证刷新请求"""
    refresh_token: str

    def to_dict(self) -> dict:
        return {"refreshToken": self.refresh_token}


@dataclass
class RefreshResponse:
    """Social 认证刷新响应"""
    access_token: str
    refresh_token: Optional[str] = None
    profile_arn: Optional[str] = None
    expires_in: Optional[int] = None

    @classmethod
    def from_dict(cls, data: dict) -> "RefreshResponse":
        return cls(
            access_token=data["accessToken"],
            refresh_token=data.get("refreshToken"),
            profile_arn=data.get("profileArn"),
            expires_in=data.get("expiresIn"),
        )


@dataclass
class IdcRefreshRequest:
    """IdC Token 刷新请求 (AWS SSO OIDC)"""
    client_id: str
    client_secret: str
    refresh_token: str
    grant_type: str = "refresh_token"

    def to_dict(self) -> dict:
        return {
            "clientId": self.client_id,
            "clientSecret": self.client_secret,
            "refreshToken": self.refresh_token,
            "grantType": self.grant_type,
        }


@dataclass
class IdcRefreshResponse:
    """IdC Token 刷新响应 (AWS SSO OIDC)"""
    access_token: str
    refresh_token: Optional[str] = None
    expires_in: Optional[int] = None

    @classmethod
    def from_dict(cls, data: dict) -> "IdcRefreshResponse":
        return cls(
            access_token=data["accessToken"],
            refresh_token=data.get("refreshToken"),
            expires_in=data.get("expiresIn"),
        )
