import asyncio
import json
import os
import signal
import struct
import time
import types

import numpy as np
import pytest

from src import marketmatch_stt_process as process_module
from src.marketmatch_canonical_wav import CanonicalWavCode
from src.marketmatch_stt_process import (
    MAX_RESULT_JSON_BYTES,
    MarketMatchProcessCode,
    MarketMatchProcessError,
    active_worker_count,
    release_admission,
    shutdown_active_workers,
    transcribe_in_spawned_process,
    try_acquire_admission,
)


def _message(*, text="ok", duration_ms=1, segments=None):
    if segments is None:
        segments = [{"start_ms": 0, "end_ms": duration_ms, "text": text}]
    return json.dumps(
        {
            "duration_ms": duration_ms,
            "segments": segments,
            "transcript_text": text,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()


def _read_input(input_connection):
    try:
        return input_connection.recv_bytes()
    finally:
        input_connection.close()


def _success_child(input_connection, result_connection):
    _read_input(input_connection)
    result_connection.send_bytes(_message())
    result_connection.close()


def _versioned_success_child(input_connection, result_connection, protocol_version):
    _read_input(input_connection)
    payload = json.loads(_message(text=f"v{protocol_version}"))
    if protocol_version >= 2:
        payload.update({"language": "en", "language_confidence": 0.93})
    result_connection.send_bytes(process_module._encode_worker_message(payload))
    result_connection.close()


def _large_result_child(input_connection, result_connection):
    _read_input(input_connection)
    text = "x" * (128 * 1024)
    result_connection.send_bytes(_message(text=text))
    result_connection.close()


def _crash_child(input_connection, result_connection):
    del result_connection
    _read_input(input_connection)
    os._exit(7)


def _nonzero_after_result_child(input_connection, result_connection):
    _read_input(input_connection)
    result_connection.send_bytes(_message())
    result_connection.close()
    os._exit(9)


def _malformed_child(input_connection, result_connection):
    _read_input(input_connection)
    result_connection.send_bytes(b"not-json")
    result_connection.close()


def _duplicate_child(input_connection, result_connection):
    _read_input(input_connection)
    result_connection.send_bytes(_message())
    result_connection.send_bytes(_message())
    result_connection.close()


def _oversized_child(input_connection, result_connection):
    _read_input(input_connection)
    result_connection.send_bytes(b"x" * (MAX_RESULT_JSON_BYTES + 1))
    result_connection.close()


def _hanging_child(input_connection, result_connection):
    del result_connection
    _read_input(input_connection)
    time.sleep(30)


def _invalid_wav_child(input_connection, result_connection):
    process_module._marketmatch_stt_child(input_connection, result_connection)


class _MemoryReceiveConnection:
    def __init__(self, payload):
        self.payload = payload
        self.closed = False

    def recv_bytes(self, maxlength=None):
        assert maxlength == process_module.MAX_WAV_BYTES
        return self.payload

    def close(self):
        self.closed = True


class _MemorySendConnection:
    def __init__(self):
        self.messages = []
        self.closed = False

    def send_bytes(self, payload):
        self.messages.append(payload)

    def close(self):
        self.closed = True


def _canonical_wav(sample=0):
    pcm = struct.pack("<h", sample)
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(pcm))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 16_000, 32_000, 2, 16)
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )


def _canonical_wav_frames(sample_count, sample=0):
    pcm = struct.pack(f"<{sample_count}h", *([sample] * sample_count))
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(pcm))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 16_000, 32_000, 2, 16)
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )


@pytest.fixture(autouse=True)
def _clean_workers():
    shutdown_active_workers()
    yield
    shutdown_active_workers()


async def test_spawned_child_success():
    result = await transcribe_in_spawned_process(
        b"fixture",
        deadline=time.monotonic() + 5,
        _target=_success_child,
    )
    assert result.duration_ms == 1
    assert result.transcript_text == "ok"
    assert result.segments == ((0, 1, "ok"),)
    assert active_worker_count() == 0


async def test_updated_parent_requests_version_two_from_default_worker(monkeypatch):
    monkeypatch.setattr(process_module, "_marketmatch_stt_child", _versioned_success_child)
    result = await transcribe_in_spawned_process(
        b"fixture",
        deadline=time.monotonic() + 5,
    )
    assert result.transcript_text == "v2"
    assert result.language == "en"
    assert result.language_confidence == 0.93
    assert active_worker_count() == 0


async def test_result_larger_than_pipe_buffer_does_not_deadlock():
    result = await transcribe_in_spawned_process(
        b"fixture",
        deadline=time.monotonic() + 5,
        _target=_large_result_child,
    )
    assert len(result.transcript_text) == 128 * 1024
    assert active_worker_count() == 0


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (_crash_child, MarketMatchProcessCode.WORKER_FAILED),
        (_nonzero_after_result_child, MarketMatchProcessCode.WORKER_FAILED),
        (_malformed_child, MarketMatchProcessCode.WORKER_PROTOCOL_ERROR),
        (_duplicate_child, MarketMatchProcessCode.WORKER_PROTOCOL_ERROR),
        (_oversized_child, MarketMatchProcessCode.WORKER_PROTOCOL_ERROR),
    ],
)
async def test_child_failures_are_fixed_and_reaped(target, expected):
    with pytest.raises(MarketMatchProcessError) as caught:
        await transcribe_in_spawned_process(
            b"PRIVATE_AUDIO_CANARY",
            deadline=time.monotonic() + 5,
            _target=target,
        )
    assert caught.value.code is expected
    assert "PRIVATE_AUDIO_CANARY" not in str(caught.value) + repr(caught.value)
    assert active_worker_count() == 0


async def test_actual_child_maps_invalid_wav_without_loading_model():
    with pytest.raises(MarketMatchProcessError) as caught:
        await transcribe_in_spawned_process(
            b"not-a-wave",
            deadline=time.monotonic() + 5,
            _target=_invalid_wav_child,
        )
    assert caught.value.code is CanonicalWavCode.INVALID_WAV
    assert active_worker_count() == 0


def test_actual_child_canonical_success_with_injected_backend(monkeypatch):
    receive = _MemoryReceiveConnection(_canonical_wav())
    send = _MemorySendConnection()
    monkeypatch.setattr(process_module, "_scrub_worker_environment", lambda: None)
    monkeypatch.setattr(
        process_module,
        "_fixed_local_base_backend",
        lambda waveform, language_metadata=None: ((0.0, len(waveform) / 16_000, "ok"),),
    )

    process_module._marketmatch_stt_child(receive, send)

    assert receive.closed is True
    assert send.closed is True
    assert len(send.messages) == 1
    assert set(json.loads(send.messages[0])) == {"duration_ms", "segments", "transcript_text"}
    decoded = process_module._decode_parent_result(send.messages[0])
    assert decoded.transcript_text == "ok"
    assert decoded.segments == ((0, 0, "ok"),)


def test_updated_parent_can_request_extended_worker_language_protocol(monkeypatch):
    receive = _MemoryReceiveConnection(_canonical_wav())
    send = _MemorySendConnection()
    monkeypatch.setattr(process_module, "_scrub_worker_environment", lambda: None)

    def backend(waveform, language_metadata=None):
        language_metadata.update({"language": "es", "language_confidence": 0.88})
        return ((0.0, len(waveform) / 16_000, "hola"),)

    monkeypatch.setattr(process_module, "_fixed_local_base_backend", backend)
    process_module._marketmatch_stt_child(receive, send, 2)

    payload = json.loads(send.messages[0])
    assert set(payload) == {
        "duration_ms", "segments", "transcript_text", "language", "language_confidence",
    }
    assert payload["language"] == "es"
    assert payload["language_confidence"] == 0.88


def test_version_two_worker_uses_und_and_null_only_when_engine_metadata_is_absent(monkeypatch):
    receive = _MemoryReceiveConnection(_canonical_wav())
    send = _MemorySendConnection()
    monkeypatch.setattr(process_module, "_scrub_worker_environment", lambda: None)
    monkeypatch.setattr(
        process_module,
        "_fixed_local_base_backend",
        lambda waveform, language_metadata=None: ((0.0, len(waveform) / 16_000, "原文"),),
    )

    process_module._marketmatch_stt_child(receive, send, 2)

    payload = json.loads(send.messages[0])
    assert payload["transcript_text"] == "原文"
    assert payload["language"] == "und"
    assert payload["language_confidence"] is None


def test_two_argument_worker_protocol_remains_compatible_with_legacy_parent(monkeypatch):
    receive = _MemoryReceiveConnection(_canonical_wav())
    send = _MemorySendConnection()
    monkeypatch.setattr(process_module, "_scrub_worker_environment", lambda: None)

    def backend(waveform, language_metadata=None):
        language_metadata.update({"language": "en", "language_confidence": 0.75})
        return ((0.0, len(waveform) / 16_000, "hello"),)

    monkeypatch.setattr(process_module, "_fixed_local_base_backend", backend)
    process_module._marketmatch_stt_child(receive, send)

    assert set(json.loads(send.messages[0])) == {"duration_ms", "segments", "transcript_text"}


async def test_hard_timeout_terminates_and_reaps_child():
    with pytest.raises(MarketMatchProcessError) as caught:
        await transcribe_in_spawned_process(
            b"fixture",
            deadline=time.monotonic() + process_module.PROCESS_CLEANUP_RESERVE_SECONDS + 0.15,
            _target=_hanging_child,
        )
    assert caught.value.code is MarketMatchProcessCode.WORKER_TIMEOUT
    assert active_worker_count() == 0


async def test_cancellation_terminates_and_reaps_child():
    task = asyncio.create_task(
        transcribe_in_spawned_process(
            b"fixture",
            deadline=time.monotonic() + 30,
            _target=_hanging_child,
        )
    )
    for _ in range(200):
        if active_worker_count() == 1:
            break
        await asyncio.sleep(0.01)
    assert active_worker_count() == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert active_worker_count() == 0


async def test_repeated_failures_leave_no_registered_child():
    for _ in range(3):
        with pytest.raises(MarketMatchProcessError):
            await transcribe_in_spawned_process(
                b"fixture",
                deadline=time.monotonic() + process_module.PROCESS_CLEANUP_RESERVE_SECONDS + 0.1,
                _target=_hanging_child,
            )
        assert active_worker_count() == 0


async def test_shutdown_reaps_active_child():
    task = asyncio.create_task(
        transcribe_in_spawned_process(
            b"fixture",
            deadline=time.monotonic() + 30,
            _target=_hanging_child,
        )
    )
    for _ in range(200):
        if active_worker_count() == 1:
            break
        await asyncio.sleep(0.01)
    assert active_worker_count() == 1
    await asyncio.to_thread(shutdown_active_workers)
    with pytest.raises(MarketMatchProcessError):
        await task
    assert active_worker_count() == 0


def test_terminate_grace_escalates_to_kill_and_final_join():
    calls = []

    class IgnoringProcess:
        def __init__(self):
            self.alive_checks = 0

        def is_alive(self):
            self.alive_checks += 1
            return self.alive_checks <= 2

        def terminate(self):
            calls.append("terminate")

        def kill(self):
            calls.append("kill")

        def join(self, timeout=None):
            calls.append(("join", timeout))

        def close(self):
            calls.append("close")

    process_module._terminate_and_reap(IgnoringProcess())
    assert calls == [
        "terminate",
        ("join", process_module.PROCESS_TERMINATE_GRACE_SECONDS),
        "kill",
        ("join", None),
        "close",
    ]


def test_stale_admission_lease_cannot_release_a_new_request():
    first = try_acquire_admission()
    assert first is not None
    release_admission()
    second = try_acquire_admission()
    assert second is not None
    release_admission(first)
    assert try_acquire_admission() is None
    release_admission(second)


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"null",
        b"{}",
        b'{"error":"UNKNOWN"}',
        _message(duration_ms=0),
        _message(segments=[{"start_ms": 2, "end_ms": 1, "text": "ok"}]),
        _message(text="different", segments=[{"start_ms": 0, "end_ms": 1, "text": "ok"}]),
        json.dumps(
            {
                "duration_ms": 1,
                "segments": [],
                "transcript_text": "x" * (process_module.MAX_TRANSCRIPT_UTF8_BYTES + 1),
            }
        ).encode(),
    ],
)
def test_parent_rejects_malformed_or_out_of_bounds_schema(payload):
    with pytest.raises(MarketMatchProcessError) as caught:
        process_module._decode_parent_result(payload)
    assert caught.value.code is MarketMatchProcessCode.WORKER_PROTOCOL_ERROR


def test_fixed_backend_uses_only_local_cpu_int8_base(monkeypatch):
    captured = {}

    class Model:
        def __init__(self, model, **kwargs):
            captured["model"] = model
            captured["kwargs"] = kwargs

        def transcribe(self, waveform):
            captured["waveform"] = waveform
            segment = types.SimpleNamespace(start=0, end=1, text="ok")
            return [segment], object()

    monkeypatch.setitem(os.sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    waveform = np.zeros(16_000, dtype=np.float32)
    result = tuple(process_module._fixed_local_base_backend(waveform))

    assert captured["model"] == "base"
    assert captured["kwargs"] == {
        "device": "cpu",
            "compute_type": "int8",
            "num_workers": 1,
            "local_files_only": True,
            "revision": process_module.FASTER_WHISPER_BASE_REVISION,
        }
    assert captured["waveform"] is waveform
    assert result == ((0.0, 1.0, "ok"),)


def test_fixed_backend_clamps_whisper_frame_overshoot_to_short_wav_duration(monkeypatch):
    calls = 0

    class Model:
        def __init__(self, *_args, **_kwargs):
            pass

        def transcribe(self, _waveform):
            nonlocal calls
            calls += 1
            segment = types.SimpleNamespace(start=0.0, end=1.0, text="short speech")
            info = types.SimpleNamespace(language="en", language_probability=0.9)
            return [segment], info

    monkeypatch.setitem(os.sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    metadata = {}
    result = tuple(process_module._fixed_local_base_backend(np.zeros(4_000), metadata))

    assert calls == 1
    assert result == ((0.0, 0.25, "short speech"),)
    assert metadata == {"language": "en", "language_confidence": 0.9}


def test_v2_worker_accepts_short_wav_when_whisper_end_uses_padded_frame(monkeypatch):
    receive = _MemoryReceiveConnection(_canonical_wav_frames(4_000))
    send = _MemorySendConnection()
    monkeypatch.setattr(process_module, "_scrub_worker_environment", lambda: None)

    class Model:
        def __init__(self, *_args, **_kwargs):
            pass

        def transcribe(self, _waveform):
            segment = types.SimpleNamespace(start=0.0, end=1.0, text="short speech")
            info = types.SimpleNamespace(language="en", language_probability=0.9)
            return [segment], info

    monkeypatch.setitem(os.sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    process_module._marketmatch_stt_child(receive, send, 2)

    payload = json.loads(send.messages[0])
    assert payload == {
        "duration_ms": 250,
        "segments": [{"start_ms": 0, "end_ms": 250, "text": "short speech"}],
        "transcript_text": "short speech",
        "language": "en",
        "language_confidence": 0.9,
    }
    decoded = process_module._decode_parent_result(send.messages[0])
    assert decoded.segments == ((0, 250, "short speech"),)


@pytest.mark.parametrize("raw_language", ["zh", "zh-CN", "zh-Hans"])
def test_fixed_backend_exposes_genuine_global_language_metadata(monkeypatch, raw_language):
    class Model:
        def __init__(self, *_args, **_kwargs):
            pass

        def transcribe(self, _waveform):
            segment = types.SimpleNamespace(start=0, end=1, text="原文")
            info = types.SimpleNamespace(language=raw_language, language_probability=0.875)
            return [segment], info

    monkeypatch.setitem(os.sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    metadata = {}
    result = tuple(process_module._fixed_local_base_backend(np.zeros(16_000), metadata))
    assert result == ((0.0, 1.0, "原文"),)
    assert metadata == {"language": "zh", "language_confidence": 0.875}


def test_requested_mandarin_reaches_existing_local_whisper_boundary(monkeypatch):
    observed = []

    class Model:
        def __init__(self, model, **kwargs):
            assert model == "base"
            assert kwargs["local_files_only"] is True

        def transcribe(self, waveform, **kwargs):
            observed.append(kwargs)
            segment = types.SimpleNamespace(start=0, end=0.25, text="决定")
            info = types.SimpleNamespace(language="zh", language_probability=0.9)
            return [segment], info

    monkeypatch.setitem(os.sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    result = tuple(process_module._fixed_local_base_backend(
        np.zeros(4_000), {}, requested_language="zh"
    ))
    assert result == ((0.0, 0.25, "决定"),)
    assert observed == [{"language": "zh"}]


def test_parent_accepts_language_metadata_without_segment_language_invention():
    payload = process_module._encode_worker_message({
        "duration_ms": 1000,
        "segments": [{"start_ms": 0, "end_ms": 1000, "text": "hola"}],
        "transcript_text": "hola",
        "language": "es-DO",
        "language_confidence": 0.91,
    })
    result = process_module._decode_parent_result(payload)
    assert result.language == "es"
    assert result.language_confidence == 0.91
    assert result.segments == ((0, 1000, "hola"),)


def test_worker_environment_drops_application_secrets(monkeypatch):
    fake_environment = {
        "ODYSSEUS_PRIVATE_CANARY": "secret",
        "HOME": "/safe/cache-home",
    }
    monkeypatch.setattr(process_module.os, "environ", fake_environment)
    process_module._scrub_worker_environment()
    assert "ODYSSEUS_PRIVATE_CANARY" not in fake_environment
    assert fake_environment == {"HOME": "/safe/cache-home"}


def test_process_source_excludes_prohibited_storage_and_decoder_surfaces():
    source = open(process_module.__file__, encoding="utf-8").read().lower()
    prohibited = (
        "uploadfile",
        "tempfile",
        "namedtemporaryfile",
        "pyav",
        "ffmpeg",
        "subprocess",
        "shared_memory",
        "queue(",
        "pickle",
        "sqlalchemy",
        "sessionlocal",
        "chromadb",
        "rag_manager",
        "sttservice",
        "endpoint:",
    )
    assert all(token not in source for token in prohibited)


def test_spawn_context_is_explicit_and_global_start_method_is_untouched(monkeypatch):
    calls = []
    real_get_context = process_module.multiprocessing.get_context

    def recording_get_context(method):
        calls.append(method)
        return real_get_context(method)

    monkeypatch.setattr(process_module.multiprocessing, "get_context", recording_get_context)

    async def run():
        await transcribe_in_spawned_process(
            b"fixture",
            deadline=time.monotonic() + 5,
            _target=_success_child,
        )

    asyncio.run(run())
    assert calls == ["spawn"]
