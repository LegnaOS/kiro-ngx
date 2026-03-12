"""Remote API HTTP 处理器。"""

from fastapi import Request
from fastapi.responses import JSONResponse

from admin.handlers import _error_response
from admin.error import AdminServiceError
from admin.types import AddCredentialRequest, BatchImportRequest


def _normalize_regions(value) -> list[str]:
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return ["eu-north-1", "us-east-1"]


def _parse_batch_import_request(body) -> BatchImportRequest:
    if isinstance(body, list):
        return BatchImportRequest(
            credentials=[AddCredentialRequest.from_dict(item or {}) for item in body],
        )

    if not isinstance(body, dict):
        return BatchImportRequest()

    if "credentials" in body:
        raw_credentials = body.get("credentials") or []
        if not isinstance(raw_credentials, list):
            raw_credentials = [raw_credentials]
        return BatchImportRequest(
            credentials=[AddCredentialRequest.from_dict(item or {}) for item in raw_credentials],
            skip_verify=bool(body.get("skipVerify", True)),
            regions=_normalize_regions(body.get("regions")),
        )

    if "refreshToken" in body:
        return BatchImportRequest(
            credentials=[AddCredentialRequest.from_dict(body)],
            skip_verify=bool(body.get("skipVerify", True)),
            regions=_normalize_regions(body.get("regions")),
        )

    return BatchImportRequest(
        skip_verify=bool(body.get("skipVerify", True)),
        regions=_normalize_regions(body.get("regions")),
    )


async def get_available_credentials(request: Request) -> JSONResponse:
    """GET /credentials/available - 获取可用凭据数量。"""
    service = request.app.state.admin_service
    return JSONResponse(content=service.get_available_credential_counts())


async def batch_import_credentials(request: Request) -> JSONResponse:
    """POST /credentials/batch-import - 服务端批量导入。"""
    service = request.app.state.admin_service
    body = await request.json()
    payload = _parse_batch_import_request(body)
    try:
        response = await service.batch_import_credentials(payload)
    except AdminServiceError as e:
        return _error_response(e)
    return JSONResponse(content=response.to_dict())
