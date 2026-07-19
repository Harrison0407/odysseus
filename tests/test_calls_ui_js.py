"""Focused Node-backed tests for the visible MarketMatch Capture pilot UI."""

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
APP_SOURCE = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
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
        "console.",
        "FormData",
    )
    assert all(token not in SOURCE for token in forbidden)
    assert "revokeObjectURL" in SOURCE
    assert "pagehide" in SOURCE
    assert "beforeunload" in SOURCE
    assert "MutationObserver" in SOURCE


def test_microphone_controls_are_explicit_and_accessible():
    required = (
        'id="calls-record-start-btn"',
        'id="calls-record-stop-btn"',
        'id="calls-record-cancel-btn"',
        'id="calls-record-submit-btn"',
        'id="calls-record-clear-btn"',
        'id="calls-recording-indicator"',
        'id="calls-recording-elapsed"',
        'id="calls-recording-status"',
        'aria-label="Start microphone recording"',
        'aria-label="Stop microphone recording"',
        'aria-live="polite"',
    )
    assert all(marker in INDEX for marker in required)


def test_canonical_wav_math_header_and_filename():
    result = _run_node(
        """
        import {
          CALLS_WAV_SAMPLE_RATE, downmixToMono, resamplePcm, float32ToPcm16,
          encodeCanonicalWav, recordingFilename,
        } from 'CALLS_MODULE';
        const mono = downmixToMono([
          new Float32Array([1, -1, 0.5]),
          new Float32Array([-1, 1, -0.5]),
        ]);
        const resampled = resamplePcm(new Float32Array(48000), 48000);
        const pcm = float32ToPcm16(new Float32Array([-2, -1, -0.5, 0, 0.5, 1, 2]));
        const wav = encodeCanonicalWav(new Float32Array([-1, 0, 1]));
        const view = new DataView(wav.buffer, wav.byteOffset, wav.byteLength);
        const ascii = (offset, length) => String.fromCharCode(...wav.slice(offset, offset + length));
        console.log(JSON.stringify({
          sampleRate: CALLS_WAV_SAMPLE_RATE,
          mono: Array.from(mono),
          resampledLength: resampled.length,
          pcm: Array.from(pcm),
          header: {
            riff: ascii(0, 4), wave: ascii(8, 4), fmt: ascii(12, 4), data: ascii(36, 4),
            riffSize: view.getUint32(4, true), fmtSize: view.getUint32(16, true),
            format: view.getUint16(20, true), channels: view.getUint16(22, true),
            rate: view.getUint32(24, true), byteRate: view.getUint32(28, true),
            alignment: view.getUint16(32, true), bits: view.getUint16(34, true),
            dataSize: view.getUint32(40, true), total: wav.byteLength,
          },
          filename: recordingFilename(new Date('2026-07-18T12:34:56Z')),
        }));
        """
    )
    assert result["sampleRate"] == 16000
    assert result["mono"] == [0, 0, 0]
    assert result["resampledLength"] == 16000
    assert result["pcm"] == [-32768, -32768, -16384, 0, 16384, 32767, 32767]
    assert result["header"] == {
        "riff": "RIFF",
        "wave": "WAVE",
        "fmt": "fmt ",
        "data": "data",
        "riffSize": 42,
        "fmtSize": 16,
        "format": 1,
        "channels": 1,
        "rate": 16000,
        "byteRate": 32000,
        "alignment": 2,
        "bits": 16,
        "dataSize": 6,
        "total": 50,
    }
    assert result["filename"] == "marketmatch-recording-20260718-123456.wav"


def test_generated_audio_empty_and_size_limits_fail_safely():
    result = _run_node(
        """
        import {
          MAX_CALLS_WAV_BYTES, decodeRecordingToCanonicalWav, encodeCanonicalWav,
          validateGeneratedWavBytes,
        } from 'CALLS_MODULE';
        const errors = [];
        try { encodeCanonicalWav(new Float32Array()); }
        catch (error) { errors.push({ code: error.code, message: error.message }); }
        try { validateGeneratedWavBytes({ byteLength: MAX_CALLS_WAV_BYTES + 1 }); }
        catch (error) { errors.push({ code: error.code, message: error.message }); }
        try { await decodeRecordingToCanonicalWav(new Blob([]), () => null); }
        catch (error) { errors.push({ code: error.code, message: error.message }); }
        try {
          await decodeRecordingToCanonicalWav(new Blob(['native']), () => ({
            async decodeAudioData() { throw new Error('PRIVATE_DECODER_DETAIL'); },
            async close() {},
          }));
        } catch (error) { errors.push({ code: error.code, message: error.message }); }
        console.log(JSON.stringify(errors));
        """
    )
    assert [item["code"] for item in result] == [
        "RECORDING_EMPTY",
        "RECORDING_TOO_LARGE",
        "RECORDING_EMPTY",
        "RECORDING_FAILED",
    ]
    assert "PRIVATE" not in json.dumps(result)


def test_recorded_container_is_decoded_downmixed_and_resampled_before_wav_encoding():
    result = _run_node(
        """
        import { decodeRecordingToCanonicalWav } from 'CALLS_MODULE';
        let closed = 0;
        const left = new Float32Array(48000).fill(0.5);
        const right = new Float32Array(48000).fill(-0.5);
        const context = {
          async decodeAudioData() {
            return {
              sampleRate: 48000, numberOfChannels: 2, length: 48000,
              getChannelData(index) { return index === 0 ? left : right; },
            };
          },
          async close() { closed += 1; },
        };
        const prepared = await decodeRecordingToCanonicalWav(new Blob(['native-container']), () => context);
        const view = new DataView(prepared.bytes.buffer, prepared.bytes.byteOffset, prepared.bytes.byteLength);
        console.log(JSON.stringify({
          duration: prepared.durationMs, bytes: prepared.bytes.byteLength, closed,
          rate: view.getUint32(24, true), channels: view.getUint16(22, true),
          firstSample: view.getInt16(44, true), dataSize: view.getUint32(40, true),
        }));
        """
    )
    assert result == {
        "duration": 1000,
        "bytes": 32044,
        "closed": 1,
        "rate": 16000,
        "channels": 1,
        "firstSample": 0,
        "dataSize": 32000,
    }


def test_recording_requires_user_action_transitions_and_reuses_raw_wav_request():
    result = _run_node(
        """
        import { createCallsController } from 'CALLS_MODULE';
        const states = [];
        const statuses = [];
        const tracks = [{ stops: 0, stop() { this.stops += 1; } }, { stops: 0, stop() { this.stops += 1; } }];
        const stream = { getTracks: () => tracks };
        let permissionRequests = 0;
        let startTimeslice = null;
        class Recorder {
          constructor() { this.state = 'inactive'; this.mimeType = 'audio/native'; }
          start(timeslice) { this.state = 'recording'; startTimeslice = timeslice; }
          stop() {
            this.state = 'inactive';
            if (this.ondataavailable) this.ondataavailable({ data: new Blob(['native-audio']) });
            if (this.onstop) this.onstop();
          }
        }
        const view = {
          clearResult() {}, clearRecording() {}, clearUpload() {}, showSelected() {},
          showRecording(name, size, duration, url) { states.push(['ready-file', name, size, duration, url]); },
          setReady(value, source) { states.push(['ready', value, source]); },
          setBusy(value) { states.push(['busy', value]); },
          setStatus(message, kind) { statuses.push([kind, message]); },
          setRecordingStatus(message, kind) { statuses.push([kind, message]); },
          setRecordingState(value) { states.push(['state', value]); },
          setRecordingElapsed(value) { states.push(['elapsed', value]); },
          renderResult(value) { states.push(['result', value.transcript_text]); },
          reset() {},
        };
        let request;
        const fetchImpl = async (url, options) => {
          request = { url, options, rawName: options.body.name, form: options.body instanceof FormData };
          return { ok: true, status: 200, json: async () => ({ duration_ms: 1, segments: [], transcript_text: '' }) };
        };
        const controller = createCallsController({
          view, fetchImpl,
          mediaDevices: { async getUserMedia() { permissionRequests += 1; return stream; } },
          MediaRecorderClass: Recorder,
          decodeRecording: async () => ({ bytes: new Uint8Array(46), durationMs: 1000 }),
          createWavFile: (bytes, name) => ({ name, size: bytes.byteLength, type: 'audio/wav' }),
          createObjectURL: () => 'blob:preview', revokeObjectURL() {},
          setIntervalFn: () => 7, clearIntervalFn() {}, now: () => 1234567890000,
          isSecureContext: true, copyText: async () => {},
        });
        const beforeAction = permissionRequests;
        const starting = controller.startRecording();
        const duringPermission = permissionRequests;
        const duplicate = await controller.startRecording();
        const started = await starting;
        const overlapSubmit = await controller.submit();
        const stopped = await controller.stopRecording();
        const submitted = await controller.submit();
        const headers = Object.fromEntries(Object.entries(request.options.headers).map(([k, v]) => [k.toLowerCase(), v]));
        console.log(JSON.stringify({
          beforeAction, duringPermission, duplicate, started, overlapSubmit, stopped, submitted,
          trackStops: tracks.map((track) => track.stops), startTimeslice, states, statuses,
          request: { url: request.url, method: request.options.method, credentials: request.options.credentials,
            headers, rawName: request.rawName, form: request.form },
        }));
        """
    )
    assert result["beforeAction"] == 0
    assert result["duringPermission"] == 1
    assert result["duplicate"] is False
    assert result["started"] is True
    assert result["overlapSubmit"] is False
    assert result["stopped"] is True
    assert result["submitted"] is True
    assert result["trackStops"] == [1, 1]
    assert result["startTimeslice"] == 250
    assert ["state", "permission"] in result["states"]
    assert ["state", "recording"] in result["states"]
    assert ["state", "processing"] in result["states"]
    assert ["state", "ready"] in result["states"]
    assert result["request"]["url"] == "/api/marketmatch/stt/transcribe"
    assert result["request"]["method"] == "POST"
    assert result["request"]["credentials"] == "same-origin"
    assert result["request"]["headers"] == {"content-type": "audio/wav"}
    assert result["request"]["rawName"].endswith(".wav")
    assert result["request"]["form"] is False


def test_recording_cancel_clear_panel_close_and_request_overlap_cleanup():
    result = _run_node(
        """
        import { createCallsController } from 'CALLS_MODULE';
        const stoppedTracks = [];
        const revoked = [];
        const clearedTimers = [];
        const aborts = [];
        const states = [];
        let streamNumber = 0;
        class Recorder {
          constructor(stream) { this.stream = stream; this.state = 'inactive'; this.mimeType = 'audio/native'; }
          start() { this.state = 'recording'; }
          stop() {
            this.state = 'inactive';
            if (this.ondataavailable) this.ondataavailable({ data: new Blob(['native']) });
            if (this.onstop) this.onstop();
          }
        }
        const view = {
          clearResult() {}, clearRecording() { states.push('cleared-recording'); }, clearUpload() {},
          showSelected() {}, showRecording() {}, setReady() {}, setBusy() {}, setStatus() {},
          setRecordingStatus(_message, kind) { states.push(kind); },
          setRecordingState(value) { states.push(value); }, setRecordingElapsed() {}, renderResult() {}, reset() { states.push('reset'); },
        };
        const mediaDevices = { async getUserMedia() {
          streamNumber += 1;
          const id = streamNumber;
          return { getTracks: () => [{ stop() { stoppedTracks.push(id); } }] };
        } };
        let pendingReject;
        const fetchImpl = (_url, options) => new Promise((_resolve, reject) => {
          pendingReject = reject;
          options.signal.addEventListener('abort', () => { aborts.push(true); reject(new DOMException('stop', 'AbortError')); });
        });
        const controller = createCallsController({
          view, mediaDevices, MediaRecorderClass: Recorder, fetchImpl,
          decodeRecording: async () => ({ bytes: new Uint8Array(46), durationMs: 1000 }),
          createWavFile: (bytes, name) => ({ name, size: bytes.byteLength, type: 'audio/wav' }),
          createObjectURL: () => 'blob:recording', revokeObjectURL: (url) => revoked.push(url),
          setIntervalFn: () => 91, clearIntervalFn: (id) => clearedTimers.push(id), now: () => 1,
          isSecureContext: true, copyText: async () => {},
        });
        await controller.startRecording();
        const cancelled = controller.cancelRecording();
        await controller.startRecording();
        const closed = controller.onPanelHidden();
        await controller.startRecording();
        await controller.stopRecording();
        const submitting = controller.submit();
        const overlapStart = await controller.startRecording();
        controller.reset();
        const submitResult = await submitting;
        await controller.startRecording();
        controller.reset();
        await controller.startRecording();
        controller.destroy();
        if (pendingReject) pendingReject(new DOMException('stop', 'AbortError'));
        console.log(JSON.stringify({
          cancelled, closed, overlapStart, submitResult, stoppedTracks, revoked, clearedTimers, aborts, states,
          finalState: controller.getRecordingState(),
        }));
        """
    )
    assert result["cancelled"] is True
    assert result["closed"] is True
    assert result["overlapStart"] is False
    assert result["submitResult"] is False
    assert result["stoppedTracks"] == [1, 2, 3, 4, 5]
    assert result["revoked"] == ["blob:recording"]
    assert result["clearedTimers"] == [91, 91, 91, 91, 91]
    assert result["aborts"] == [True]
    assert "cancelled" in result["states"]
    assert "reset" in result["states"]
    assert result["finalState"] == "idle"


def test_microphone_permission_and_device_errors_are_fixed_and_safe():
    result = _run_node(
        """
        import { createCallsController } from 'CALLS_MODULE';
        const messages = [];
        const view = {
          clearResult() {}, clearRecording() {}, clearUpload() {}, setReady() {}, setRecordingState() {},
          setRecordingStatus(message, kind) { messages.push([kind, message]); },
        };
        const attempt = async (name) => {
          const controller = createCallsController({
            view, MediaRecorderClass: class {}, isSecureContext: true,
            mediaDevices: { async getUserMedia() { throw Object.assign(new Error('PRIVATE_DEVICE_DETAIL'), { name }); } },
          });
          return controller.startRecording();
        };
        await attempt('NotAllowedError');
        await attempt('NotFoundError');
        await attempt('NotReadableError');
        const unsupported = createCallsController({ view, mediaDevices: null, MediaRecorderClass: null });
        await unsupported.startRecording();
        console.log(JSON.stringify(messages));
        """
    )
    assert result == [
        ["loading", "Waiting for microphone permission…"],
        ["error", "Microphone permission was denied. Allow access and try again."],
        ["loading", "Waiting for microphone permission…"],
        ["error", "No microphone is available."],
        ["loading", "Waiting for microphone permission…"],
        ["error", "The microphone could not be started. Check the device and try again."],
        ["error", "Microphone recording is unavailable in this browser."],
    ]
    assert "PRIVATE" not in json.dumps(result)


def test_library_save_controls_are_explicit_and_unavailable_before_success():
    required = (
        'id="calls-save"',
        'id="calls-save-title"',
        'id="calls-save-btn"',
        'id="calls-save-status"',
        'for="calls-save-title"',
        'aria-live="polite"',
        'Save Capture to Library',
    )
    assert all(marker in INDEX for marker in required)
    assert '<section id="calls-save" class="calls-card calls-save"' in INDEX
    assert 'id="calls-save-btn" class="calls-button calls-button-primary" disabled' in INDEX
    result = _run_node(
        """
        import { createCallsController } from 'CALLS_MODULE';
        let requests = 0;
        const controller = createCallsController({
          view: {},
          fetchImpl: async () => { requests += 1; return { ok: true, json: async () => ({ id: 'doc' }) }; },
        });
        const before = await controller.saveToLibrary('Premature');
        console.log(JSON.stringify({ before, requests }));
        """
    )
    assert result == {"before": False, "requests": 0}


def test_document_save_request_reuses_owner_scoped_json_contract_and_complete_content():
    result = _run_node(
        """
        import {
          buildCaptureDocumentContent, defaultCallsDocumentTitle,
          requestCallsDocumentSave,
        } from 'CALLS_MODULE';
        const transcript = '<b>Complete transcript</b>';
        const transcription = {
          duration_ms: 3723456,
          transcript_text: transcript,
          segments: [
            { start_ms: 0, end_ms: 3200, text: '<b>Complete ' },
            { start_ms: 3200, end_ms: 6543, text: 'transcript</b>' },
          ],
        };
        const createdAt = new Date(2026, 6, 18, 14, 5, 0);
        let captured;
        const fetchImpl = async (url, options) => {
          captured = { url, options, form: options.body instanceof FormData };
          return { ok: true, status: 200, json: async () => ({ id: 'doc-123', owner: 'PRIVATE_OWNER' }) };
        };
        const saved = await requestCallsDocumentSave(
          { title: '  Calls Persistence Test  ', result: transcription, createdAt },
          { fetchImpl },
        );
        const body = JSON.parse(captured.options.body);
        const headers = Object.fromEntries(
          Object.entries(captured.options.headers).map(([key, value]) => [key.toLowerCase(), value]),
        );
        console.log(JSON.stringify({
          saved,
          defaultTitle: defaultCallsDocumentTitle(createdAt),
          directContent: buildCaptureDocumentContent({
            title: 'Calls Persistence Test', result: transcription, createdAt,
          }),
          silenceContent: buildCaptureDocumentContent({
            title: 'Silence', result: { duration_ms: 1000, transcript_text: '', segments: [] }, createdAt,
          }),
          request: {
            url: captured.url,
            method: captured.options.method,
            credentials: captured.options.credentials,
            headers,
            optionKeys: Object.keys(captured.options).sort(),
            form: captured.form,
            body,
          },
        }));
        """
    )
    request = result["request"]
    assert result["saved"] == {"id": "doc-123"}
    assert result["defaultTitle"] == "Capture — 2026-07-18 14:05"
    assert request["url"] == "/api/document"
    assert request["method"] == "POST"
    assert request["credentials"] == "same-origin"
    assert request["headers"] == {"content-type": "application/json"}
    assert request["form"] is False
    assert request["body"]["title"] == "Calls Persistence Test"
    assert request["body"]["language"] == "markdown"
    assert set(request["body"]) == {"title", "language", "content"}
    assert result["directContent"] == request["body"]["content"]
    content = request["body"]["content"]
    assert "AI-generated transcript" in content
    assert "Review before relying" in content
    assert "Source: MarketMatch Capture" in content
    assert "Capture title: Calls Persistence Test" in content
    assert "Created: 2026-07-18 14:05" in content
    assert "Duration: 01:02:03.456" in content
    assert "<b>Complete transcript</b>" in content
    assert "[00:00.000 – 00:03.200] <b>Complete " in content
    assert "[00:03.200 – 00:06.543] transcript</b>" in content
    assert "(No speech was detected.)" in result["silenceContent"]
    assert "(No speech segments were detected.)" in result["silenceContent"]
    assert "audio" not in request["body"]
    assert "owner" not in request["body"]
    assert "session_id" not in request["body"]
    assert "Complete transcript" not in request["url"]
    assert not ({"authorization", "x-api-key", "x-odysseus-internal-token", "x-odysseus-owner"} & request["headers"].keys())


def test_save_title_validation_duplicate_prevention_success_and_new_result_reset():
    result = _run_node(
        """
        import { createCallsController } from 'CALLS_MODULE';
        const events = [];
        const view = {
          clearResult() {}, clearSave() { events.push(['clear-save']); }, showSelected() {}, setReady() {},
          setBusy() {}, setStatus() {}, renderResult() {},
          showSave(title) { events.push(['show-save', title]); },
          setSaveBusy(value) { events.push(['save-busy', value]); },
          setSaved(value) { events.push(['saved', value]); },
          setSaveStatus(message, kind) { events.push(['save-status', kind, message]); },
          reset() {},
        };
        let transcriptionNumber = 0;
        let saveRequests = 0;
        let resolveSave;
        const fetchImpl = async (url, options) => {
          if (url === '/api/marketmatch/stt/transcribe') {
            transcriptionNumber += 1;
            const text = `Transcript ${transcriptionNumber}`;
            return { ok: true, status: 200, json: async () => ({
              duration_ms: 1000,
              transcript_text: text,
              segments: [{ start_ms: 0, end_ms: 1000, text }],
            }) };
          }
          saveRequests += 1;
          return new Promise((resolve) => { resolveSave = () => resolve({ ok: true, status: 200, json: async () => ({ id: `doc-${saveRequests}` }) }); });
        };
        const controller = createCallsController({ view, fetchImpl, now: () => new Date(2026, 6, 18, 14, 5).getTime() });
        controller.selectFile({ name: 'call.wav', size: 46 });
        await controller.submit();
        const emptyTitle = await controller.saveToLibrary('   ');
        const pending = controller.saveToLibrary('  Calls Persistence Test  ');
        const duplicatePending = await controller.saveToLibrary('Duplicate');
        resolveSave();
        const saved = await pending;
        const duplicateComplete = await controller.saveToLibrary('Duplicate');
        await controller.submit();
        const secondPending = controller.saveToLibrary('Second result');
        resolveSave();
        const secondSaved = await secondPending;
        console.log(JSON.stringify({
          emptyTitle, duplicatePending, saved, duplicateComplete, secondSaved,
          saveRequests, events,
        }));
        """
    )
    assert result["emptyTitle"] is False
    assert result["duplicatePending"] is False
    assert result["saved"] is True
    assert result["duplicateComplete"] is False
    assert result["secondSaved"] is True
    assert result["saveRequests"] == 2
    assert any(event[:2] == ["save-status", "error"] and "title" in event[2] for event in result["events"])
    assert ["saved", True] in result["events"]
    assert sum(event[0] == "show-save" for event in result["events"]) == 2
    assert sum(event == ["clear-save"] for event in result["events"]) >= 2


def test_save_failure_is_safe_preserves_transcript_and_supports_copy():
    result = _run_node(
        """
        import { createCallsController, requestCallsDocumentSave } from 'CALLS_MODULE';
        const rendered = [];
        const saveStatuses = [];
        let copied = '';
        const payload = {
          duration_ms: 1000,
          transcript_text: 'Keep this transcript',
          segments: [{ start_ms: 0, end_ms: 1000, text: 'Keep this transcript' }],
        };
        let requestNumber = 0;
        const fetchImpl = async (url) => {
          requestNumber += 1;
          if (url.includes('transcribe')) return { ok: true, status: 200, json: async () => payload };
          return { ok: false, status: 500, json: async () => ({ detail: 'PRIVATE_BACKEND_EXCEPTION' }) };
        };
        const view = {
          clearResult() {}, clearSave() {}, showSelected() {}, setReady() {}, setBusy() {}, setStatus() {},
          renderResult(value) { rendered.push(value.transcript_text); }, showSave() {}, setSaveBusy() {},
          setSaveStatus(message, kind) { saveStatuses.push([kind, message]); }, reset() {},
        };
        const controller = createCallsController({ view, fetchImpl, copyText: async (text) => { copied = text; } });
        controller.selectFile({ name: 'call.wav', size: 46 });
        await controller.submit();
        const saved = await controller.saveToLibrary('Safe failure');
        const copiedResult = await controller.copyTranscript();
        const directErrors = [];
        for (const response of [
          { ok: false, status: 401 },
          { ok: false, status: 403 },
          { ok: false, status: 413 },
          { ok: false, status: 422 },
          { ok: true, status: 200, json: async () => { throw new Error('PRIVATE_HTML'); } },
        ]) {
          try {
            await requestCallsDocumentSave(
              { title: 'Title', result: payload, createdAt: new Date(0) },
              { fetchImpl: async () => response },
            );
          } catch (error) { directErrors.push([error.code, error.message]); }
        }
        console.log(JSON.stringify({ saved, copiedResult, copied, rendered, saveStatuses, directErrors, requestNumber }));
        """
    )
    assert result["saved"] is False
    assert result["copiedResult"] is True
    assert result["copied"] == "Keep this transcript"
    assert result["rendered"] == ["Keep this transcript"]
    assert result["requestNumber"] == 2
    assert result["saveStatuses"][-1] == ["error", "The Capture could not be saved. Please try again."]
    assert [code for code, _ in result["directErrors"]] == [
        "DOCUMENT_HTTP_401", "DOCUMENT_HTTP_403", "DOCUMENT_HTTP_413",
        "DOCUMENT_HTTP_422", "DOCUMENT_MALFORMED_RESPONSE",
    ]
    assert "PRIVATE" not in json.dumps(result)


def test_clear_and_panel_close_abort_pending_save_and_clear_save_state():
    result = _run_node(
        """
        import { createCallsController } from 'CALLS_MODULE';
        const events = [];
        let saveAborts = 0;
        const view = {
          clearResult() {}, clearSave() { events.push('clear-save'); }, showSelected() {}, setReady() {},
          setBusy() {}, setStatus() {}, renderResult() {}, showSave() {},
          setSaveBusy(value) { events.push(['busy', value]); },
          setSaveStatus(message, kind) { events.push(['status', kind, message]); }, reset() { events.push('reset'); },
        };
        const fetchImpl = (url, options) => {
          if (url.includes('transcribe')) return Promise.resolve({
            ok: true, status: 200, json: async () => ({
              duration_ms: 1000, transcript_text: 'Result',
              segments: [{ start_ms: 0, end_ms: 1000, text: 'Result' }],
            }),
          });
          return new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => {
            saveAborts += 1;
            reject(new DOMException('private abort detail', 'AbortError'));
          }, { once: true }));
        };
        const controller = createCallsController({ view, fetchImpl });
        controller.selectFile({ name: 'call.wav', size: 46 });
        await controller.submit();
        const clearing = controller.saveToLibrary('First');
        controller.reset();
        const clearedResult = await clearing;
        controller.selectFile({ name: 'call.wav', size: 46 });
        await controller.submit();
        const closing = controller.saveToLibrary('Second');
        controller.onPanelHidden();
        const closedResult = await closing;
        console.log(JSON.stringify({
          clearedResult, closedResult, saveAborts, events,
          active: controller.isSaveActive(),
        }));
        """
    )
    assert result["clearedResult"] is False
    assert result["closedResult"] is False
    assert result["saveAborts"] == 2
    assert result["active"] is False
    assert "reset" in result["events"]
    assert "clear-save" in result["events"]
    assert any(event[:2] == ["status", "cancelled"] for event in result["events"] if isinstance(event, list))


def test_saved_history_request_is_owner_implicit_bounded_and_marker_filtered():
    result = _run_node(
        """
        import {
          buildCallsDocumentContent, filterCallsHistoryDocuments,
          isCallsTranscriptDocument, requestCallsHistory,
        } from 'CALLS_MODULE';
        const transcription = {
          duration_ms: 1000,
          transcript_text: 'History test',
          segments: [{ start_ms: 0, end_ms: 1000, text: 'History test' }],
        };
        const preview = buildCallsDocumentContent(transcription, new Date(0)).slice(0, 500);
        const callsDoc = (id, title) => ({
          id, title, language: 'markdown', preview,
          created_at: '2026-07-18T12:00:00Z', updated_at: '2026-07-18T13:00:00Z',
        });
        const documents = [
          callsDoc('doc-a', 'Site Coordination July 18'),
          callsDoc('doc-a', 'Duplicate identity'),
          { id: 'normal-call', title: 'Call notes', language: 'markdown', preview: '# Ordinary notes' },
          { id: 'almost', title: 'MarketMatch Calls Transcript', language: 'markdown', preview: '# MarketMatch Calls Transcript' },
          null,
          ...Array.from({ length: 22 }, (_, index) => callsDoc(`doc-${index}`, `Custom ${index}`)),
        ];
        let captured;
        const fetchImpl = async (url, options) => {
          captured = { url, options };
          return { ok: true, status: 200, json: async () => ({ documents, total: documents.length }) };
        };
        const history = await requestCallsHistory({ fetchImpl });
        const direct = filterCallsHistoryDocuments({ documents });
        console.log(JSON.stringify({
          history, direct,
          markerRecognized: isCallsTranscriptDocument(callsDoc('marker', 'Arbitrary title')),
          unrelatedRecognized: isCallsTranscriptDocument(documents[2]),
          request: {
            url: captured.url,
            method: captured.options.method,
            credentials: captured.options.credentials,
            keys: Object.keys(captured.options).sort(),
            hasHeaders: Object.hasOwn(captured.options, 'headers'),
            hasBody: Object.hasOwn(captured.options, 'body'),
          },
        }));
        """
    )
    request = result["request"]
    assert request["url"] == "/api/documents/library?search=MarketMatch&sort=recent&offset=0&limit=20"
    assert request["method"] == "GET"
    assert request["credentials"] == "same-origin"
    assert request["hasHeaders"] is False
    assert request["hasBody"] is False
    assert result["markerRecognized"] is True
    assert result["unrelatedRecognized"] is False
    assert result["history"] == result["direct"]
    assert len(result["history"]) <= 20
    assert result["history"][0]["title"] == "Site Coordination July 18"
    assert len({item["id"] for item in result["history"]}) == len(result["history"])
    assert "normal-call" not in {item["id"] for item in result["history"]}
    assert "almost" not in {item["id"] for item in result["history"]}
    assert all(key not in request["url"].lower() for key in ("owner", "username", "session_id", "impersonat"))
    assert "History test" not in request["url"]


def test_history_lifecycle_prevents_overlap_aborts_stale_and_reloads_after_reopen():
    result = _run_node(
        """
        import { buildCallsDocumentContent, createCallsController } from 'CALLS_MODULE';
        const preview = buildCallsDocumentContent({
          duration_ms: 1000, transcript_text: '', segments: [],
        }, new Date(0)).slice(0, 500);
        const renders = [];
        const states = [];
        let requests = 0;
        const fetchImpl = (_url, options) => {
          requests += 1;
          if (requests === 1) {
            return new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => {
              reject(new DOMException('PRIVATE_STALE_RESPONSE', 'AbortError'));
            }, { once: true }));
          }
          return Promise.resolve({ ok: true, status: 200, json: async () => ({ documents: requests === 2 ? [] : [{
            id: 'doc-reopen', title: 'Window Installation Review', preview,
            created_at: '2026-07-18T12:00:00Z', updated_at: null,
          }] }) });
        };
        const view = {
          setHistoryLoading(busy, refreshing) { states.push(['loading', busy, refreshing]); },
          renderHistory(items) { renders.push(items.map((item) => item.title)); },
          setHistoryError(message) { states.push(['error', message]); },
        };
        const controller = createCallsController({ view, fetchImpl });
        const first = controller.onPanelOpened();
        const duplicate = await controller.loadHistory({ refreshing: true });
        controller.onPanelHidden();
        const stale = await first;
        const empty = await controller.onPanelOpened();
        const refreshed = await controller.loadHistory({ refreshing: true });
        console.log(JSON.stringify({
          duplicate, stale, empty, refreshed, requests, renders, states,
          active: controller.isHistoryActive(),
        }));
        """
    )
    assert result["duplicate"] is False
    assert result["stale"] is False
    assert result["empty"] is True
    assert result["refreshed"] is True
    assert result["requests"] == 3
    assert result["renders"] == [[], ["Window Installation Review"]]
    assert result["active"] is False
    assert not any("PRIVATE" in str(state) for state in result["states"])
    assert ["loading", True, False] in result["states"]
    assert ["loading", True, True] in result["states"]


def test_history_failures_are_fixed_safe_and_do_not_break_calls_features():
    result = _run_node(
        """
        import { createCallsController, requestCallsHistory } from 'CALLS_MODULE';
        const errors = [];
        for (const response of [
          { ok: false, status: 401 },
          { ok: false, status: 403 },
          { ok: false, status: 500, json: async () => ({ detail: 'PRIVATE_BACKEND' }) },
          { ok: true, status: 200, json: async () => { throw new Error('PRIVATE_HTML'); } },
          { ok: true, status: 200, json: async () => ({ documents: 'PRIVATE_BAD_SHAPE' }) },
        ]) {
          try { await requestCallsHistory({ fetchImpl: async () => response }); }
          catch (error) { errors.push([error.code, error.message]); }
        }
        const statuses = [];
        let copied = '';
        const fetchImpl = async (url) => {
          if (url.includes('/api/documents/library')) return { ok: false, status: 500 };
          if (url === '/api/document') return { ok: true, status: 200, json: async () => ({ id: 'saved-doc' }) };
          return { ok: true, status: 200, json: async () => ({
            duration_ms: 1000, transcript_text: 'Still works',
            segments: [{ start_ms: 0, end_ms: 1000, text: 'Still works' }],
          }) };
        };
        const view = {
          setHistoryLoading() {}, setHistoryError(message) { statuses.push(message); },
          clearResult() {}, clearSave() {}, showSelected() {}, setReady() {}, setBusy() {},
          setStatus() {}, renderResult() {}, showSave() {}, setSaveBusy() {}, reset() {},
        };
        const controller = createCallsController({ view, fetchImpl, copyText: async (text) => { copied = text; } });
        const history = await controller.onPanelOpened();
        controller.selectFile({ name: 'call.wav', size: 46 });
        const transcribed = await controller.submit();
        const copy = await controller.copyTranscript();
        const saved = await controller.saveToLibrary('History failure still saves');
        await new Promise((resolve) => setTimeout(resolve, 0));
        console.log(JSON.stringify({ errors, statuses, history, transcribed, copy, copied, saved }));
        """
    )
    assert [code for code, _ in result["errors"]] == [
        "HISTORY_HTTP_401", "HISTORY_HTTP_403", "HISTORY_HTTP_500",
        "HISTORY_MALFORMED_RESPONSE", "HISTORY_MALFORMED_RESPONSE",
    ]
    assert "PRIVATE" not in json.dumps(result)
    assert result["history"] is False
    assert result["transcribed"] is True
    assert result["copy"] is True
    assert result["copied"] == "Still works"
    assert result["saved"] is True
    assert result["statuses"] == [
        "Saved Captures could not be loaded. Please try again.",
        "Saved Captures could not be loaded. Please try again.",
    ]


def test_successful_save_refreshes_history_once_failed_save_does_not_and_clear_never_deletes():
    result = _run_node(
        """
        import { buildCallsDocumentContent, createCallsController } from 'CALLS_MODULE';
        const preview = buildCallsDocumentContent({
          duration_ms: 1000, transcript_text: 'Saved result',
          segments: [{ start_ms: 0, end_ms: 1000, text: 'Saved result' }],
        }, new Date(0)).slice(0, 500);
        const renders = [];
        const requests = [];
        let historyRequests = 0;
        let saveShouldFail = false;
        const fetchImpl = async (url, options) => {
          requests.push([url, options.method]);
          if (url.includes('/api/documents/library')) {
            historyRequests += 1;
            const docs = historyRequests === 1 ? [] : [
              { id: 'new-doc', title: 'New history item', preview },
              { id: 'new-doc', title: 'Duplicate result', preview },
            ];
            return { ok: true, status: 200, json: async () => ({ documents: docs }) };
          }
          if (url === '/api/document') {
            return saveShouldFail
              ? { ok: false, status: 500 }
              : { ok: true, status: 200, json: async () => ({ id: 'new-doc' }) };
          }
          return { ok: true, status: 200, json: async () => ({
            duration_ms: 1000, transcript_text: 'Saved result',
            segments: [{ start_ms: 0, end_ms: 1000, text: 'Saved result' }],
          }) };
        };
        const view = {
          setHistoryLoading() {}, setHistoryError() {}, renderHistory(items) { renders.push(items.map((item) => item.id)); },
          clearResult() {}, clearSave() {}, showSelected() {}, setReady() {}, setBusy() {}, setStatus() {},
          renderResult() {}, showSave() {}, setSaveBusy() {}, setSaved() {}, setSaveStatus() {}, reset() {},
        };
        const controller = createCallsController({ view, fetchImpl });
        await controller.onPanelOpened();
        controller.selectFile({ name: 'call.wav', size: 46 });
        await controller.submit();
        const saved = await controller.saveToLibrary('Saved transcript');
        await new Promise((resolve) => setTimeout(resolve, 0));
        controller.reset();
        saveShouldFail = true;
        controller.selectFile({ name: 'call.wav', size: 46 });
        await controller.submit();
        const failed = await controller.saveToLibrary('Failed transcript');
        await new Promise((resolve) => setTimeout(resolve, 0));
        console.log(JSON.stringify({ saved, failed, historyRequests, renders, requests }));
        """
    )
    assert result["saved"] is True
    assert result["failed"] is False
    assert result["historyRequests"] == 2
    assert result["renders"] == [[], ["new-doc"]]
    assert not any(method in {"DELETE", "PUT", "PATCH"} for _, method in result["requests"])


def test_history_open_uses_existing_document_module_and_accessible_safe_ui():
    result = _run_node(
        """
        import { createCallsController } from 'CALLS_MODULE';
        const opened = [];
        const errors = [];
        const controller = createCallsController({
          view: { setHistoryError(message) { errors.push(message); } },
          openDocument: async (id) => { opened.push(id); },
        });
        const valid = await controller.openHistoryDocument('internal-doc-id');
        const invalid = await controller.openHistoryDocument('');
        console.log(JSON.stringify({ valid, invalid, opened, errors }));
        """
    )
    assert result == {"valid": True, "invalid": False, "opened": ["internal-doc-id"], "errors": []}
    required = (
        'id="calls-history-heading"',
        'id="calls-history-refresh-btn"',
        'id="calls-history-status"',
        'id="calls-history-empty"',
        'id="calls-history-list"',
        'aria-label="Saved Captures"',
        'aria-live="polite"',
    )
    assert all(marker in INDEX for marker in required)
    assert "open.setAttribute('aria-label', `Open ${documentValue.title} from Library`)" in SOURCE
    assert "setElementText(title, documentValue.title)" in SOURCE
    assert "documentModule.loadDocument(documentId)" in APP_SOURCE
    assert "callsModule.init(document" in APP_SOURCE


def test_analysis_controls_are_explicit_accessible_and_render_with_text_nodes():
    required = (
        'id="calls-analysis"',
        'id="calls-analysis-generate-btn"',
        'id="calls-analysis-regenerate-btn"',
        'id="calls-analysis-copy-btn"',
        'id="calls-analysis-status"',
        'id="calls-analysis-summary-heading">Summary',
        'id="calls-analysis-decisions-heading">Decisions',
        'id="calls-analysis-actions-heading">Action Items',
        'id="calls-analysis-questions-heading">Open Questions',
        'aria-live="polite"',
    )
    assert all(marker in INDEX for marker in required)
    assert 'id="calls-analysis-generate-btn" class="calls-button calls-button-primary" disabled' in INDEX
    assert "setElementText(analysisSummary, value.summary)" in SOURCE
    assert "setElementText(item, itemValue)" in SOURCE
    assert "analysisSummary.innerHTML" not in SOURCE
    assert "analysisResult.innerHTML" not in SOURCE


def test_analysis_request_is_exact_bounded_same_origin_json_and_strictly_validated():
    result = _run_node(
        """
        import {
          MAX_CALLS_ANALYSIS_TRANSCRIPT_CHARS,
          formatCallsAnalysisText,
          requestCallsAnalysis,
        } from 'CALLS_MODULE';
        const transcript = 'Complete transcript only';
        const valid = {
          summary: '<b>Summary stays text</b>',
          decisions: [],
          action_items: [{ task: 'Send drawing', owner: null, due_date: null }],
          open_questions: [],
        };
        let captured;
        let calls = 0;
        const fetchImpl = async (url, options) => {
          calls += 1;
          captured = { url, options };
          return { ok: true, status: 200, json: async () => valid };
        };
        const analysis = await requestCallsAnalysis(transcript, { fetchImpl });
        let oversized;
        try {
          await requestCallsAnalysis('x'.repeat(MAX_CALLS_ANALYSIS_TRANSCRIPT_CHARS + 1), { fetchImpl });
        } catch (error) {
          oversized = { code: error.code, message: error.message };
        }
        const firstCaptured = captured;
        let unicodeBoundaryCalls = 0;
        await requestCallsAnalysis('😀'.repeat(MAX_CALLS_ANALYSIS_TRANSCRIPT_CHARS), {
          fetchImpl: async () => {
            unicodeBoundaryCalls += 1;
            return { ok: true, status: 200, json: async () => valid };
          },
        });
        const headers = Object.fromEntries(
          Object.entries(firstCaptured.options.headers).map(([key, value]) => [key.toLowerCase(), value]),
        );
        console.log(JSON.stringify({
          calls,
          unicodeBoundaryCalls,
          url: firstCaptured.url,
          method: firstCaptured.options.method,
          credentials: firstCaptured.options.credentials,
          headers,
          body: JSON.parse(firstCaptured.options.body),
          bodyKeys: Object.keys(JSON.parse(firstCaptured.options.body)).sort(),
          optionKeys: Object.keys(firstCaptured.options).sort(),
          analysis,
          copied: formatCallsAnalysisText(analysis),
          oversized,
        }));
        """
    )
    assert result["calls"] == 1
    assert result["unicodeBoundaryCalls"] == 1
    assert result["url"] == "/api/marketmatch/calls/analyze"
    assert "Complete transcript" not in result["url"]
    assert result["method"] == "POST"
    assert result["credentials"] == "same-origin"
    assert result["headers"] == {"content-type": "application/json"}
    assert result["body"] == {"transcript": "Complete transcript only"}
    assert result["bodyKeys"] == ["transcript"]
    assert not ({"authorization", "x-api-key", "x-odysseus-internal-token", "x-odysseus-owner"} & result["headers"].keys())
    assert result["oversized"]["code"] == "ANALYSIS_TOO_LONG"
    assert "Summary\n<b>Summary stays text</b>" in result["copied"]
    assert "Decisions\nNone identified." in result["copied"]
    assert "Owner: Not stated." in result["copied"]
    assert "Due date: Not stated." in result["copied"]
    assert "Open Questions\nNone identified." in result["copied"]


def test_analysis_safe_errors_and_malformed_responses_never_expose_backend_details():
    result = _run_node(
        """
        import { requestCallsAnalysis } from 'CALLS_MODULE';
        const responses = [
          { ok: false, status: 502, json: async () => ({ detail: 'PRIVATE MODEL OUTPUT' }) },
          { ok: true, status: 200, json: async () => { throw new SyntaxError('PRIVATE HTML'); } },
          { ok: true, status: 200, json: async () => ({ summary: 'x', decisions: 'bad', action_items: [], open_questions: [] }) },
          { ok: true, status: 200, json: async () => ({ summary: 'x', decisions: [], action_items: [{ task: 'x' }], open_questions: [] }) },
        ];
        const errors = [];
        for (const response of responses) {
          try {
            await requestCallsAnalysis('Keep this transcript', { fetchImpl: async () => response });
          } catch (error) {
            errors.push({ code: error.code, message: error.message, status: error.status });
          }
        }
        console.log(JSON.stringify(errors));
        """
    )
    assert result[0] == {
        "code": "ANALYSIS_HTTP_502",
        "message": "The local analysis result could not be validated.",
        "status": 502,
    }
    assert all(item["message"] == "The local analysis result could not be validated." for item in result)
    assert "PRIVATE" not in json.dumps(result)


def test_analysis_controller_requires_success_prevents_duplicates_copies_regenerates_and_does_not_save_analysis():
    result = _run_node(
        """
        import { createCallsController } from 'CALLS_MODULE';
        const events = [];
        const requests = [];
        const copied = [];
        let resolveAnalysis;
        let analysisNumber = 0;
        let analysisShouldFail = false;
        let documentBody = null;
        const analysisPayload = () => ({
          summary: `ANALYSIS CANARY ${analysisNumber}`,
          decisions: analysisNumber === 1 ? [] : ['Proceed carefully.'],
          action_items: [{ task: 'Send drawing', owner: null, due_date: null }],
          open_questions: [],
        });
        const fetchImpl = (url, options) => {
          requests.push([url, options]);
          if (url === '/api/marketmatch/stt/transcribe') {
            return Promise.resolve({ ok: true, status: 200, json: async () => ({
              duration_ms: 1000,
              transcript_text: 'Keep this complete transcript',
              segments: [{ start_ms: 0, end_ms: 1000, text: 'Keep this complete transcript' }],
            }) });
          }
          if (url === '/api/marketmatch/calls/analyze') {
            analysisNumber += 1;
            return new Promise((resolve) => { resolveAnalysis = () => resolve(
              analysisShouldFail
                ? { ok: false, status: 503, json: async () => ({ detail: 'PRIVATE' }) }
                : { ok: true, status: 200, json: async () => analysisPayload() },
            ); });
          }
          if (url === '/api/document') {
            documentBody = JSON.parse(options.body);
            return Promise.resolve({ ok: true, status: 200, json: async () => ({ id: 'doc-1' }) });
          }
          throw new Error('unexpected request');
        };
        const view = {
          clearResult() {}, clearSave() {}, clearAnalysis() { events.push(['analysis-clear']); },
          clearRecording() {}, showSelected() {}, setReady() {}, setBusy() {}, setStatus() {},
          renderResult() {}, showSave() {}, setSaveBusy() {}, setSaved() {}, setSaveStatus() {},
          showAnalysisReady(enabled) { events.push(['analysis-ready', enabled]); },
          beginAnalysisAttempt() { events.push(['analysis-attempt']); },
          setAnalysisBusy(value) { events.push(['analysis-busy', value]); },
          setAnalysisStatus(message, kind) { events.push(['analysis-status', kind, message]); },
          renderAnalysis(value) { events.push(['analysis-render', value.summary]); },
          reset() {}, setRecordingState() {},
        };
        const controller = createCallsController({
          view,
          fetchImpl,
          copyText: async (text) => { copied.push(text); },
          now: () => new Date(2026, 6, 18, 14, 5).getTime(),
        });
        const before = await controller.generateAnalysis();
        controller.selectFile({ name: 'call.wav', size: 46 });
        const transcribed = await controller.submit();
        const firstPromise = controller.generateAnalysis();
        const duplicate = await controller.generateAnalysis();
        resolveAnalysis();
        const first = await firstPromise;
        const copiedFirst = await controller.copyAnalysis();
        const regeneratePromise = controller.generateAnalysis();
        resolveAnalysis();
        const regenerated = await regeneratePromise;
        analysisShouldFail = true;
        const failedPromise = controller.generateAnalysis();
        resolveAnalysis();
        const failed = await failedPromise;
        const transcriptAfterFailure = await controller.copyTranscript();
        const saved = await controller.saveToLibrary('Transcript only');
        console.log(JSON.stringify({
          before, transcribed, duplicate, first, copiedFirst, regenerated,
          failed, transcriptAfterFailure, saved,
          analysisRequests: requests.filter(([url]) => url.includes('/calls/analyze')).length,
          copied, events, documentBody,
        }));
        """
    )
    assert result["before"] is False
    assert result["transcribed"] is True
    assert result["duplicate"] is False
    assert result["first"] is True
    assert result["copiedFirst"] is True
    assert result["regenerated"] is True
    assert result["failed"] is False
    assert result["transcriptAfterFailure"] is True
    assert result["analysisRequests"] == 3
    assert ["analysis-ready", True] in result["events"]
    assert ["analysis-render", "ANALYSIS CANARY 1"] in result["events"]
    assert ["analysis-render", "ANALYSIS CANARY 2"] in result["events"]
    assert all(heading in result["copied"][0] for heading in ("Summary", "Decisions", "Action Items", "Open Questions"))
    assert result["copied"][-1] == "Keep this complete transcript"
    assert result["saved"] is True
    assert "Keep this complete transcript" in result["documentBody"]["content"]
    assert "ANALYSIS CANARY" not in result["documentBody"]["content"]
    assert set(result["documentBody"]) == {"title", "language", "content"}


def test_analysis_lifecycle_aborts_and_ignores_stale_results_without_losing_transcript():
    result = _run_node(
        """
        import { createCallsController } from 'CALLS_MODULE';
        const pending = [];
        const signals = [];
        const events = [];
        const copied = [];
        let transcriptNumber = 0;
        const validAnalysis = (label) => ({
          summary: label, decisions: [], action_items: [], open_questions: [],
        });
        const fetchImpl = (url, options) => {
          if (url === '/api/marketmatch/stt/transcribe') {
            transcriptNumber += 1;
            const text = `Transcript ${transcriptNumber}`;
            return Promise.resolve({ ok: true, status: 200, json: async () => ({
              duration_ms: 1000, transcript_text: text,
              segments: [{ start_ms: 0, end_ms: 1000, text }],
            }) });
          }
          if (url === '/api/marketmatch/calls/analyze') {
            signals.push(options.signal);
            return new Promise((resolve) => pending.push((label) => resolve({
              ok: true, status: 200, json: async () => validAnalysis(label),
            })));
          }
          throw new Error('unexpected request');
        };
        const view = {
          clearResult() {}, clearSave() {}, clearRecording() {}, showSelected() {}, setReady() {},
          setBusy() {}, setStatus() {}, renderResult() {}, showSave() {}, setSaveBusy() {},
          clearAnalysis() { events.push('clear-analysis'); },
          showAnalysisReady(enabled) { events.push(`ready-${enabled}`); },
          beginAnalysisAttempt() {}, setAnalysisBusy() {}, setAnalysisStatus() {},
          renderAnalysis(value) { events.push(`render-${value.summary}`); },
          reset() {}, setRecordingState() {}, setRecordingStatus() {},
        };
        const controller = createCallsController({
          view, fetchImpl, copyText: async (text) => copied.push(text),
          mediaDevices: { getUserMedia: async () => { throw { name: 'NotAllowedError' }; } },
          MediaRecorderClass: function FakeRecorder() {}, BlobClass: Blob,
        });
        controller.selectFile({ name: 'one.wav', size: 46 });
        await controller.submit();

        const staleAfterAudio = controller.generateAnalysis();
        controller.selectFile({ name: 'two.wav', size: 46 });
        pending.shift()('old-audio');
        const audioIgnored = await staleAfterAudio;

        await controller.submit();
        const staleAfterTranscription = controller.generateAnalysis();
        const retranscribed = await controller.submit();
        pending.shift()('old-transcription');
        const transcriptionIgnored = await staleAfterTranscription;

        const staleAfterClear = controller.generateAnalysis();
        controller.reset();
        pending.shift()('old-clear');
        const clearIgnored = await staleAfterClear;

        controller.selectFile({ name: 'three.wav', size: 46 });
        await controller.submit();
        const staleAfterClose = controller.generateAnalysis();
        controller.onPanelHidden();
        pending.shift()('old-close');
        const closeIgnored = await staleAfterClose;

        const completed = controller.generateAnalysis();
        pending.shift()('current');
        const completedResult = await completed;
        const transcriptPreserved = await controller.copyTranscript();
        const recordingStarted = await controller.startRecording();

        console.log(JSON.stringify({
          audioIgnored, retranscribed, transcriptionIgnored, clearIgnored, closeIgnored,
          completedResult, recordingStarted, transcriptPreserved, copied, events,
          aborted: signals.map((signal) => signal.aborted),
          staleRendered: events.filter((event) => String(event).startsWith('render-old')),
        }));
        """
    )
    assert result["audioIgnored"] is False
    assert result["retranscribed"] is True
    assert result["transcriptionIgnored"] is False
    assert result["clearIgnored"] is False
    assert result["closeIgnored"] is False
    assert result["completedResult"] is True
    assert result["recordingStarted"] is False
    assert result["transcriptPreserved"] is True
    assert result["copied"][-1].startswith("Transcript ")
    assert result["staleRendered"] == []
    assert result["aborted"][:4] == [True, True, True, True]
    assert "render-current" in result["events"]


def test_capture_visible_rename_session_details_and_clear_reset():
    assert '<span class="grow">Capture</span>' in INDEX
    assert 'aria-label="Open Capture"' in INDEX
    assert 'aria-label="Close Capture"' in INDEX
    assert '>Calls<' not in INDEX
    assert (
        'Record meetings, walkthroughs, voice notes and field observations. '
        'Add photographs and videos, transcribe speech locally and preserve selected records in Library.'
    ) in INDEX
    for marker in (
        'id="calls-capture-details-heading">Capture details',
        'id="calls-save-title"',
        'id="calls-capture-type"',
        'id="calls-capture-notes"',
        'id="calls-capture-created"',
        'id="calls-capture-status"',
        'id="calls-history-heading">Saved Captures',
        'id="calls-timeline-heading">Capture timeline',
        'id="calls-save-heading">Save Capture to Library',
    ):
        assert marker in INDEX
    for capture_type in (
        "Meeting", "Field Observation", "Walkthrough", "Training", "Voice Note",
        "Supplier Conversation", "Other",
    ):
        assert f"<option>{capture_type}</option>" in INDEX

    result = _run_node(
        """
        import { createCallsController, defaultCallsDocumentTitle } from 'CALLS_MODULE';
        const rendered = [];
        const statuses = [];
        const fixed = new Date(2026, 6, 19, 9, 7).getTime();
        const view = {
          renderCaptureDetails(value) { rendered.push({ title: value.title, type: value.type, notes: value.notes }); },
          setSaveStatus(message, kind) { statuses.push([kind, message]); },
          setCaptureSaveReady() {}, setSaved() {}, renderTimeline() {}, renderCaptureMedia() {},
          setMediaStatus() {}, setCaptureStatus() {}, clearResult() {}, clearSave() {}, clearAnalysis() {},
          setAnalysisBusy() {}, setSaveBusy() {}, setRecordingState() {}, clearRecording() {}, reset() {},
        };
        const controller = createCallsController({ view, now: () => fixed });
        const edited = controller.updateCaptureDetails({
          title: '  Site walk  ', type: 'Walkthrough', notes: '<b>Safe note</b>',
        });
        const emptySave = await controller.saveToLibrary('   ');
        controller.reset();
        console.log(JSON.stringify({
          defaultTitle: defaultCallsDocumentTitle(new Date(fixed)), edited, emptySave,
          reset: rendered.at(-1), statuses,
        }));
        """
    )
    assert result["defaultTitle"] == "Capture — 2026-07-19 09:07"
    assert result["edited"]["type"] == "Walkthrough"
    assert result["edited"]["notes"] == "<b>Safe note</b>"
    assert result["emptySave"] is False
    assert result["reset"] == {
        "title": "Capture — 2026-07-19 09:07", "type": "Meeting", "notes": "",
    }
    assert ["error", "Enter a Capture title before saving."] in result["statuses"]


def test_capture_photo_video_validation_and_transient_fingerprint():
    result = _run_node(
        """
        import {
          MAX_CAPTURE_PHOTO_BYTES, MAX_CAPTURE_VIDEO_BYTES,
          captureMediaFingerprint, validateCaptureMediaFile,
        } from 'CALLS_MODULE';
        const file = (name, size, type, lastModified = 7) => ({ name, size, type, lastModified });
        const photo = file('site.jpg', 100, 'image/jpeg');
        console.log(JSON.stringify({
          photo: validateCaptureMediaFile(photo, 'photo'),
          png: validateCaptureMediaFile(file('site.png', MAX_CAPTURE_PHOTO_BYTES, 'image/png'), 'photo'),
          webp: validateCaptureMediaFile(file('site.webp', 1, 'image/webp'), 'photo'),
          badPhoto: validateCaptureMediaFile(file('site.gif', 1, 'image/gif'), 'photo'),
          largePhoto: validateCaptureMediaFile(file('site.jpg', MAX_CAPTURE_PHOTO_BYTES + 1, 'image/jpeg'), 'photo'),
          mp4: validateCaptureMediaFile(file('walk.mp4', 1, 'video/mp4'), 'video'),
          webm: validateCaptureMediaFile(file('walk.webm', 1, 'video/webm'), 'video'),
          mov: validateCaptureMediaFile(file('walk.mov', MAX_CAPTURE_VIDEO_BYTES, 'video/quicktime'), 'video'),
          badVideo: validateCaptureMediaFile(file('walk.avi', 1, 'video/x-msvideo'), 'video'),
          largeVideo: validateCaptureMediaFile(file('walk.mp4', MAX_CAPTURE_VIDEO_BYTES + 1, 'video/mp4'), 'video'),
          empty: validateCaptureMediaFile(file('empty.jpg', 0, 'image/jpeg'), 'photo'),
          fingerprint: captureMediaFingerprint(photo),
          sameFingerprint: captureMediaFingerprint({ ...photo }) === captureMediaFingerprint(photo),
        }));
        """
    )
    assert result["photo"] == result["png"] == result["webp"] == {"ok": True}
    assert result["mp4"] == result["webm"] == result["mov"] == {"ok": True}
    assert result["badPhoto"]["code"] == "MEDIA_TYPE_UNSUPPORTED"
    assert result["largePhoto"]["code"] == "MEDIA_TOO_LARGE"
    assert result["badVideo"]["code"] == "MEDIA_TYPE_UNSUPPORTED"
    assert result["largeVideo"]["code"] == "MEDIA_TOO_LARGE"
    assert result["empty"]["code"] == "MEDIA_EMPTY"
    assert result["sameFingerprint"] is True
    assert "site.jpg" in result["fingerprint"]


def test_capture_media_lifecycle_timeline_and_in_memory_only_save():
    result = _run_node(
        """
        import { createCallsController } from 'CALLS_MODULE';
        const revoked = [];
        const timelines = [];
        const mediaRenders = [];
        const statuses = [];
        const requests = [];
        const view = {
          renderCaptureMedia(kind, items) {
            mediaRenders.push([kind, items.map(({ name, caption, url }) => ({ name, caption, url }))]);
          },
          renderTimeline(items) { timelines.push(items.map((item) => item.message)); },
          setMediaStatus(kind, message, state) { statuses.push([kind, state, message]); },
          setCaptureSaveReady() {}, setSaved() {}, setSaveStatus() {}, setSaveBusy() {},
          renderCaptureDetails() {}, setCaptureStatus() {}, setHistoryLoading() {},
          clearResult() {}, clearSave() {}, clearAnalysis() {}, setAnalysisBusy() {},
          setRecordingState() {}, clearRecording() {}, reset() {},
        };
        const fetchImpl = async (url, options) => {
          requests.push({ url, method: options.method, body: JSON.parse(options.body) });
          return { ok: true, status: 200, json: async () => ({ id: 'capture-doc' }) };
        };
        const controller = createCallsController({
          view, fetchImpl, now: () => new Date(2026, 6, 19, 10, 0).getTime(),
          createObjectURL: (file) => `blob:preview-${file.name}`,
          revokeObjectURL: (url) => revoked.push(url),
        });
        controller.updateCaptureDetails({
          title: 'Capture Test', type: 'Field Observation', notes: '<b>Review facade</b>',
        });
        const photo = { name: 'facade.jpg', size: 123, type: 'image/jpeg', lastModified: 1 };
        const video = { name: 'walk.mp4', size: 456, type: 'video/mp4', lastModified: 2 };
        const photoAdded = controller.addCaptureMedia('photo', photo);
        const duplicate = controller.addCaptureMedia('photo', { ...photo });
        const videoAdded = controller.addCaptureMedia('video', video);
        controller.updateMediaCaption('photo', 'capture-media-1', '<img src=x> Front elevation');
        controller.updateMediaCaption('video', 'capture-media-2', 'Walkthrough clip');
        const saved = await controller.saveToLibrary('  Capture Test  ');
        const photoRemoved = controller.removeCaptureMedia('photo', 'capture-media-1');
        controller.onPanelHidden();
        console.log(JSON.stringify({
          photoAdded, duplicate, videoAdded, saved, photoRemoved, revoked, timelines,
          mediaRenders, statuses, requests,
        }));
        """
    )
    assert result["photoAdded"] is True
    assert result["duplicate"] is False
    assert result["videoAdded"] is True
    assert result["saved"] is True
    assert result["photoRemoved"] is True
    assert sorted(result["revoked"]) == ["blob:preview-facade.jpg", "blob:preview-walk.mp4"]
    assert len(result["requests"]) == 1
    request = result["requests"][0]
    assert request["url"] == "/api/document"
    assert request["method"] == "POST"
    assert request["body"]["title"] == "Capture Test"
    content = request["body"]["content"]
    assert "# MarketMatch Capture" in content
    assert "Capture title: Capture Test" in content
    assert "Capture type: Field Observation" in content
    assert "&lt;b&gt;Review facade&lt;/b&gt;" in content
    assert "facade.jpg" in content and "Front elevation" in content
    assert "walk.mp4" in content and "Walkthrough clip" in content
    assert "Visual media was not persisted with this Capture." in content
    assert "blob:preview" not in content
    assert "data:" not in content
    assert "analysis" not in content.lower()
    latest_timeline = result["timelines"][-1]
    assert latest_timeline.count("Photo added.") == 1
    assert latest_timeline.count("Video added.") == 1
    assert "Capture saved to Library." in latest_timeline
    assert "Attachment removed." in latest_timeline
    assert any(state == "error" and "already" in message for _, state, message in result["statuses"])


def test_capture_history_accepts_new_and_legacy_markers_without_title_filtering():
    result = _run_node(
        """
        import { buildCaptureDocumentContent, filterCallsHistoryDocuments } from 'CALLS_MODULE';
        const capturePreview = buildCaptureDocumentContent({
          notes: 'New capture', createdAt: new Date(0), captureType: 'Meeting',
        });
        const legacyPreview = [
          '# MarketMatch Calls Transcript', '',
          '**AI-generated transcript. Review before relying on it for operational decisions.**', '',
          'Source: MarketMatch Calls', '', '## Transcript', '', 'Legacy',
        ].join('\\n');
        const documents = [
          { id: 'new', title: 'Window Installation Review', preview: capturePreview },
          { id: 'old', title: 'Site Coordination July 18', preview: legacyPreview },
          { id: 'ordinary', title: 'Call notes', preview: '# MarketMatch Capture notes only' },
          { id: 'new', title: 'Duplicate', preview: capturePreview },
        ];
        console.log(JSON.stringify(filterCallsHistoryDocuments({ documents })));
        """
    )
    assert [item["id"] for item in result] == ["new", "old"]
    assert [item["title"] for item in result] == [
        "Window Installation Review", "Site Coordination July 18",
    ]


def test_capture_media_dom_privacy_timeline_hooks_and_analysis_boundary():
    for marker in (
        'id="calls-photo-input"',
        'accept="image/jpeg,image/png,image/webp"',
        'id="calls-video-input"',
        'accept="video/mp4,video/webm,video/quicktime"',
        'capture="environment"',
        'Photos and videos in this pilot remain in memory and are not saved.',
        'Current AI analysis uses the transcript only. Photo and video interpretation is not included yet.',
    ):
        assert marker in INDEX
    for source_marker in (
        "preview.controls = true",
        "preview.preload = 'metadata'",
        "setElementText(name, media.name)",
        "setElementText(message, event.message)",
        "addTimelineEvent('Recording started.')",
        "addTimelineEvent('Recording stopped.')",
        "addTimelineEvent('Transcription completed.')",
        "addTimelineEvent('Transcript analysis generated.')",
        "addTimelineEvent('Capture saved to Library.')",
    ):
        assert source_marker in SOURCE
    assert "preview.autoplay" not in SOURCE
    assert "/api/upload" not in SOURCE
    assert "FormData" not in SOURCE
    assert "localStorage" not in SOURCE
    assert "sessionStorage" not in SOURCE
    assert "indexedDB" not in SOURCE
    assert "caches.open" not in SOURCE
    assert "FileReader" not in SOURCE
    assert "readAsDataURL" not in SOURCE
    assert "console.log" not in SOURCE
    assert "console.error" not in SOURCE
    assert "JSON.stringify({ transcript })" in SOURCE
