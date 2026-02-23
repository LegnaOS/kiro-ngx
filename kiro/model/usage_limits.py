"""使用额度类型 - 参考 src/kiro/model/usage_limits.rs"""

from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class Bonus:
    current_usage: float = 0.0
    usage_limit: float = 0.0
    status: Optional[str] = None

    def is_active(self) -> bool:
        return self.status == "ACTIVE"

    @classmethod
    def from_dict(cls, data: dict) -> "Bonus":
        return cls(
            current_usage=data.get("currentUsage", 0.0),
            usage_limit=data.get("usageLimit", 0.0),
            status=data.get("status"),
        )


@dataclass
class FreeTrialInfo:
    current_usage: int = 0
    current_usage_with_precision: float = 0.0
    free_trial_expiry: Optional[float] = None
    free_trial_status: Optional[str] = None
    usage_limit: int = 0
    usage_limit_with_precision: float = 0.0

    def is_active(self) -> bool:
        return self.free_trial_status == "ACTIVE"

    @classmethod
    def from_dict(cls, data: dict) -> "FreeTrialInfo":
        return cls(
            current_usage=data.get("currentUsage", 0),
            current_usage_with_precision=data.get("currentUsageWithPrecision", 0.0),
            free_trial_expiry=data.get("freeTrialExpiry"),
            free_trial_status=data.get("freeTrialStatus"),
            usage_limit=data.get("usageLimit", 0),
            usage_limit_with_precision=data.get("usageLimitWithPrecision", 0.0),
        )


@dataclass
class UsageBreakdown:
    current_usage: int = 0
    current_usage_with_precision: float = 0.0
    bonuses: List[Bonus] = field(default_factory=list)
    free_trial_info: Optional[FreeTrialInfo] = None
    next_date_reset: Optional[float] = None
    usage_limit: int = 0
    usage_limit_with_precision: float = 0.0

    @classmethod
    def from_dict(cls, data: dict) -> "UsageBreakdown":
        bonuses = [Bonus.from_dict(b) for b in data.get("bonuses", [])]
        trial = data.get("freeTrialInfo")
        return cls(
            current_usage=data.get("currentUsage", 0),
            current_usage_with_precision=data.get("currentUsageWithPrecision", 0.0),
            bonuses=bonuses,
            free_trial_info=FreeTrialInfo.from_dict(trial) if trial else None,
            next_date_reset=data.get("nextDateReset"),
            usage_limit=data.get("usageLimit", 0),
            usage_limit_with_precision=data.get("usageLimitWithPrecision", 0.0),
        )


@dataclass
class SubscriptionInfo:
    subscription_title: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "SubscriptionInfo":
        return cls(subscription_title=data.get("subscriptionTitle"))


@dataclass
class UsageLimitsResponse:
    next_date_reset: Optional[float] = None
    subscription_info: Optional[SubscriptionInfo] = None
    usage_breakdown_list: List[UsageBreakdown] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "UsageLimitsResponse":
        sub = data.get("subscriptionInfo")
        breakdowns = [UsageBreakdown.from_dict(b) for b in data.get("usageBreakdownList", [])]
        return cls(
            next_date_reset=data.get("nextDateReset"),
            subscription_info=SubscriptionInfo.from_dict(sub) if sub else None,
            usage_breakdown_list=breakdowns,
        )

    def subscription_title(self) -> Optional[str]:
        if self.subscription_info:
            return self.subscription_info.subscription_title
        return None

    def _primary_breakdown(self) -> Optional[UsageBreakdown]:
        return self.usage_breakdown_list[0] if self.usage_breakdown_list else None

    def usage_limit_total(self) -> float:
        bd = self._primary_breakdown()
        if not bd:
            return 0.0
        total = bd.usage_limit_with_precision
        if bd.free_trial_info and bd.free_trial_info.is_active():
            total += bd.free_trial_info.usage_limit_with_precision
        for bonus in bd.bonuses:
            if bonus.is_active():
                total += bonus.usage_limit
        return total

    def current_usage_total(self) -> float:
        bd = self._primary_breakdown()
        if not bd:
            return 0.0
        total = bd.current_usage_with_precision
        if bd.free_trial_info and bd.free_trial_info.is_active():
            total += bd.free_trial_info.current_usage_with_precision
        for bonus in bd.bonuses:
            if bonus.is_active():
                total += bonus.current_usage
        return total
