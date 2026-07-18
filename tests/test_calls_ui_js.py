"""Focused Node-backed tests for the visible MarketMatch Calls pilot UI."""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "static" / "js" / "calls.js").as_uri()
SOURCE = (ROOT / "static" / "js" / "calls.js").read_text(encoding="utf-8")
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
HAS_NODE = shutil.which("node") is not None


def _run_node(script: str):
    if not HAS_NODE:
        pytest.skip("node binary not on PATH")
    completed = subprocess.run(
        ["node", "--input-type=module"],
        input=textwrap.dedent(script).replace("CALLS_MODULE", MODULE),
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip())


def test_wav_only_control_and_client_side_limits():
    assert 'id="calls-file-input"' in INDEX
    assert 'accept=".wav"' in INDEX
    result = _run_node(
        """
        import { MAX_CALLS_WAV_BYTES, validateCallsFile } from 'CALLS_MODULE';
        const check = (name, size) => validateCallsFile({ name, size });
        console.log(JSON.stringify({
          mp3: check('call.mp3', 10),
          empty: check('call.wav', 0),
          exact: check('call.WAV', MAX_CALLS_WAV_BYTES),
          over: check('call.wav', MAX_CALLS_WAV_BYTES + 1),
        }));
        """
    )
    assert result["mp3"]["code"] == "WAV_REQUIRED"
    assert result["empty"]["code"] == "EMPTY_FILE"
    assert result["exact"] == {"ok": True}
    assert result["over"]["code"] == "FILE_TOO_LARGE"


def test_request_is_raw_same_origin_wav_without_forbidden_headers():
    result = _run_node(
        """
        import { requestCallsTranscription } from 'CALLS_MODULE';
        const file = { name: 'sample.wav', size: 46 };
        let captured;
        const fetchImpl = async (url, options) => {
          captured = { url, options, raw: options.body === file, form: options.body instanceof FormData };
          return { ok: true, status: 200, json: async () => ({ duration_ms: 1, segments: [], transcript_text: '' }) };
        };
        await requestCallsTranscription(file, { fetchImpl });
        const lowerHeaders = Object.fromEntries(Object.entries(captured.options.headers).map(([k, v]) => [k.toLowerCase(), v]));
        console.log(JSON.stringify({
          url: captured.url,
          method: captured.options.method,
          credentials: captured.options.credentials,
          headers: lowerHeaders,
          raw: captured.raw,
          form: captured.form,
          optionKeys: Object.keys(captured.options).sort(),
        }));
        """
    )
    assert result["url"] == "/api/marketmatch/stt/transcribe"
    assert result["method"] == "POST"
    assert result["credentials"] == "same-origin"
    assert result["headers"] == {"content-type": "audio/wav"}
    assert result["raw"] is True
    assert result["form"] is False
    assert not ({"authorization", "x-api-key", "x-odysseus-internal-token", "x-odysseus-owner"} & result["headers"].keys())


def test_duplicate_submit_loading_and_cancellation():
    result = _run_node(
        """
        import { createCallsController } from 'CALLS_MODULE';
        const events = [];
        const view = {
          clearResult() {}, showSelected() {}, setReady() {}, renderResult() {}, reset() {},
          setBusy(value) { events.push(['busy', value]); },
          setStatus(message, kind) { events.push(['status', kind, message]); },
        };
        let calls = 0;
        const fetchImpl = (_url, options) => new Promise((_resolve, reject) => {
          calls += 1;
          options.signal.addEventListener('abort', () => reject(new DOMException('stopped', 'AbortError')), { once: true });
        });
        const controller = createCallsController({ view, fetchImpl, copyText: async () => {} });
        controller.selectFile({ name: 'call.wav', size: 46 });
        const first = controller.submit();
        const duplicate = await controller.submit();
        const cancelled = controller.cancel();
        const firstResult = await first;
        console.log(JSON.stringify({ calls, duplicate, cancelled, firstResult, active: controller.isActive(), events }));
        """
    )
    assert result["calls"] == 1
    assert result["duplicate"] is False
    assert result["cancelled"] is True
    assert result["firstResult"] is False
    assert result["active"] is False
    assert ["busy", True] in result["events"]
    assert ["busy", False] in result["events"]
    assert any(event[:2] == ["status", "cancelled"] for event in result["events"])


def test_success_empty_result_timestamps_copy_and_reset():
    result = _run_node(
        """
        import { createCallsController, formatCallsTimestamp } from 'CALLS_MODULE';
        const rendered = [];
        const statuses = [];
        let resets = 0;
        let copied = null;
        const view = {
          clearResult() {}, showSelected() {}, setReady() {}, setBusy() {},
          setStatus(message, kind) { statuses.push([kind, message]); },
          renderResult(value) { rendered.push(value); },
          reset() { resets += 1; },
        };
        let payload = { duration_ms: 1000, segments: [{ start_ms: 0, end_ms: 600, text: 'Example' }], transcript_text: 'Example' };
        const fetchImpl = async () => ({ ok: true, status: 200, json: async () => payload });
        const controller = createCallsController({ view, fetchImpl, copyText: async (text) => { copied = text; } });
        controller.selectFile({ name: 'call.wav', size: 46 });
        const success = await controller.submit();
        const copy = await controller.copyTranscript();
        controller.reset();
        payload = { duration_ms: 1000, segments: [], transcript_text: '' };
        controller.selectFile({ name: 'silence.wav', size: 46 });
        const silence = await controller.submit();
        console.log(JSON.stringify({ success, copy, copied, resets, silence, rendered, statuses, timestamp: formatCallsTimestamp(3661001) }));
        """
    )
    assert result["success"] is True
    assert result["copy"] is True
    assert result["copied"] == "Example"
    assert result["resets"] == 1
    assert result["silence"] is True
    assert result["rendered"][0]["segments"][0] == {"start_ms": 0, "end_ms": 600, "text": "Example"}
    assert result["rendered"][1] == {"duration_ms": 1000, "segments": [], "transcript_text": ""}
    assert result["timestamp"] == "01:01:01.001"
    assert any("No speech was detected" in message for _, message in result["statuses"])


def test_fixed_http_error_mapping():
    result = _run_node(
        """
        import { fixedCallsError } from 'CALLS_MODULE';
        const statuses = [401, 403, 413, 415, 422, 429, 502, 503, 504];
        console.log(JSON.stringify(Object.fromEntries(statuses.map((status) => [status, fixedCallsError(status)]))));
        """
    )
    assert set(result) == {"401", "403", "413", "415", "422", "429", "502", "503", "504"}
    assert all(isinstance(message, str) and message for message in result.values())
    assert len(set(result.values())) == len(result)


def test_malformed_and_non_json_successes_are_fixed_safe_failures():
    result = _run_node(
        """
        import { requestCallsTranscription } from 'CALLS_MODULE';
        const file = { name: 'call.wav', size: 46 };
        const cases = [
          { ok: true, status: 200, json: async () => { throw new SyntaxError('PRIVATE_BODY'); } },
          { ok: true, status: 200, json: async () => ({ private: 'PRIVATE_OBJECT' }) },
        ];
        const errors = [];
        for (const response of cases) {
          try { await requestCallsTranscription(file, { fetchImpl: async () => response }); }
          catch (error) { errors.push({ code: error.code, message: error.message }); }
        }
        console.log(JSON.stringify(errors));
        """
    )
    assert result == [
        {"code": "MALFORMED_RESPONSE", "message": "Transcription could not be completed. Please try again."},
        {"code": "MALFORMED_RESPONSE", "message": "Transcription could not be completed. Please try again."},
    ]
    assert "PRIVATE" not in json.dumps(result)


def test_transcript_text_uses_text_content_not_html():
    result = _run_node(
        """
        import { setElementText } from 'CALLS_MODULE';
        const target = { textContent: '', innerHTML: 'unchanged' };
        const canary = '<img src=x onerror=alert(1)>';
        setElementText(target, canary);
        console.log(JSON.stringify(target));
        """
    )
    assert result == {"textContent": "<img src=x onerror=alert(1)>", "innerHTML": "unchanged"}
    assert "transcript.innerHTML" not in SOURCE
    assert "segment.text" in SOURCE


def test_calls_module_does_not_use_browser_persistence_or_logging():
    forbidden = (
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "caches.",
        "URL.createObjectURL",
        "console.",
        "FormData",
    )
    assert all(token not in SOURCE for token in forbidden)
