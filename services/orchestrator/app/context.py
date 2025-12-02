from contextvars import ContextVar
from typing import Optional

_request_id_ctx_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


def get_request_id() -> Optional[str]:
    return _request_id_ctx_var.get()


def set_request_id(request_id: str) -> None:
    _request_id_ctx_var.set(request_id)
