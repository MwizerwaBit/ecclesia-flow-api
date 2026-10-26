"""HTTP response helpers shared by controllers."""

from fastapi.responses import Response


def attachment_response(data: bytes, mime: str, name: str) -> Response:
    """A file download: always an attachment, never rendered inline by the
    browser, never cached."""
    safe_name = "".join(c for c in name if c.isalnum() or c in "._- ")[:100] or "document"
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )
