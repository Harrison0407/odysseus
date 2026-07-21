"""Common-media conversion and safety regression coverage for Capture STT."""

import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import struct

import pytest

from src import marketmatch_audio as audio
from src.marketmatch_audio import MarketMatchAudioCode, MarketMatchAudioError
from src.marketmatch_canonical_wav import (
    BYTE_RATE,
    CanonicalWavCode,
    CanonicalWavError,
    validate_canonical_wav_layout,
)
from src.marketmatch_canonical_wav_file import decode_canonical_wav_file
from src.upload_limits import MARKETMATCH_CALL_AUDIO_MAX_BYTES
from src import marketmatch_stt_process as process


def _hanging_file_worker(_path, _result_connection):
    while True:
        time.sleep(1)


def _crashing_file_worker(_path, _result_connection):
    raise RuntimeError("private crash detail")


def _require_ffmpeg():
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("local FFmpeg/FFprobe executables are unavailable")
    return ffmpeg


def _generate(path: Path, output_args: list[str], *, inputs: list[str] | None = None):
    ffmpeg = _require_ffmpeg()
    input_args = inputs or ["-f", "lavfi", "-i", "sine=frequency=440:duration=0.08"]
    completed = subprocess.run(
        [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", *input_args,
         *output_args, "-y", str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    if completed.returncode:
        pytest.fail(f"local FFmpeg fixture generation failed for {path.suffix}")


@pytest.mark.parametrize(
    ("suffix", "arguments"),
    [
        (".wav", ["-c:a", "pcm_s24le"]),
        (".m4a", ["-c:a", "aac"]),
        (".mp3", ["-c:a", "libmp3lame"]),
        (".aac", ["-c:a", "aac"]),
        (".caf", ["-c:a", "pcm_s16be"]),
        (".flac", ["-c:a", "flac"]),
        (".ogg", ["-ac", "2", "-c:a", "vorbis", "-strict", "-2"]),
        (".opus", ["-c:a", "libopus", "-f", "ogg"]),
        (".webm", ["-c:a", "libopus"]),
        (".mp4", ["-c:a", "aac"]),
        (".mov", ["-c:a", "aac"]),
    ],
)
async def test_common_local_formats_convert_through_canonical_boundary(tmp_path, suffix, arguments):
    source = tmp_path / f"source{suffix}"
    _generate(source, arguments)
    workdir = tmp_path / "private"
    workdir.mkdir(mode=0o700)

    canonical = await audio.prepare_canonical_audio(source, workdir)
    waveform = decode_canonical_wav_file(
        canonical, byte_limit=10_000_000, duration_limit_ms=1_000
    )

    assert waveform.sample_count > 0
    assert waveform.duration_ms <= 1_000
    header = canonical.read_bytes()[:44]
    assert header[20:36] == b"\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00"


@pytest.mark.parametrize("payload", [b"not media", b"RIFF\x00\x00\x00\x00WAVEtruncated"])
async def test_renamed_nonmedia_and_truncated_wav_reject(tmp_path, payload):
    source = tmp_path / "forged.m4a"
    source.write_bytes(payload)
    with pytest.raises(MarketMatchAudioError) as raised:
        await audio.inspect_local_audio(source, tmp_path)
    assert raised.value.code is MarketMatchAudioCode.MALFORMED_AUDIO


async def test_video_only_mp4_rejects_as_no_audio(tmp_path):
    source = tmp_path / "video.mp4"
    _generate(
        source,
        ["-c:v", "libx264", "-pix_fmt", "yuv420p"],
        inputs=["-f", "lavfi", "-i", "color=size=16x16:duration=0.08"],
    )
    with pytest.raises(MarketMatchAudioError) as raised:
        await audio.inspect_local_audio(source, tmp_path)
    assert raised.value.code is MarketMatchAudioCode.NO_AUDIO_STREAM


async def test_multiple_audio_streams_reject(tmp_path):
    source = tmp_path / "multiple.m4a"
    _generate(
        source,
        ["-map", "0:a", "-map", "1:a", "-c:a", "aac"],
        inputs=[
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.08",
            "-f", "lavfi", "-i", "sine=frequency=880:duration=0.08",
        ],
    )
    with pytest.raises(MarketMatchAudioError) as raised:
        await audio.inspect_local_audio(source, tmp_path)
    assert raised.value.code is MarketMatchAudioCode.EXCESSIVE_STREAMS


def _probe(duration: str, *, format_name="wav", codec="pcm_s16le") -> bytes:
    return json.dumps({
        "programs": [], "stream_groups": [],
        "streams": [{"index": 0, "codec_type": "audio", "codec_name": codec, "duration": duration}],
        "format": {"format_name": format_name, "duration": duration},
    }).encode()


def test_six_hour_duration_boundary_and_above_limit():
    assert audio._parse_probe_payload(_probe("21600.000000"), 21_600).duration_seconds == 21_600
    with pytest.raises(MarketMatchAudioError) as raised:
        audio._parse_probe_payload(_probe("21600.000001"), 21_600)
    assert raised.value.code is MarketMatchAudioCode.DURATION_LIMIT_EXCEEDED
    assert audio.canonical_wav_max_bytes(21_600) == 44 + (21_600 * BYTE_RATE)
    data_size = 21_600 * BYTE_RATE
    header = (
        b"RIFF" + struct.pack("<I", 36 + data_size) + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 16_000, 32_000, 2, 16)
        + b"data" + struct.pack("<I", data_size)
    )
    assert validate_canonical_wav_layout(
        header,
        total_size=44 + data_size,
        byte_limit=44 + data_size,
        duration_limit_ms=21_600_000,
        maximum_byte_limit=44 + data_size,
        maximum_duration_ms=21_600_000,
    ) == 21_600 * 16_000
    over_header = (
        b"RIFF" + struct.pack("<I", 38 + data_size) + header[8:40]
        + struct.pack("<I", data_size + 2)
    )
    with pytest.raises(CanonicalWavError) as decoded_over:
        validate_canonical_wav_layout(
            over_header,
            total_size=46 + data_size,
            byte_limit=46 + data_size,
            duration_limit_ms=21_600_000,
            maximum_byte_limit=46 + data_size,
            maximum_duration_ms=21_600_000,
        )
    assert decoded_over.value.code is CanonicalWavCode.DURATION_LIMIT_EXCEEDED


def test_playlist_and_external_programs_reject_without_following_resources():
    with pytest.raises(MarketMatchAudioError) as playlist:
        audio._parse_probe_payload(_probe("1", format_name="hls"), 21_600)
    assert playlist.value.code is MarketMatchAudioCode.EXTERNAL_MEDIA_REJECTED
    value = json.loads(_probe("1"))
    value["programs"] = [{"program_id": 1}]
    with pytest.raises(MarketMatchAudioError) as program:
        audio._parse_probe_payload(json.dumps(value).encode(), 21_600)
    assert program.value.code is MarketMatchAudioCode.EXTERNAL_MEDIA_REJECTED


async def test_concat_with_absolute_external_reference_rejects(tmp_path):
    _require_ffmpeg()
    source = tmp_path / "encoded.input"
    source.write_text("ffconcat version 1.0\nfile '/etc/passwd'\n", encoding="utf-8")
    with pytest.raises(MarketMatchAudioError) as raised:
        await audio.inspect_local_audio(source, tmp_path)
    assert raised.value.code in {
        MarketMatchAudioCode.MALFORMED_AUDIO,
        MarketMatchAudioCode.EXTERNAL_MEDIA_REJECTED,
    }


def test_filename_is_display_only_and_cannot_escape():
    assert audio.sanitize_original_filename("../../private/voice.m4a") == "voice.m4a"
    assert audio.sanitize_original_filename("..\\..\\voice\nname.mp3") == "voicename.mp3"
    assert audio.sanitize_original_filename(None) == "audio"


def test_cleanup_retries_after_workspace_cleanup_failure(tmp_path):
    root = tmp_path / "marketmatch-audio-fixture"
    root.mkdir(mode=0o700)
    (root / "encoded.input").write_bytes(b"private")

    class BrokenCleanup:
        name = str(root)

        @staticmethod
        def cleanup():
            raise PermissionError("private path detail")

    audio.cleanup_private_workdir(BrokenCleanup())
    assert not root.exists()


def test_exact_marketmatch_upload_limit_and_no_duplicate_setting():
    assert MARKETMATCH_CALL_AUDIO_MAX_BYTES == 209_715_200
    source = Path("routes/marketmatch_stt_routes.py").read_text(encoding="utf-8")
    assert "from src.upload_limits import MARKETMATCH_CALL_AUDIO_MAX_BYTES" in source
    assert "209715200" not in source


def test_process_invocation_has_no_shell_or_remote_protocols():
    source = Path(audio.__file__).read_text(encoding="utf-8")
    assert "shell=True" not in source
    assert "create_subprocess_shell" not in source
    assert '"-protocol_whitelist", "file"' in source
    for protocol in ("http", "https", "ftp", "sftp", "rtsp", "rtmp", "data", "concat"):
        assert f'"{protocol},' not in source


async def test_conversion_failure_leaves_no_active_process(tmp_path):
    source = tmp_path / "bad.mp3"
    source.write_bytes(b"bad")
    with pytest.raises(MarketMatchAudioError):
        await audio.inspect_local_audio(source, tmp_path)
    assert audio.active_media_process_count() == 0


async def test_conversion_process_timeout_and_cancellation_reap(monkeypatch):
    monkeypatch.setattr(audio.shutil, "which", lambda _name: sys.executable)
    with pytest.raises(MarketMatchAudioError) as raised:
        await audio._run_local_process(
            "ffmpeg", ["-c", "import time; time.sleep(10)"], timeout=0.05
        )
    assert raised.value.code is MarketMatchAudioCode.CONVERSION_TIMEOUT
    assert audio.active_media_process_count() == 0

    task = asyncio.create_task(audio._run_local_process(
        "ffmpeg", ["-c", "import time; time.sleep(10)"], timeout=10
    ))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert audio.active_media_process_count() == 0


async def test_missing_local_ffmpeg_is_distinct(monkeypatch):
    monkeypatch.setattr(audio.shutil, "which", lambda _name: None)
    with pytest.raises(MarketMatchAudioError) as raised:
        await audio._run_local_process("ffmpeg", [], timeout=1)
    assert raised.value.code is MarketMatchAudioCode.FFMPEG_UNAVAILABLE


def test_file_boundary_rejects_noncanonical_wav(tmp_path):
    path = tmp_path / "bad.wav"
    path.write_bytes(b"RIFF" + b"\x00" * 100)
    with pytest.raises(CanonicalWavError) as raised:
        decode_canonical_wav_file(path, byte_limit=1_000, duration_limit_ms=1_000)
    assert raised.value.code in {CanonicalWavCode.INVALID_WAV, CanonicalWavCode.UNSUPPORTED_WAV_FORMAT}


async def test_file_transcription_timeout_terminates_and_reaps(tmp_path):
    path = (tmp_path / "canonical.wav").resolve()
    path.write_bytes(b"placeholder")
    with pytest.raises(process.MarketMatchProcessError) as raised:
        await process.transcribe_canonical_file_in_spawned_process(
            path,
            byte_limit=1_000,
            duration_limit_ms=1_000,
            deadline=time.monotonic() + 0.15,
            _target=_hanging_file_worker,
        )
    assert raised.value.code is process.MarketMatchProcessCode.WORKER_TIMEOUT
    assert process.active_worker_count() == 0


async def test_file_transcription_crash_is_safe_and_reaped(tmp_path):
    path = (tmp_path / "canonical.wav").resolve()
    path.write_bytes(b"placeholder")
    with pytest.raises(process.MarketMatchProcessError) as raised:
        await process.transcribe_canonical_file_in_spawned_process(
            path,
            byte_limit=1_000,
            duration_limit_ms=1_000,
            deadline=time.monotonic() + 2,
            _target=_crashing_file_worker,
        )
    assert raised.value.code is process.MarketMatchProcessCode.WORKER_FAILED
    assert "private crash detail" not in repr(raised.value)
    assert process.active_worker_count() == 0


async def test_file_transcription_cancellation_terminates_and_reaps(tmp_path):
    path = (tmp_path / "canonical.wav").resolve()
    path.write_bytes(b"placeholder")
    task = asyncio.create_task(process.transcribe_canonical_file_in_spawned_process(
        path,
        byte_limit=1_000,
        duration_limit_ms=1_000,
        deadline=time.monotonic() + 10,
        _target=_hanging_file_worker,
    ))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.active_worker_count() == 0
