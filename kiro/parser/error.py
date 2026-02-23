"""AWS Event Stream 解析错误 - 参考 src/kiro/parser/error.rs"""


class ParseError(Exception):
    """解析错误基类"""
    pass


class IncompleteError(ParseError):
    """数据不足"""
    def __init__(self, needed: int, available: int):
        self.needed = needed
        self.available = available
        super().__init__(f"数据不足: 需要 {needed} 字节, 当前 {available} 字节")


class PreludeCrcMismatch(ParseError):
    """Prelude CRC 校验失败"""
    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual
        super().__init__(f"Prelude CRC 校验失败: 期望 0x{expected:08x}, 实际 0x{actual:08x}")


class MessageCrcMismatch(ParseError):
    """Message CRC 校验失败"""
    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual
        super().__init__(f"Message CRC 校验失败: 期望 0x{expected:08x}, 实际 0x{actual:08x}")


class InvalidHeaderType(ParseError):
    """无效的头部值类型"""
    def __init__(self, type_id: int):
        self.type_id = type_id
        super().__init__(f"无效的头部值类型: {type_id}")


class HeaderParseFailed(ParseError):
    """头部解析错误"""
    pass


class MessageTooLarge(ParseError):
    """消息长度超限"""
    def __init__(self, length: int, max_size: int):
        self.length = length
        self.max_size = max_size
        super().__init__(f"消息长度超限: {length} 字节 (最大 {max_size})")


class MessageTooSmall(ParseError):
    """消息长度过小"""
    def __init__(self, length: int, min_size: int):
        self.length = length
        self.min_size = min_size
        super().__init__(f"消息长度过小: {length} 字节 (最小 {min_size})")


class InvalidMessageType(ParseError):
    """无效的消息类型"""
    def __init__(self, msg_type: str):
        self.msg_type = msg_type
        super().__init__(f"无效的消息类型: {msg_type}")


class TooManyErrors(ParseError):
    """连续错误过多，解码器已停止"""
    def __init__(self, count: int, last_error: str):
        self.count = count
        self.last_error = last_error
        super().__init__(f"连续错误过多 ({count} 次)，解码器已停止: {last_error}")


class BufferOverflow(ParseError):
    """缓冲区溢出"""
    def __init__(self, size: int, max_size: int):
        self.size = size
        self.max_size = max_size
        super().__init__(f"缓冲区溢出: {size} 字节 (最大 {max_size})")
