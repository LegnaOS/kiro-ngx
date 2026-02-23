"""AWS Event Stream 头部解析 - 参考 src/kiro/parser/header.rs"""

import struct
from enum import IntEnum
from typing import Any, Dict, Optional, Union

from .error import IncompleteError, InvalidHeaderType, HeaderParseFailed


class HeaderValueType(IntEnum):
    BOOL_TRUE = 0
    BOOL_FALSE = 1
    BYTE = 2
    SHORT = 3
    INTEGER = 4
    LONG = 5
    BYTE_ARRAY = 6
    STRING = 7
    TIMESTAMP = 8
    UUID = 9


class HeaderValue:
    """头部值，支持 AWS Event Stream 协议定义的所有值类型"""

    def __init__(self, value: Any, value_type: HeaderValueType):
        self.value = value
        self.value_type = value_type

    def as_str(self) -> Optional[str]:
        if self.value_type == HeaderValueType.STRING:
            return self.value
        return None

    def __repr__(self) -> str:
        return f"HeaderValue({self.value_type.name}, {self.value!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HeaderValue):
            return NotImplemented
        return self.value == other.value and self.value_type == other.value_type


class Headers:
    """消息头部集合"""

    def __init__(self):
        self._inner: Dict[str, HeaderValue] = {}

    def insert(self, name: str, value: HeaderValue):
        self._inner[name] = value

    def get(self, name: str) -> Optional[HeaderValue]:
        return self._inner.get(name)

    def get_string(self, name: str) -> Optional[str]:
        v = self.get(name)
        return v.as_str() if v else None

    def message_type(self) -> Optional[str]:
        return self.get_string(":message-type")

    def event_type(self) -> Optional[str]:
        return self.get_string(":event-type")

    def exception_type(self) -> Optional[str]:
        return self.get_string(":exception-type")

    def error_code(self) -> Optional[str]:
        return self.get_string(":error-code")


def _ensure_bytes(data: bytes, offset: int, needed: int):
    if len(data) - offset < needed:
        raise IncompleteError(needed=needed, available=len(data) - offset)


def parse_headers(data: bytes, header_length: int) -> Headers:
    """从字节流解析头部"""
    if len(data) < header_length:
        raise IncompleteError(needed=header_length, available=len(data))

    headers = Headers()
    offset = 0

    while offset < header_length:
        # 名称长度 (1 byte)
        if offset >= len(data):
            break
        name_len = data[offset]
        offset += 1

        if name_len == 0:
            raise HeaderParseFailed("头部名称长度不能为 0")

        # 名称
        _ensure_bytes(data, offset, name_len)
        name = data[offset:offset + name_len].decode("utf-8", errors="replace")
        offset += name_len

        # 值类型 (1 byte)
        _ensure_bytes(data, offset, 1)
        type_byte = data[offset]
        offset += 1

        if type_byte > 9:
            raise InvalidHeaderType(type_byte)
        value_type = HeaderValueType(type_byte)

        # 根据类型解析值
        value, consumed = _parse_header_value(data[offset:], value_type)
        offset += consumed
        headers.insert(name, value)

    return headers


def _parse_header_value(data: bytes, value_type: HeaderValueType) -> tuple:
    """解析头部值，返回 (HeaderValue, consumed_bytes)"""
    if value_type == HeaderValueType.BOOL_TRUE:
        return HeaderValue(True, value_type), 0
    elif value_type == HeaderValueType.BOOL_FALSE:
        return HeaderValue(False, value_type), 0
    elif value_type == HeaderValueType.BYTE:
        _ensure_bytes(data, 0, 1)
        v = struct.unpack_from(">b", data, 0)[0]
        return HeaderValue(v, value_type), 1
    elif value_type == HeaderValueType.SHORT:
        _ensure_bytes(data, 0, 2)
        v = struct.unpack_from(">h", data, 0)[0]
        return HeaderValue(v, value_type), 2
    elif value_type == HeaderValueType.INTEGER:
        _ensure_bytes(data, 0, 4)
        v = struct.unpack_from(">i", data, 0)[0]
        return HeaderValue(v, value_type), 4
    elif value_type in (HeaderValueType.LONG, HeaderValueType.TIMESTAMP):
        _ensure_bytes(data, 0, 8)
        v = struct.unpack_from(">q", data, 0)[0]
        return HeaderValue(v, value_type), 8
    elif value_type == HeaderValueType.BYTE_ARRAY:
        _ensure_bytes(data, 0, 2)
        length = struct.unpack_from(">H", data, 0)[0]
        _ensure_bytes(data, 0, 2 + length)
        v = bytes(data[2:2 + length])
        return HeaderValue(v, value_type), 2 + length
    elif value_type == HeaderValueType.STRING:
        _ensure_bytes(data, 0, 2)
        length = struct.unpack_from(">H", data, 0)[0]
        _ensure_bytes(data, 0, 2 + length)
        v = data[2:2 + length].decode("utf-8", errors="replace")
        return HeaderValue(v, value_type), 2 + length
    elif value_type == HeaderValueType.UUID:
        _ensure_bytes(data, 0, 16)
        v = bytes(data[:16])
        return HeaderValue(v, value_type), 16
    else:
        raise InvalidHeaderType(int(value_type))
