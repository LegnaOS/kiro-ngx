"""CRC32 校验 - 参考 src/kiro/parser/crc.rs"""

import binascii


def crc32(data: bytes) -> int:
    """计算 CRC32 校验和 (ISO-HDLC 标准)"""
    return binascii.crc32(data) & 0xFFFFFFFF
