"""Structured errors for PriceFRAME upstream calls."""

from __future__ import annotations


class PriceFrameError(Exception):
    """Base class for PriceFRAME client errors."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class PriceFrameAuthError(PriceFrameError):
    """PriceFRAME rejected or could not verify the user's JWT."""


class PriceFrameForbiddenError(PriceFrameError):
    """PriceFRAME denied the requested action."""


class PriceFrameNotFoundError(PriceFrameError):
    """PriceFRAME resource was not found."""


class PriceFrameResponseError(PriceFrameError):
    """PriceFRAME returned an unexpected non-success response."""


class PriceFrameUnavailableError(PriceFrameError):
    """PriceFRAME could not be reached or repeatedly returned 5xx responses."""
