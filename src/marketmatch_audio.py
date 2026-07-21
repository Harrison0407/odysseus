"""Bounded, local-only media inspection and canonical WAV conversion."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import json
import math
import os
from pathlib import Path
import shutil
import stat
import tempfile
import threading
import time
from typing import NoReturn

from src.marketmatch_canonical_wav import BYTE_RATE


DEFAULT_MAX_DURATION_SECONDS = 21_600
FFPROBE_TIMEOUT_HARD_MAX_SECONDS = 300.0
FFMPEG_TIMEOUT_HARD_MAX_SECONDS = 7_200.0
WAV_FRAMING_OVERHEAD_BYTES = 4_096
PROBE_OUTPUT_LIMIT_BYTES = 64 * 1024
MAX_MEDIA_STREAMS = 4
SUPPORTED_FORMAT_NAMES = frozenset({
    "aac", "caf", "flac", "mov,mp4,m4a,3gp,3g2,mj2", "mp3", "ogg", "wav",
    "matroska,webm",
})
SUPPORTED_AUDIO_CODECS = frozenset({
    "aac", "alac", "flac", "mp3", "opus", "pcm_f32be", "pcm_f32le",
    "pcm_f64be", "pcm_f64le", "pcm_s16be", "pcm_s16le", "pcm_s24be",
    "pcm_s24le", "pcm_s32be", "pcm_s32le", "pcm_s8", "pcm_u8", "vorbis",
})


def _positive_number_env(name: str, default: float, *, maximum: float | None = None) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive number")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} exceeds its hard maximum")
    return value


def _duration_env() -> float:
    value = _positive_number_env(
        "MARKETMATCH_AUDIO_MAX_DURATION_SECONDS", DEFAULT_MAX_DURATION_SECONDS
    )
    if value > DEFAULT_MAX_DURATION_SECONDS:
        raise ValueError("MARKETMATCH_AUDIO_MAX_DURATION_SECONDS may not exceed 21600")
    return value


MARKETMATCH_AUDIO_MAX_DURATION_SECONDS = _duration_env()
MARKETMATCH_FFPROBE_TIMEOUT_SECONDS = _positive_number_env(
    "MARKETMATCH_FFPROBE_TIMEOUT_SECONDS", 30.0,
    maximum=FFPROBE_TIMEOUT_HARD_MAX_SECONDS,
)
MARKETMATCH_FFMPEG_TIMEOUT_SECONDS = _positive_number_env(
    "MARKETMATCH_FFMPEG_TIMEOUT_SECONDS", 7_200.0,
    maximum=FFMPEG_TIMEOUT_HARD_MAX_SECONDS,
)


class MarketMatchAudioCode(str, Enum):
    FFMPEG_UNAVAILABLE = "FFMPEG_UNAVAILABLE"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    MALFORMED_AUDIO = "MALFORMED_AUDIO"
    NO_AUDIO_STREAM = "NO_AUDIO_STREAM"
    EXCESSIVE_STREAMS = "EXCESSIVE_STREAMS"
    EXTERNAL_MEDIA_REJECTED = "EXTERNAL_MEDIA_REJECTED"
    DURATION_LIMIT_EXCEEDED = "DURATION_LIMIT_EXCEEDED"
    DECODED_OUTPUT_LIMIT_EXCEEDED = "DECODED_OUTPUT_LIMIT_EXCEEDED"
    CONVERSION_FAILED = "CONVERSION_FAILED"
    AUDIO_PROBE_TIMEOUT = "AUDIO_PROBE_TIMEOUT"
    CONVERSION_TIMEOUT = "CONVERSION_TIMEOUT"


class MarketMatchAudioError(Exception):
    def __init__(self, code: MarketMatchAudioCode):
        if type(code) is not MarketMatchAudioCode:
            code = MarketMatchAudioCode.CONVERSION_FAILED
        self.code = code
        super().__init__(code.value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code.value!r})"


@dataclass(frozen=True, slots=True)
class InspectedAudio:
    format_name: str
    codec_name: str
    duration_seconds: float


_ACTIVE_LOCK = threading.RLock()
_ACTIVE_PROCESSES: set[object] = set()


def _fail(code: MarketMatchAudioCode) -> NoReturn:
    raise MarketMatchAudioError(code) from None


def canonical_wav_max_bytes(duration_seconds: float) -> int:
    if type(duration_seconds) not in (int, float) or not math.isfinite(float(duration_seconds)) or duration_seconds <= 0:
        _fail(MarketMatchAudioCode.DURATION_LIMIT_EXCEEDED)
    return 44 + math.floor(float(duration_seconds) * BYTE_RATE)


def sanitize_original_filename(value: object) -> str:
    """Return display-only text; never a path or media-type signal."""

    if type(value) is not str:
        return "audio"
    name = value.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch >= " " and ch != "\x7f").strip()
    return name[:128] or "audio"


def create_private_workdir() -> tempfile.TemporaryDirectory[str]:
    workspace = tempfile.TemporaryDirectory(prefix="marketmatch-audio-")
    os.chmod(workspace.name, stat.S_IRWXU)
    return workspace


def cleanup_private_workdir(workspace: object) -> None:
    """Remove one private workspace, retrying after restrictive-mode damage."""

    path_value = getattr(workspace, "name", None)
    try:
        workspace.cleanup()
        return
    except Exception:
        pass
    if type(path_value) is not str or not path_value:
        return
    root = Path(path_value)
    try:
        for child in root.rglob("*"):
            try:
                os.chmod(child, stat.S_IRWXU)
            except Exception:
                pass
        try:
            os.chmod(root, stat.S_IRWXU)
        except Exception:
            pass
        shutil.rmtree(root, ignore_errors=True)
    except Exception:
        pass


def _register(process: object) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_PROCESSES.add(process)


def _unregister(process: object) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_PROCESSES.discard(process)


def active_media_process_count() -> int:
    with _ACTIVE_LOCK:
        return len(_ACTIVE_PROCESSES)


async def shutdown_active_media_processes() -> None:
    """Terminate and reap every registered FFmpeg/FFprobe child."""

    with _ACTIVE_LOCK:
        processes = tuple(_ACTIVE_PROCESSES)
    for process in processes:
        await _terminate_and_reap(process)
        _unregister(process)


async def _terminate_and_reap(process: object) -> None:
    try:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                process.kill()
        await process.wait()
    except (ProcessLookupError, Exception):
        try:
            if process.returncode is None:
                process.kill()
            await process.wait()
        except Exception:
            pass


async def _run_local_process(
    executable: str,
    arguments: list[str],
    *,
    timeout: float,
    stdout_path: Path | None = None,
    watched_output: Path | None = None,
    output_limit: int | None = None,
    timeout_code: MarketMatchAudioCode = MarketMatchAudioCode.CONVERSION_TIMEOUT,
) -> int:
    resolved = shutil.which(executable)
    if not resolved:
        _fail(MarketMatchAudioCode.FFMPEG_UNAVAILABLE)
    output_handle = None
    process = None
    try:
        if stdout_path is not None:
            output_handle = stdout_path.open("xb")
        process = await asyncio.create_subprocess_exec(
            resolved,
            *arguments,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=output_handle if output_handle is not None else asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        _register(process)
        deadline = time.monotonic() + timeout
        while process.returncode is None:
            if watched_output is not None and output_limit is not None:
                try:
                    if watched_output.stat().st_size > output_limit:
                        await _terminate_and_reap(process)
                        _fail(MarketMatchAudioCode.DECODED_OUTPUT_LIMIT_EXCEEDED)
                except FileNotFoundError:
                    pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                await _terminate_and_reap(process)
                _fail(timeout_code)
            try:
                await asyncio.wait_for(process.wait(), timeout=min(0.05, remaining))
            except asyncio.TimeoutError:
                continue
        return int(process.returncode)
    except asyncio.CancelledError:
        if process is not None:
            await asyncio.shield(_terminate_and_reap(process))
        raise
    except MarketMatchAudioError:
        raise
    except (FileNotFoundError, PermissionError, OSError):
        _fail(MarketMatchAudioCode.FFMPEG_UNAVAILABLE)
    finally:
        if output_handle is not None:
            output_handle.close()
        if process is not None:
            _unregister(process)


def _parse_probe_payload(payload: bytes, maximum_duration: float) -> InspectedAudio:
    try:
        value = json.loads(payload.decode("utf-8", errors="strict"))
    except Exception:
        _fail(MarketMatchAudioCode.MALFORMED_AUDIO)
    if type(value) is not dict or set(value) - {"format", "programs", "stream_groups", "streams"}:
        _fail(MarketMatchAudioCode.MALFORMED_AUDIO)
    if value.get("programs") not in (None, []) or value.get("stream_groups") not in (None, []):
        _fail(MarketMatchAudioCode.EXTERNAL_MEDIA_REJECTED)
    media_format = value.get("format")
    streams = value.get("streams")
    if type(media_format) is not dict or type(streams) is not list:
        _fail(MarketMatchAudioCode.MALFORMED_AUDIO)
    if not streams:
        _fail(MarketMatchAudioCode.NO_AUDIO_STREAM)
    if len(streams) > MAX_MEDIA_STREAMS:
        _fail(MarketMatchAudioCode.EXCESSIVE_STREAMS)
    format_name = media_format.get("format_name")
    if format_name not in SUPPORTED_FORMAT_NAMES:
        if format_name in {"concat", "hls", "dash", "m3u8", "sdp", "tty"}:
            _fail(MarketMatchAudioCode.EXTERNAL_MEDIA_REJECTED)
        _fail(MarketMatchAudioCode.UNSUPPORTED_FORMAT)
    if any(type(item) is not dict for item in streams):
        _fail(MarketMatchAudioCode.MALFORMED_AUDIO)
    if any(item.get("codec_type") not in {"audio", "video"} for item in streams):
        _fail(MarketMatchAudioCode.EXTERNAL_MEDIA_REJECTED)
    audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
    if not audio_streams:
        _fail(MarketMatchAudioCode.NO_AUDIO_STREAM)
    if len(audio_streams) != 1:
        _fail(MarketMatchAudioCode.EXCESSIVE_STREAMS)
    codec_name = audio_streams[0].get("codec_name")
    if codec_name not in SUPPORTED_AUDIO_CODECS:
        _fail(MarketMatchAudioCode.UNSUPPORTED_FORMAT)
    raw_duration = audio_streams[0].get("duration") or media_format.get("duration")
    try:
        duration = float(raw_duration)
    except (TypeError, ValueError, OverflowError):
        _fail(MarketMatchAudioCode.MALFORMED_AUDIO)
    if not math.isfinite(duration) or duration <= 0:
        _fail(MarketMatchAudioCode.MALFORMED_AUDIO)
    if duration > maximum_duration:
        _fail(MarketMatchAudioCode.DURATION_LIMIT_EXCEEDED)
    return InspectedAudio(format_name=format_name, codec_name=codec_name, duration_seconds=duration)


async def inspect_local_audio(
    input_path: Path,
    workdir: Path,
    *,
    maximum_duration: float = MARKETMATCH_AUDIO_MAX_DURATION_SECONDS,
) -> InspectedAudio:
    probe_path = workdir / "probe.json"
    arguments = [
        "-v", "error", "-protocol_whitelist", "file",
        "-safe", "1",
        "-enable_drefs", "0", "-use_absolute_path", "0",
        "-probesize", "67108864", "-analyzeduration", "30000000",
        "-show_entries", "format=format_name,duration:stream=index,codec_type,codec_name,duration",
        "-of", "json", "-i", os.fspath(input_path),
    ]
    returncode = await _run_local_process(
        "ffprobe", arguments, timeout=MARKETMATCH_FFPROBE_TIMEOUT_SECONDS,
        stdout_path=probe_path, timeout_code=MarketMatchAudioCode.AUDIO_PROBE_TIMEOUT,
    )
    if returncode != 0:
        _fail(MarketMatchAudioCode.MALFORMED_AUDIO)
    try:
        size = probe_path.stat().st_size
        if size <= 0 or size > PROBE_OUTPUT_LIMIT_BYTES:
            _fail(MarketMatchAudioCode.EXCESSIVE_STREAMS)
        payload = probe_path.read_bytes()
    except MarketMatchAudioError:
        raise
    except Exception:
        _fail(MarketMatchAudioCode.MALFORMED_AUDIO)
    return _parse_probe_payload(payload, maximum_duration)


async def convert_to_canonical_wav(
    input_path: Path,
    output_path: Path,
    *,
    maximum_duration: float = MARKETMATCH_AUDIO_MAX_DURATION_SECONDS,
    inspected_format: str | None = None,
) -> None:
    maximum_bytes = canonical_wav_max_bytes(maximum_duration)
    # One extra sample exposes understated duration metadata; the strict
    # canonical validator then rejects it while output remains bounded.
    conversion_limit = maximum_duration + (1.0 / BYTE_RATE * 2)
    demuxer_safety = (
        ["-enable_drefs", "0", "-use_absolute_path", "0"]
        if inspected_format == "mov,mp4,m4a,3gp,3g2,mj2"
        else []
    )
    arguments = [
        "-nostdin", "-hide_banner", "-loglevel", "error",
        "-protocol_whitelist", "file", *demuxer_safety, "-i", os.fspath(input_path),
        "-map", "0:a:0", "-vn", "-sn", "-dn", "-map_metadata", "-1",
        "-t", f"{conversion_limit:.6f}", "-c:a", "pcm_s16le", "-ac", "1",
        "-ar", "16000", "-fflags", "+bitexact", "-flags:a", "+bitexact",
        "-f", "wav", "-y", os.fspath(output_path),
    ]
    returncode = await _run_local_process(
        "ffmpeg", arguments, timeout=MARKETMATCH_FFMPEG_TIMEOUT_SECONDS,
        watched_output=output_path, output_limit=maximum_bytes + WAV_FRAMING_OVERHEAD_BYTES,
    )
    if returncode != 0:
        _fail(MarketMatchAudioCode.CONVERSION_FAILED)
    try:
        size = output_path.stat().st_size
    except Exception:
        _fail(MarketMatchAudioCode.CONVERSION_FAILED)
    if size > maximum_bytes:
        _fail(MarketMatchAudioCode.DECODED_OUTPUT_LIMIT_EXCEEDED)
    if size <= 44:
        _fail(MarketMatchAudioCode.CONVERSION_FAILED)


async def prepare_canonical_audio(input_path: Path, workdir: Path) -> Path:
    inspected = await inspect_local_audio(input_path, workdir)
    output_path = workdir / "canonical.wav"
    await convert_to_canonical_wav(
        input_path, output_path, inspected_format=inspected.format_name
    )
    return output_path


__all__ = (
    "DEFAULT_MAX_DURATION_SECONDS", "MARKETMATCH_AUDIO_MAX_DURATION_SECONDS",
    "MarketMatchAudioCode", "MarketMatchAudioError", "active_media_process_count",
    "canonical_wav_max_bytes", "create_private_workdir", "inspect_local_audio",
    "cleanup_private_workdir", "prepare_canonical_audio", "sanitize_original_filename",
    "shutdown_active_media_processes",
)
