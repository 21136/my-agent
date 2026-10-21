"""First-party image generation/edit tool backed by the 0x567 image API."""

from __future__ import annotations

import base64
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

import httpx

from host_scope import HostScopeError
from image_references import ImageReference
from llm_secrets import IMAGE_GENERATION_KEY_ENV, get_llm_secret
from paths import AgentPaths, PathError
from tools.schema import ToolErrorCode, ToolResult, tool_fail, tool_ok

TOOL_NAME = "generate_image"
API_KEY_ENV = IMAGE_GENERATION_KEY_ENV
IMAGE_MODELS = frozenset({"gpt-image-2.5-flare", "gpt-image-2.5-sunburst"})
DEFAULT_MODEL = "gpt-image-2.5-flare"
DEFAULT_SIZE = "1024x1024"
DEFAULT_QUALITY = "low"
GENERATE_URL = "https://api-cdn.0x567.com/v1/images/generations"
EDIT_URL = "https://api-cdn.0x567.com/v1/images/edits"
REQUEST_TIMEOUT_SECONDS = 180.0
MAX_PROMPT_CHARS = 10_000
MAX_REFERENCE_IMAGE_BYTES = 50 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8\xff"
_SIZE_RE = re.compile(r"^(\d+)x(\d+)$")


class _ImageToolError(ValueError):
    def __init__(self, message: str, *, code: str = ToolErrorCode.VALIDATION_ERROR) -> None:
        super().__init__(message)
        self.code = str(code)


def run(
    arguments: dict[str, Any],
    *,
    paths: AgentPaths,
    prepared_reference: ImageReference | None = None,
    project_root: str = "",
    conversation_id: str = "",
) -> ToolResult:
    """Generate or edit one PNG, after ToolExecutor has obtained confirmation."""
    started = time.perf_counter()
    try:
        prompt, model, size, quality = _validate_arguments(arguments)
        api_key = _resolve_api_key(paths)
        if not api_key:
            raise _ImageToolError(
                f"{API_KEY_ENV} is not configured",
                code=ToolErrorCode.MISSING_API_KEY,
            )
        reference = _read_reference(prepared_reference, paths) if prepared_reference else None
        response = _post_image_request(
            prompt=prompt,
            model=model,
            size=size,
            quality=quality,
            api_key=api_key,
            reference=reference,
        )
        if response.status_code >= 400:
            return tool_fail(
                TOOL_NAME,
                "http_error",
                f"image API request failed with HTTP {response.status_code}",
                duration_ms=_elapsed_ms(started),
                details={"status_code": response.status_code},
            )
        image_bytes = _decode_png_response(response)
        output_path = _output_path(
            paths,
            project_root=project_root,
            conversation_id=conversation_id,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_png_exclusive(output_path, image_bytes)
        relative_path = paths.to_agent_relative(output_path)
        return tool_ok(
            TOOL_NAME,
            {
                "path": relative_path,
                "display_paths": [relative_path],
                "model": model,
                "bytes": len(image_bytes),
                "used_reference_image": reference is not None,
                "reference_filename": prepared_reference.filename if prepared_reference else None,
            },
            output_path=relative_path,
            duration_ms=_elapsed_ms(started),
        )
    except _ImageToolError as exc:
        return tool_fail(
            TOOL_NAME,
            exc.code,
            str(exc),
            duration_ms=_elapsed_ms(started),
        )
    except httpx.TimeoutException:
        return tool_fail(
            TOOL_NAME,
            "timeout",
            "image API request timed out",
            duration_ms=_elapsed_ms(started),
        )
    except httpx.HTTPError as exc:
        return tool_fail(
            TOOL_NAME,
            "network_error",
            f"image API network request failed ({type(exc).__name__})",
            duration_ms=_elapsed_ms(started),
        )
    except (OSError, PathError) as exc:
        return tool_fail(
            TOOL_NAME,
            "output_write_failed",
            f"could not safely read/write image file ({type(exc).__name__})",
            duration_ms=_elapsed_ms(started),
        )


def _validate_arguments(arguments: dict[str, Any]) -> tuple[str, str, str, str]:
    prompt = arguments.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise _ImageToolError("prompt is required")
    prompt = prompt.strip()
    if len(prompt) > MAX_PROMPT_CHARS:
        raise _ImageToolError(f"prompt must be {MAX_PROMPT_CHARS} characters or fewer")

    model = arguments.get("model", DEFAULT_MODEL)
    if not isinstance(model, str) or model not in IMAGE_MODELS:
        raise _ImageToolError("model must be a supported 0x567 GPT Image model")

    size = arguments.get("size", DEFAULT_SIZE)
    if not isinstance(size, str):
        raise _ImageToolError("size must be auto or WIDTHxHEIGHT")
    size = _validate_size(size)

    quality = arguments.get("quality", DEFAULT_QUALITY)
    if not isinstance(quality, str) or quality not in {"low", "medium", "high", "auto"}:
        raise _ImageToolError("quality must be low, medium, high, or auto")
    return prompt, model, size, quality


def _validate_size(size: str) -> str:
    if size == "auto":
        return size
    match = _SIZE_RE.fullmatch(size)
    if not match:
        raise _ImageToolError("size must be auto or WIDTHxHEIGHT")
    width, height = (int(match.group(1)), int(match.group(2)))
    if width <= 0 or height <= 0 or width % 16 or height % 16:
        raise _ImageToolError("custom size dimensions must be positive multiples of 16")
    long_edge = max(width, height)
    short_edge = min(width, height)
    if long_edge > 3840 or short_edge > 2160 or width * height > 3840 * 2160:
        raise _ImageToolError("size exceeds the maximum supported resolution of 3840x2160")
    if long_edge > short_edge * 3:
        raise _ImageToolError("size aspect ratio must be between 1:3 and 3:1")
    return f"{width}x{height}"


def _resolve_api_key(paths: AgentPaths) -> str | None:
    from_environment = os.environ.get(API_KEY_ENV, "").strip()
    if from_environment:
        return from_environment
    stored = get_llm_secret(API_KEY_ENV, paths)
    return stored.strip() if stored and stored.strip() else None


def _read_reference(
    reference: ImageReference,
    paths: AgentPaths,
) -> tuple[str, bytes, str]:
    try:
        resolved = paths.resolve_under_agent(reference.source_ref, must_exist=True).resolve(strict=True)
        expected = reference.path.resolve(strict=True)
        if resolved != expected:
            raise _ImageToolError("selected reference image changed after selection")
        if not reference.source_ref.casefold().startswith("host:"):
            resolved.relative_to(paths.agent_root.resolve())
    except (OSError, PathError, HostScopeError, ValueError) as exc:
        raise _ImageToolError("selected reference image is no longer available") from exc
    if not resolved.is_file():
        raise _ImageToolError("selected reference image is no longer available")
    if resolved.stat().st_size <= 0 or resolved.stat().st_size >= MAX_REFERENCE_IMAGE_BYTES:
        raise _ImageToolError("reference image must be non-empty and smaller than 50 MB")

    image_bytes = resolved.read_bytes()
    if not image_bytes or len(image_bytes) >= MAX_REFERENCE_IMAGE_BYTES:
        raise _ImageToolError("reference image must be non-empty and smaller than 50 MB")
    mime_type = _detect_reference_mime(image_bytes)
    if mime_type is None or mime_type != reference.mime_type:
        raise _ImageToolError("reference image contents do not match a supported PNG/JPEG/WebP type")
    filename = Path(reference.filename).name
    if not filename:
        raise _ImageToolError("reference image filename is invalid")
    return filename, image_bytes, mime_type


def _detect_reference_mime(image_bytes: bytes) -> str | None:
    if image_bytes.startswith(PNG_SIGNATURE):
        return "image/png"
    if image_bytes.startswith(_JPEG_SIGNATURE):
        return "image/jpeg"
    if (
        len(image_bytes) >= 12
        and image_bytes[:4] == b"RIFF"
        and image_bytes[8:12] == b"WEBP"
    ):
        return "image/webp"
    return None


def _post_image_request(
    *,
    prompt: str,
    model: str,
    size: str,
    quality: str,
    api_key: str,
    reference: tuple[str, bytes, str] | None,
) -> httpx.Response:
    headers = {"Authorization": f"Bearer {api_key}"}
    if reference is None:
        return httpx.post(
            GENERATE_URL,
            headers={**headers, "Content-Type": "application/json"},
            json={
                "model": model,
                "prompt": prompt,
                "size": size,
                "quality": quality,
                "n": 1,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

    filename, image_bytes, mime_type = reference
    return httpx.post(
        EDIT_URL,
        headers=headers,
        data={
            "model": model,
            "prompt": prompt,
            "size": size,
            "quality": quality,
            "n": "1",
        },
        files=[("image[]", (filename, image_bytes, mime_type))],
        timeout=REQUEST_TIMEOUT_SECONDS,
    )


def _decode_png_response(response: httpx.Response) -> bytes:
    try:
        payload = response.json()
    except (ValueError, TypeError) as exc:
        raise _ImageToolError("image API returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise _ImageToolError("image API returned an invalid response")
    data = payload.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise _ImageToolError("image API response did not contain an image")
    encoded = data[0].get("b64_json")
    if not isinstance(encoded, str) or not encoded:
        raise _ImageToolError("image API response did not contain PNG data")
    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise _ImageToolError("image API returned malformed base64 data") from exc
    if len(image_bytes) < len(PNG_SIGNATURE) or not image_bytes.startswith(PNG_SIGNATURE):
        raise _ImageToolError("image API response is not a valid PNG")
    return image_bytes


def _output_path(
    paths: AgentPaths,
    *,
    project_root: str,
    conversation_id: str,
) -> Path:
    if project_root.strip():
        project = paths.resolve_under_agent_for_write(project_root.strip())
        try:
            project.resolve().relative_to(paths.workspace.resolve())
        except ValueError as exc:
            raise _ImageToolError("project image output must stay under workspace") from exc
        base = project / "artifacts" / "generated-images"
    else:
        safe_conversation = re.sub(r"[^A-Za-z0-9._-]", "_", conversation_id.strip()) or "unbound"
        if safe_conversation in {".", ".."}:
            safe_conversation = "unbound"
        base = paths.workspace / "_drops" / safe_conversation / "generated-images"
    target = base / f"image-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}.png"
    try:
        return paths.resolve_under_agent_for_write(str(target))
    except PathError as exc:
        raise _ImageToolError("image output path is outside the permitted workspace") from exc


def write_png_exclusive(path: Path, image_bytes: bytes) -> None:
    """Write a valid PNG with exclusive creation; never replace an existing file."""
    if path.suffix.casefold() != ".png" or not path.is_absolute():
        raise ValueError("output must be an absolute .png path")
    if not isinstance(image_bytes, bytes) or not image_bytes.startswith(PNG_SIGNATURE):
        raise ValueError("only valid PNG bytes can be written")
    created = False
    try:
        with path.open("xb") as handle:
            created = True
            handle.write(image_bytes)
    except Exception:
        if created:
            path.unlink(missing_ok=True)
        raise


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))

