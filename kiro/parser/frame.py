"""AWS Event Stream 消息帧解析 - 参考 src/kiro/parser/frame.rs"""

import json
import struct
from typing import Any, Optional, Tuple

from .crc import crc32
from .error import (
    HeaderParseFailed, MessageCrcMismatch, MessageTooLarge, MessageTooSmall, PreludeCrcMismatch,
)
from .header import Headers, parse_headers

# Prelude 固定大小 (12 字节)
PRELUDE_SIZE = 12
# 最小消息大小 (Prelude + Message CRC)
MIN_MESSAGE_SIZE = PRELUDE_SIZE + 4
# 最大消息大小限制 (16 MB)
MAX_MESSAGE_SIZE = 16 * 1024 * 1024


class Frame:
    """解析后的消息帧"""

    def __init__(self, headers: Headers, payload: bytes):
        self.headers = headers
        self.payload = payload

    def message_type(self) -> Optional[str]:
        return self.headers.message_type()

    def event_type(self) -> Optional[str]:
        return self.headers.event_type()

    def payload_as_json(self) -> Any:
        return json.loads(self.payload)

    def payload_as_str(self) -> str:
        return self.payload.decode("utf-8", errors="replace")


def parse_frame(buffer: bytes) -> Optional[Tuple[Frame, int]]:
    """尝试从缓冲区解析一个完整的帧

    Returns:
        None - 数据不足
        (Frame, consumed) - 成功解析
    Raises:
        ParseError 子类 - 解析错误
    """
    if len(buffer) < PRELUDE_SIZE:
        return None

    # 读取 prelude
    total_length = struct.unpack_from(">I", buffer, 0)[0]
    header_length = struct.unpack_from(">I", buffer, 4)[0]
    prelude_crc = struct.unpack_from(">I", buffer, 8)[0]

    # 验证消息长度范围
    if total_length < MIN_MESSAGE_SIZE:
        raise MessageTooSmall(total_length, MIN_MESSAGE_SIZE)
    if total_length > MAX_MESSAGE_SIZE:
        raise MessageTooLarge(total_length, MAX_MESSAGE_SIZE)

    # 检查是否有完整的消息
    if len(buffer) < total_length:
        return None

    # 验证 Prelude CRC
    actual_prelude_crc = crc32(buffer[:8])
    if actual_prelude_crc != prelude_crc:
        raise PreludeCrcMismatch(prelude_crc, actual_prelude_crc)

    # 验证 Message CRC
    message_crc = struct.unpack_from(">I", buffer, total_length - 4)[0]
    actual_message_crc = crc32(buffer[:total_length - 4])
    if actual_message_crc != message_crc:
        raise MessageCrcMismatch(message_crc, actual_message_crc)

    # 解析头部
    headers_start = PRELUDE_SIZE
    headers_end = headers_start + header_length
    if headers_end > total_length - 4:
        raise HeaderParseFailed("头部长度超出消息边界")

    headers = parse_headers(buffer[headers_start:headers_end], header_length)

    # 提取 payload
    payload = bytes(buffer[headers_end:total_length - 4])

    return Frame(headers, payload), total_length
