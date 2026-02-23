"""Admin API 错误类型 - 参考 src/admin/error.rs"""

from admin.types import AdminErrorResponse


class AdminServiceError(Exception):
    """Admin 服务错误基类"""

    def status_code(self) -> int:
        raise NotImplementedError

    def to_response(self) -> AdminErrorResponse:
        raise NotImplementedError


class NotFoundError(AdminServiceError):
    def __init__(self, id: int):
        self.id = id
        super().__init__(f"凭据不存在: {id}")

    def status_code(self) -> int:
        return 404

    def to_response(self) -> AdminErrorResponse:
        return AdminErrorResponse.not_found(str(self))


class UpstreamError(AdminServiceError):
    def __init__(self, message: str):
        super().__init__(f"上游服务错误: {message}")

    def status_code(self) -> int:
        return 502

    def to_response(self) -> AdminErrorResponse:
        return AdminErrorResponse.api_error(str(self))


class InternalError(AdminServiceError):
    def __init__(self, message: str):
        super().__init__(f"内部错误: {message}")

    def status_code(self) -> int:
        return 500

    def to_response(self) -> AdminErrorResponse:
        return AdminErrorResponse.internal_error(str(self))


class InvalidCredentialError(AdminServiceError):
    def __init__(self, message: str):
        super().__init__(f"凭据无效: {message}")

    def status_code(self) -> int:
        return 400

    def to_response(self) -> AdminErrorResponse:
        return AdminErrorResponse.invalid_request(str(self))
