"""Attachment malware scan helpers."""

from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass

from xframe_agent.settings import Settings


@dataclass(frozen=True)
class ScanResult:
    status: str
    detail: str | None = None

    @property
    def is_clean(self) -> bool:
        return self.status == "clean"


async def scan_bytes(data: bytes, settings: Settings) -> ScanResult:
    """Scan bytes with ClamAV when enabled; otherwise mark as clean."""

    if not settings.clamav_enabled:
        return ScanResult(status="clean", detail="clamav_disabled")
    return await asyncio.to_thread(
        _scan_bytes_sync,
        data,
        settings.clamav_host,
        settings.clamav_port,
    )


def _scan_bytes_sync(data: bytes, host: str, port: int) -> ScanResult:
    try:
        with socket.create_connection((host, port), timeout=5) as sock:
            sock.sendall(b"zINSTREAM\0")
            for start in range(0, len(data), 8192):
                chunk = data[start : start + 8192]
                sock.sendall(len(chunk).to_bytes(4, "big") + chunk)
            sock.sendall((0).to_bytes(4, "big"))
            response = sock.recv(4096).decode("utf-8", errors="replace")
    except OSError as exc:
        return ScanResult(status="failed", detail=str(exc))

    if "FOUND" in response:
        return ScanResult(status="infected", detail=response.strip())
    if "OK" in response:
        return ScanResult(status="clean", detail=response.strip())
    return ScanResult(status="failed", detail=response.strip() or "unknown_clamav_response")
