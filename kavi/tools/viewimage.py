"""ViewImage tool - make an image file visible to a vision-capable model.

A tool's return value becomes a tool-result block, but image content must travel
in a user message. So instead of returning the image, this tool stages an image
content block on the engine (via ``ctx.stage_user_content``); the engine flushes
it as a user message right after the tool-call batch, and the model sees the
actual image on its next step. Requires a multimodal model.
"""

from __future__ import annotations

import base64
from pathlib import Path

from pydantic import BaseModel, Field

from kavi.messages import ImageBlock
from kavi.tools.base import Tool, ToolContext, ToolResult

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
# Providers cap a single image around 5 MB; reject larger to fail fast and cheap.
MAX_BYTES = 5 * 1024 * 1024


class ViewImageInput(BaseModel):
    path: str = Field(description="Path to the image (absolute or relative to cwd).")


def _detect_mime(path: Path, data: bytes) -> str:
    # Trust the file's magic bytes first (an image is what its bytes say it is),
    # then fall back to the extension for formats we don't sniff.
    for sig, mime in _MAGIC:
        if data.startswith(sig):
            return mime
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    # No recognised magic. Only trust the extension if the bytes are not clearly
    # something else (avoids treating a text file named .png as an image).
    ext = path.suffix.lower()
    if ext in _MIME_BY_EXT and data[:1] not in (b"", b"{", b"<") and not data[:5].isascii():
        return _MIME_BY_EXT[ext]
    return ""


class ViewImageTool(Tool):
    name = "ViewImage"
    description = """
    View an image file (PNG, JPEG, GIF, WebP) so you can actually SEE its contents. The
    image is shown to you in the next step - read it there and describe or use it as the
    task needs. Use for screenshots, diagrams, charts, photos, or any image the user points
    you at. Requires a vision-capable model. Read-only.
    """
    InputModel = ViewImageInput
    is_read_only = True

    def permission_subject(self, data: ViewImageInput) -> str:  # type: ignore[override]
        return data.path

    def render_call(self, data: ViewImageInput) -> str:  # type: ignore[override]
        return f"ViewImage {data.path}"

    async def run(self, data: ViewImageInput, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        path = self.resolve_path(ctx.cwd, data.path)
        if not path.exists():
            return ToolResult.error(f"File not found: {path}")
        if path.is_dir():
            return ToolResult.error(f"Path is a directory, not a file: {path}")

        size = path.stat().st_size
        if size > MAX_BYTES:
            return ToolResult.error(
                f"Image too large ({size} bytes, max {MAX_BYTES}). "
                "Resize or compress it first (e.g. via the Bash tool)."
            )
        try:
            raw = path.read_bytes()
        except OSError as exc:
            return ToolResult.error(str(exc))

        mime = _detect_mime(path, raw)
        if not mime:
            return ToolResult.error(
                f"'{path}' does not look like a supported image (PNG, JPEG, GIF, WebP)."
            )

        if ctx.stage_user_content is None:
            return ToolResult.error(
                "ViewImage needs an active agent session to display the image."
            )

        encoded = base64.b64encode(raw).decode("ascii")
        ctx.stage_user_content(ImageBlock(data=encoded, media_type=mime))
        return ToolResult(
            content=(
                f"Loaded image '{path.name}' ({mime}, {size} bytes). It is attached in "
                "the next message - view it there to see its contents."
            ),
            title=f"ViewImage {data.path}",
            display=f"{mime}, {size} bytes",
        )
