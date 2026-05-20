from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str
    detail: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
