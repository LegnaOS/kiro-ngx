"""Machine ID 生成 - 参考 src/kiro/machine_id.rs"""

import hashlib
from typing import Optional


def _sha256_hex(input_str: str) -> str:
    return hashlib.sha256(input_str.encode()).hexdigest()


def _normalize_machine_id(machine_id: str) -> Optional[str]:
    """标准化 machineId 格式

    支持：
    - 64 字符十六进制字符串（直接返回）
    - UUID 格式（移除连字符后补齐到 64 字符）
    """
    trimmed = machine_id.strip()

    # 64 字符十六进制
    if len(trimmed) == 64 and all(c in "0123456789abcdefABCDEF" for c in trimmed):
        return trimmed

    # UUID 格式
    without_dashes = trimmed.replace("-", "")
    if len(without_dashes) == 32 and all(c in "0123456789abcdefABCDEF" for c in without_dashes):
        return without_dashes + without_dashes

    return None


def generate_from_credentials(credentials, config) -> Optional[str]:
    """根据凭证信息生成唯一的 Machine ID

    优先级: 凭据级 machineId > config.machineId > refreshToken 生成
    """
    # 凭据级 machineId
    if credentials.machine_id:
        normalized = _normalize_machine_id(credentials.machine_id)
        if normalized:
            return normalized

    # 全局 machineId
    if config.machine_id:
        normalized = _normalize_machine_id(config.machine_id)
        if normalized:
            return normalized

    # 使用 refreshToken 生成
    if credentials.refresh_token:
        return _sha256_hex(f"KotlinNativeAPI/{credentials.refresh_token}")

    return None
