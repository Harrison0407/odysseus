"""Real-process coverage for macOS spawn with detached standard descriptors."""

from __future__ import annotations

import asyncio
import json
import multiprocessing.resource_tracker
import os
from pathlib import Path
import pty
import resource
import subprocess
import sys
import tempfile
import time

import pytest

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if os.fspath(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(_REPOSITORY_ROOT))

from src import marketmatch_stt_process as process


def _fictional_child(input_connection, result_connection):
    input_connection.recv_bytes()
    input_connection.close()
    text = "fictional-std-" + "".join("1" if state else "0" for state in _descriptor_state())
    result_connection.send_bytes(process._encode_worker_message({
        "duration_ms": 1,
        "segments": [{"start_ms": 0, "end_ms": 1, "text": text}],
        "transcript_text": text,
        "language": "zh",
        "language_confidence": 1.0,
    }))
    result_connection.close()


def _fictional_file_child(_wav_path, result_connection):
    text = "fictional-file-std-" + "".join(
        "1" if state else "0" for state in _descriptor_state()
    )
    result_connection.send_bytes(process._encode_worker_message({
        "duration_ms": 1,
        "segments": [{"start_ms": 0, "end_ms": 1, "text": text}],
        "transcript_text": text,
        "language": "zh",
        "language_confidence": 1.0,
    }))
    result_connection.close()


def _descriptor_state() -> list[bool]:
    states = []
    for descriptor in (0, 1, 2):
        try:
            os.fstat(descriptor)
        except OSError:
            states.append(False)
        else:
            states.append(True)
    return states


def _descriptor_identity(descriptor: int) -> tuple[int, int, int, int] | None:
    try:
        details = os.fstat(descriptor)
    except OSError:
        return None
    return details.st_dev, details.st_ino, details.st_mode, details.st_rdev


def _open_descriptor_count() -> int:
    soft_limit = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    ceiling = min(int(soft_limit), 1_024)
    count = 0
    for descriptor in range(ceiling):
        try:
            os.fstat(descriptor)
        except OSError:
            continue
        count += 1
    return count


async def _one_result():
    return await process.transcribe_in_spawned_process(
        b"fictional-audio",
        deadline=time.monotonic() + 5,
        _target=_fictional_child,
    )


async def _one_file_result():
    with tempfile.TemporaryDirectory(prefix="marketmatch-stt-fd-test-") as directory:
        path = Path(directory) / "canonical.wav"
        path.write_bytes(b"fictional-generated-audio")
        return await process.transcribe_canonical_file_in_spawned_process(
            path,
            byte_limit=1_000,
            duration_limit_ms=1_000,
            deadline=time.monotonic() + 5,
            _target=_fictional_file_child,
        )


async def _exercise_workers(mode: str) -> dict[str, object]:
    if mode == "stdin_closed":
        os.close(0)
    elif mode == "stdout_closed":
        os.close(1)
    elif mode == "stderr_closed":
        os.close(2)
    elif mode == "multiple_closed":
        os.close(0)
        os.close(2)
    elif mode == "all_closed":
        os.close(0)
        os.close(1)
        os.close(2)
    elif mode == "stdin_null":
        replacement = os.open(os.devnull, os.O_RDONLY)
        try:
            os.dup2(replacement, 0)
        finally:
            if replacement != 0:
                os.close(replacement)

    before = _descriptor_state()
    valid_identities = [
        _descriptor_identity(descriptor) if before[descriptor] else None
        for descriptor in (0, 1, 2)
    ]
    first = await _one_result()
    file_result = await _one_file_result()
    warmed_descriptor_count = _open_descriptor_count()
    repeated = [await _one_result() for _ in range(4)]
    concurrent = await asyncio.gather(_one_result(), _one_result())
    final_descriptor_count = _open_descriptor_count()
    tracker = multiprocessing.resource_tracker._resource_tracker
    outcome = {
        "results_valid": all(
            item.transcript_text.startswith("fictional-std-")
            and item.language == "zh"
            and item.segments == ((0, 1, item.transcript_text),)
            for item in (first, *repeated, *concurrent)
        ) and file_result.transcript_text.startswith("fictional-file-std-"),
        "descriptor_growth": final_descriptor_count - warmed_descriptor_count,
        "child_standard_states": sorted({
            item.transcript_text.rsplit("-", 1)[-1]
            for item in (first, *repeated, *concurrent, file_result)
        }),
        "resource_tracker_alive": tracker._check_alive(),
        "active_workers": process.active_worker_count(),
    }
    after = _descriptor_state()
    after_identities = [_descriptor_identity(descriptor) for descriptor in (0, 1, 2)]
    outcome.update({
        "before": before,
        "after": after,
        "valid_unchanged": all(
            identity is None or identity == after_identities[descriptor]
            for descriptor, identity in enumerate(valid_identities)
        ),
    })
    return outcome


def _run_harness(mode: str, report_descriptor: int) -> None:
    outcome = asyncio.run(_exercise_workers(mode))
    os.write(report_descriptor, json.dumps(outcome, sort_keys=True).encode() + b"\n")


def _invoke_harness(
    tmp_path: Path,
    mode: str,
    *,
    tty_stdin: bool = False,
    tty_output: bool = False,
    nohup: bool = False,
    detached_session: bool = False,
):
    report_read, report_write = os.pipe()
    log_path = tmp_path / f"{mode}.log"
    master = slave = None
    try:
        with log_path.open("wb") as log:
            stdin = subprocess.DEVNULL
            stdout = stderr = log
            if tty_stdin or tty_output:
                master, slave = pty.openpty()
            if tty_stdin:
                stdin = slave
            if tty_output:
                stdout = stderr = slave
            command = [
                sys.executable,
                "-B",
                os.fspath(Path(__file__).resolve()),
                "--fd-harness",
                mode,
                str(report_write),
            ]
            if nohup:
                command.insert(0, "nohup")
            completed = subprocess.run(
                command,
                cwd=Path(__file__).resolve().parents[1],
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                pass_fds=(report_write,),
                start_new_session=detached_session,
                check=False,
                timeout=30,
            )
        os.close(report_write)
        report_write = -1
        payload = os.read(report_read, 64 * 1024)
        assert completed.returncode == 0, log_path.read_text(encoding="utf-8", errors="replace")
        assert payload
        return json.loads(payload)
    finally:
        for descriptor in (report_read, report_write, master, slave):
            if descriptor is None or descriptor < 0:
                continue
            try:
                os.close(descriptor)
            except OSError:
                pass


@pytest.mark.parametrize(
    ("mode", "expected_before"),
    [
        ("valid", [True, True, True]),
        ("stdin_closed", [False, True, True]),
        ("stdin_null", [True, True, True]),
        ("stdout_closed", [True, False, True]),
        ("stderr_closed", [True, True, False]),
        ("multiple_closed", [False, True, False]),
        ("all_closed", [False, False, False]),
    ],
)
def test_real_spawn_repairs_only_invalid_standard_descriptors(
    tmp_path, mode, expected_before
):
    report = _invoke_harness(tmp_path, mode)
    assert report["before"] == expected_before
    assert report["after"] == [True, True, True]
    assert report["valid_unchanged"] is True
    assert report["results_valid"] is True
    assert report["resource_tracker_alive"] is True
    assert report["child_standard_states"] == ["111"]
    assert report["active_workers"] == 0
    assert report["descriptor_growth"] == 0


def test_real_spawn_with_foreground_terminal_descriptors(tmp_path):
    report = _invoke_harness(
        tmp_path, "valid", tty_stdin=True, tty_output=True
    )
    assert report["results_valid"] is True
    assert report["valid_unchanged"] is True


def test_real_spawn_under_nohup_with_null_stdin_and_redirected_output(tmp_path):
    report = _invoke_harness(tmp_path, "stdin_null", nohup=True)
    assert report["results_valid"] is True
    assert report["resource_tracker_alive"] is True
    assert report["descriptor_growth"] == 0


def test_real_spawn_in_detached_session_with_redirected_descriptors(tmp_path):
    report = _invoke_harness(tmp_path, "stdin_null", detached_session=True)
    assert report["results_valid"] is True
    assert report["valid_unchanged"] is True
    assert report["resource_tracker_alive"] is True
    assert report["descriptor_growth"] == 0


def test_valid_standard_descriptors_do_not_open_null_device(monkeypatch):
    assert process._standard_descriptor_is_valid(0)
    assert process._standard_descriptor_is_valid(1)
    assert process._standard_descriptor_is_valid(2)

    def unexpected_open(*_args, **_kwargs):
        raise AssertionError("valid standard descriptors must not be replaced")

    monkeypatch.setattr(process.os, "open", unexpected_open)
    assert process._repair_invalid_standard_descriptors() == ()


if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == "--fd-harness":
    _run_harness(sys.argv[2], int(sys.argv[3]))
