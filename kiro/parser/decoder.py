"""AWS Event Stream 流式解码器 - 参考 src/kiro/parser/decoder.rs"""

import logging
import struct
from enum import Enum, auto
from typing import Iterator, List, Optional

from .error import (
    BufferOverflow, HeaderParseFailed, MessageCrcMismatch, MessageTooLarge,
    MessageTooSmall, ParseError, PreludeCrcMismatch, TooManyErrors,
)
from .frame import Frame, PRELUDE_SIZE, parse_frame

logger = logging.getLogger(__name__)

DEFAULT_MAX_BUFFER_SIZE = 16 * 1024 * 1024
DEFAULT_MAX_ERRORS = 5
DEFAULT_BUFFER_CAPACITY = 8192


class DecoderState(Enum):
    READY = auto()
    PARSING = auto()
    RECOVERING = auto()
    STOPPED = auto()


class EventStreamDecoder:
    """流式事件解码器"""

    def __init__(
        self,
        max_errors: int = DEFAULT_MAX_ERRORS,
        max_buffer_size: int = DEFAULT_MAX_BUFFER_SIZE,
    ):
        self._buffer = bytearray()
        self._state = DecoderState.READY
        self._frames_decoded = 0
        self._error_count = 0
        self._max_errors = max_errors
        self._max_buffer_size = max_buffer_size
        self._bytes_skipped = 0

    def feed(self, data: bytes):
        """向解码器提供数据"""
        new_size = len(self._buffer) + len(data)
        if new_size > self._max_buffer_size:
            raise BufferOverflow(new_size, self._max_buffer_size)

        self._buffer.extend(data)
        if self._state == DecoderState.RECOVERING:
            self._state = DecoderState.READY

    def decode(self) -> Optional[Frame]:
        """尝试解码下一个帧，返回 None 表示数据不足"""
        if self._state == DecoderState.STOPPED:
            raise TooManyErrors(self._error_count, "解码器已停止")

        if not self._buffer:
            self._state = DecoderState.READY
            return None

        self._state = DecoderState.PARSING

        try:
            result = parse_frame(bytes(self._buffer))
        except ParseError as e:
            self._error_count += 1
            error_msg = str(e)

            if self._error_count >= self._max_errors:
                self._state = DecoderState.STOPPED
                logger.error("解码器停止: 连续 %d 次错误，最后错误: %s", self._error_count, error_msg)
                raise TooManyErrors(self._error_count, error_msg) from e

            self._try_recover(e)
            self._state = DecoderState.RECOVERING
            raise

        if result is None:
            self._state = DecoderState.READY
            return None

        frame, consumed = result
        self._buffer = self._buffer[consumed:]
        self._state = DecoderState.READY
        self._frames_decoded += 1
        self._error_count = 0
        return frame

    def decode_all(self) -> List[Frame]:
        """解码所有可用帧"""
        frames = []
        while True:
            if self._state in (DecoderState.STOPPED, DecoderState.RECOVERING):
                break
            try:
                frame = self.decode()
            except ParseError:
                break
            if frame is None:
                break
            frames.append(frame)
        return frames

    def _try_recover(self, error: ParseError):
        """尝试容错恢复"""
        if not self._buffer:
            return

        # Prelude 阶段错误：逐字节跳过
        if isinstance(error, (PreludeCrcMismatch, MessageTooSmall, MessageTooLarge)):
            skipped = self._buffer[0]
            self._buffer = self._buffer[1:]
            self._bytes_skipped += 1
            logger.warning("Prelude 错误恢复: 跳过字节 0x%02x (累计跳过 %d 字节)", skipped, self._bytes_skipped)
            return

        # Data 阶段错误：尝试跳过整帧
        if isinstance(error, (MessageCrcMismatch, HeaderParseFailed)):
            if len(self._buffer) >= PRELUDE_SIZE:
                total_length = struct.unpack_from(">I", self._buffer, 0)[0]
                if 16 <= total_length <= len(self._buffer):
                    logger.warning("Data 错误恢复: 跳过损坏帧 (%d 字节)", total_length)
                    self._buffer = self._buffer[total_length:]
                    self._bytes_skipped += total_length
                    return

        # 回退到逐字节跳过
        skipped = self._buffer[0]
        self._buffer = self._buffer[1:]
        self._bytes_skipped += 1
        logger.warning("通用错误恢复: 跳过字节 0x%02x (累计跳过 %d 字节)", skipped, self._bytes_skipped)

    def reset(self):
        """重置解码器到初始状态"""
        self._buffer.clear()
        self._state = DecoderState.READY
        self._frames_decoded = 0
        self._error_count = 0
        self._bytes_skipped = 0

    @property
    def state(self) -> DecoderState:
        return self._state

    @property
    def is_ready(self) -> bool:
        return self._state == DecoderState.READY

    @property
    def is_stopped(self) -> bool:
        return self._state == DecoderState.STOPPED

    @property
    def is_recovering(self) -> bool:
        return self._state == DecoderState.RECOVERING

    @property
    def frames_decoded(self) -> int:
        return self._frames_decoded

    @property
    def error_count(self) -> int:
        return self._error_count

    @property
    def bytes_skipped(self) -> int:
        return self._bytes_skipped

    @property
    def buffer_len(self) -> int:
        return len(self._buffer)

    def try_resume(self):
        """尝试从 Stopped 状态恢复"""
        if self._state == DecoderState.STOPPED:
            self._error_count = 0
            self._state = DecoderState.READY
            logger.info("解码器从 Stopped 状态恢复")
