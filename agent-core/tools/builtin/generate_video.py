"""First-party Grok video generation tool backed by the 0x567 API."""

from __future__ import annotations

import base64
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from host_scope import HostScopeError
from image_references import ImageReference
from llm_secrets import IMAGE_GENERATION_KEY_ENV, get_llm_secret
from paths import AgentPaths, PathError
from tools.schema import ToolErrorCode, ToolResult, tool_fail, tool_ok

TOOL_NAME = "generate_video"
API_KEY_ENV = IMAGE_GENERATION_KEY_ENV
API_ROOT = "https://api-us.0x567.com"
GENERATE_URL = f"{API_ROOT}/v1/videos/generations"
DEFAULT_MODEL = "grok-imagine-video-1.5"
ALLOWED_MODELS = frozenset({"grok-imagine-video-1.5", "grok-imagine-video"})
DEFAULT_DURATION = 5
DEFAULT_RESOLUTION = "480p"
DEFAULT_ASPECT_RATIO = "16:9"
REQUEST_TIMEOUT_SECONDS = 30.0
POLL_TIMEOUT_SECONDS = 180.0
POLL_INTERVAL_SECONDS = 2.0
MAX_PROMPT_CHARS = 10_000
MAX_VIDEO_BYTES = 500 * 1024 * 1024
MAX_REFERENCE_IMAGE_BYTES = 50 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,1024}$")
_ASPECT_RATIO_RE = re.compile(r"^(\d{1,2}):(\d{1,2})$")


class _VideoToolError(ValueError):
    def __init__(self, message: str, *, code: str = ToolErrorCode.VALIDATION_ERROR) -> None:
        super().__init__(message)
        self.code = str(code)


def run(
    arguments: dict[str, Any],
    *,
    paths: AgentPaths,
    prepared_reference: ImageReference | None = None,
    reference_image_path: str | None = None,
    project_root: str = "",
    conversation_id: str = "",
) -> ToolResult:
    """Generate and save a video, or resume polling an existing request_id.

    Generation submission is intentionally never retried: the provider may bill
    a request even when its response is lost. A known request_id is returned on
    polling timeout so callers can resume without submitting another generation.
    """
    started = time.perf_counter()
    request_id: str | None = None
    try:
        if prepared_reference is not None and reference_image_path is not None:
            raise _VideoToolError("provide only one video reference image source")
        request_id_arg = arguments.get("request_id")
        is_resume = request_id_arg is not None
        if is_resume and (prepared_reference is not None or reference_image_path is not None):
            raise _VideoToolError("reference images cannot be combined with request_id polling")
        if is_resume:
            if not isinstance(request_id_arg, str) or not _REQUEST_ID_RE.fullmatch(request_id_arg):
                raise _VideoToolError("request_id must contain only letters, digits, '_' or '-' and be at most 1024 characters")
            request_id = request_id_arg
            model = duration = resolution = aspect_ratio = None
            prompt = ""
        else:
            prompt, model, duration, resolution, aspect_ratio = _validate_arguments(arguments)

        api_key = _resolve_api_key(paths)
        if not api_key:
            raise _VideoToolError(f"{API_KEY_ENV} is not configured", code=ToolErrorCode.MISSING_API_KEY)
        # Read and validate local reference bytes before any billable submission.
        image_data_url = _read_reference_data_url(
            paths,
            prepared_reference=prepared_reference,
            reference_image_path=reference_image_path,
        ) if request_id is None else None
        headers = {"Authorization": f"Bearer {api_key}"}
        output_path = _output_path(paths, project_root=project_root, conversation_id=conversation_id)
        # Resolve and create the permitted destination before a potentially billable request.
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            raise _VideoToolError("generated video destination already exists; refusing to overwrite")
        if request_id is None:
            # Do not retry this POST. If it times out before a request_id is
            # received, the user must check provider-side request history.
            response = httpx.post(
                GENERATE_URL,
                headers={**headers, "Content-Type": "application/json"},
                json={
                    **{
                        "model": model,
                        "prompt": prompt,
                        "duration": duration,
                        "resolution": resolution,
                        "aspect_ratio": aspect_ratio,
                    },
                    **({"image": {"url": image_data_url}} if image_data_url is not None else {}),
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code >= 400:
                return tool_fail(TOOL_NAME, "http_error", f"video API request failed with HTTP {response.status_code}", duration_ms=_elapsed_ms(started), details={"status_code": response.status_code})
            payload = _json_object(response, "video API")
            candidate = payload.get("request_id")
            if not isinstance(candidate, str) or not _REQUEST_ID_RE.fullmatch(candidate):
                raise _VideoToolError("video API response did not contain a valid request_id")
            request_id = candidate

        video_url = _poll_until_done(request_id, headers)
        video_size = _download_video(video_url, output_path)
        relative_path = paths.to_agent_relative(output_path)
        result_data = {
            "path": relative_path,
            "display_paths": [relative_path],
            "request_id": request_id,
            "bytes": video_size,
            "mime_type": "video/mp4",
        }
        if not is_resume:
            result_data.update({"model": model, "duration": duration, "resolution": resolution, "aspect_ratio": aspect_ratio})
        return tool_ok(
            TOOL_NAME,
            result_data,
            output_path=relative_path,
            duration_ms=_elapsed_ms(started),
        )
    except _VideoToolError as exc:
        details = {"request_id": request_id, "resume_with_request_id": request_id} if request_id else None
        return tool_fail(TOOL_NAME, exc.code, str(exc), duration_ms=_elapsed_ms(started), details=details)
    except httpx.TimeoutException:
        message = "video generation submission timed out; it was not retried; check provider request history before submitting again" if request_id is None else "video API polling/download timed out; resume with the returned request_id"
        details = {"request_id": request_id, "resume_with_request_id": request_id} if request_id else None
        return tool_fail(TOOL_NAME, "timeout", message, duration_ms=_elapsed_ms(started), details=details)
    except httpx.HTTPError as exc:
        message = "video API submission failed; it was not retried; verify provider request history before submitting again" if request_id is None else "video API polling/download failed"
        details = {"request_id": request_id, "resume_with_request_id": request_id} if request_id else None
        return tool_fail(TOOL_NAME, "network_error", f"{message} ({type(exc).__name__})", duration_ms=_elapsed_ms(started), details=details)
    except (OSError, PathError) as exc:
        details = {"request_id": request_id} if request_id else None
        return tool_fail(TOOL_NAME, "output_write_failed", f"could not safely write video file ({type(exc).__name__})", duration_ms=_elapsed_ms(started), details=details)


def _validate_arguments(arguments: dict[str, Any]) -> tuple[str, str, int, str, str]:
    prompt = arguments.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise _VideoToolError("prompt is required")
    prompt = prompt.strip()
    if len(prompt) > MAX_PROMPT_CHARS:
        raise _VideoToolError(f"prompt must be {MAX_PROMPT_CHARS} characters or fewer")
    model = arguments.get("model", DEFAULT_MODEL)
    if not isinstance(model, str) or not model.strip() or len(model) > 200:
        raise _VideoToolError("model must be a non-empty model identifier")
    if model.strip() not in ALLOWED_MODELS:
        raise _VideoToolError("model must be grok-imagine-video-1.5 or grok-imagine-video")
    duration = arguments.get("duration", DEFAULT_DURATION)
    if type(duration) is not int or not 1 <= duration <= 15:
        raise _VideoToolError("duration must be an integer from 1 to 15 seconds")
    resolution = arguments.get("resolution", DEFAULT_RESOLUTION)
    if not isinstance(resolution, str) or resolution not in {"480p", "720p", "1080p"}:
        raise _VideoToolError("resolution must be 480p, 720p, or 1080p")
    aspect_ratio = arguments.get("aspect_ratio", DEFAULT_ASPECT_RATIO)
    match = _ASPECT_RATIO_RE.fullmatch(aspect_ratio) if isinstance(aspect_ratio, str) else None
    if not match or int(match.group(1)) == 0 or int(match.group(2)) == 0:
        raise _VideoToolError("aspect_ratio must be a ratio such as 16:9")
    if max(int(match.group(1)), int(match.group(2))) > 3 * min(int(match.group(1)), int(match.group(2))):
        raise _VideoToolError("aspect_ratio must be between 1:3 and 3:1")
    return prompt, model.strip(), duration, resolution, aspect_ratio


def _resolve_api_key(paths: AgentPaths) -> str | None:
    from_environment = os.environ.get(API_KEY_ENV, "").strip()
    if from_environment:
        return from_environment
    stored = get_llm_secret(API_KEY_ENV, paths)
    return stored.strip() if stored and stored.strip() else None



def _read_reference_data_url(
    paths: AgentPaths,
    *,
    prepared_reference: ImageReference | None,
    reference_image_path: str | None,
) -> str | None:
    if prepared_reference is None and reference_image_path is None:
        return None
    try:
        if prepared_reference is not None:
            from tools.builtin.generate_image import _read_reference

            _, image_bytes, mime_type = _read_reference(prepared_reference, paths)
            if mime_type != "image/png":
                raise _VideoToolError("video reference image must be PNG")
        else:
            if not isinstance(reference_image_path, str) or not reference_image_path.strip():
                raise _VideoToolError("reference_image_path must be a permitted agent path")
            resolved = paths.resolve_under_agent(reference_image_path, must_exist=True).resolve(strict=True)
            if not resolved.is_file():
                raise _VideoToolError("video reference image is not an available file")
            if resolved.stat().st_size <= 0 or resolved.stat().st_size >= MAX_REFERENCE_IMAGE_BYTES:
                raise _VideoToolError("video reference image must be non-empty and smaller than 50 MB")
            image_bytes = resolved.read_bytes()
            if not image_bytes or len(image_bytes) >= MAX_REFERENCE_IMAGE_BYTES:
                raise _VideoToolError("video reference image must be non-empty and smaller than 50 MB")
        if not image_bytes.startswith(PNG_SIGNATURE):
            raise _VideoToolError("video reference image must contain PNG data")
        return "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")
    except _VideoToolError:
        raise
    except (OSError, PathError, HostScopeError, ValueError) as exc:
        # Avoid returning path values or image/parser details to logs or events.
        raise _VideoToolError("video reference image is unavailable or outside the permitted path scope") from exc


def _poll_until_done(request_id: str, headers: dict[str, str]) -> str:
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    poll_url = f"{API_ROOT}/v1/videos/{request_id}"
    while True:
        response = httpx.get(poll_url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        if response.status_code >= 400:
            raise _VideoToolError(f"video status request failed with HTTP {response.status_code}", code="http_error")
        payload = _json_object(response, "video status")
        status = payload.get("status")
        if status == "done":
            video = payload.get("video")
            url = video.get("url") if isinstance(video, dict) else payload.get("url")
            return _validate_video_url(url)
        if status in {"failed", "error", "cancelled"}:
            raise _VideoToolError("video generation failed at provider", code="provider_error")
        if status not in {"queued", "pending", "processing", "running", "in_progress"}:
            raise _VideoToolError("video status response has an unsupported status")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _VideoToolError("video generation is still processing; resume with request_id", code="timeout")
        time.sleep(min(POLL_INTERVAL_SECONDS, remaining))


def _validate_video_url(raw_url: Any) -> str:
    if not isinstance(raw_url, str) or len(raw_url) > 4096:
        raise _VideoToolError("video API returned an invalid video URL")
    try:
        parsed = urlsplit(raw_url)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise _VideoToolError("video API returned an invalid video URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or not (hostname == "0x567.com" or hostname.endswith(".0x567.com"))
    ):
        raise _VideoToolError("video URL must use HTTPS on a 0x567.com host")
    return raw_url


def _download_video(url: str, output_path: Path) -> int:
    """Stream a bounded MP4 directly to an exclusive destination file."""
    created = False
    try:
        with httpx.stream("GET", url, timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=False) as response:
            if response.status_code >= 400:
                raise _VideoToolError(f"video download failed with HTTP {response.status_code}", code="http_error")
            with output_path.open("xb") as handle:
                created = True
                total = 0
                header = bytearray()
                for chunk in response.iter_bytes():
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_VIDEO_BYTES:
                        raise _VideoToolError("video file exceeds the 500 MB safety limit")
                    if len(header) < 12:
                        header.extend(chunk[: 12 - len(header)])
                    handle.write(chunk)
                if len(header) < 12 or header[4:8] != b"ftyp":
                    raise _VideoToolError("video download is not a valid MP4 file")
        return total
    except Exception:
        if created:
            output_path.unlink(missing_ok=True)
        raise


def _json_object(response: httpx.Response, label: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except (ValueError, TypeError) as exc:
        raise _VideoToolError(f"{label} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise _VideoToolError(f"{label} returned an invalid response")
    return payload


def _output_path(paths: AgentPaths, *, project_root: str, conversation_id: str) -> Path:
    if project_root.strip():
        project = paths.resolve_under_agent_for_write(project_root.strip())
        try:
            project.resolve().relative_to(paths.workspace.resolve())
        except ValueError as exc:
            raise _VideoToolError("project video output must stay under workspace") from exc
        base = project / "artifacts" / "generated-videos"
    else:
        safe_conversation = re.sub(r"[^A-Za-z0-9._-]", "_", conversation_id.strip()) or "unbound"
        if safe_conversation in {".", ".."}:
            safe_conversation = "unbound"
        base = paths.workspace / "_drops" / safe_conversation / "generated-videos"
    target = base / f"video-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}.mp4"
    try:
        return paths.resolve_under_agent_for_write(str(target))
    except PathError as exc:
        raise _VideoToolError("video output path is outside the permitted workspace") from exc


def write_video_exclusive(path: Path, video_bytes: bytes) -> None:
    """Write MP4 bytes using exclusive creation; never replace an existing file."""
    if path.suffix.casefold() != ".mp4" or not path.is_absolute():
        raise ValueError("output must be an absolute .mp4 path")
    if not isinstance(video_bytes, bytes) or len(video_bytes) < 12 or video_bytes[4:8] != b"ftyp":
        raise ValueError("only valid MP4 bytes can be written")
    created = False
    try:
        with path.open("xb") as handle:
            created = True
            handle.write(video_bytes)
    except Exception:
        if created:
            path.unlink(missing_ok=True)
        raise


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))

