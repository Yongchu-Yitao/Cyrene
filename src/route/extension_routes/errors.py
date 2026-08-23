from fastapi.responses import JSONResponse

from cyrene.extensions.application_service import ExtensionApplicationError


def extension_error(exc: ExtensionApplicationError) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": str(exc)}, status_code=exc.status_code
    )
