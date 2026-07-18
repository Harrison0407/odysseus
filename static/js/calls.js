const ENDPOINT = '/api/marketmatch/stt/transcribe';
export const MAX_CALLS_WAV_BYTES = 20 * 1024 * 1024;

const STATUS_MESSAGES = Object.freeze({
  401: 'Your browser session has expired. Sign in again, then retry.',
  403: 'Your account is not allowed to use the Calls transcription pilot.',
  408: 'The audio upload timed out. Choose the file and try again.',
  413: 'The WAV exceeds the pilot size or duration limit.',
  415: 'This file is not the required canonical mono 16 kHz PCM WAV format.',
  422: 'The file is not a valid canonical mono 16 kHz PCM WAV.',
  429: 'Another transcription is already running. Try again when it finishes.',
  502: 'The local transcription result could not be validated.',
  503: 'The local transcription model is unavailable right now.',
  504: 'Local transcription timed out. Try a shorter file.',
});

const GENERIC_FAILURE = 'Transcription could not be completed. Please try again.';

export class CallsUiError extends Error {
  constructor(code, message, status = 0) {
    super(message);
    this.name = 'CallsUiError';
    this.code = code;
    this.status = status;
  }
}

export function validateCallsFile(file) {
  if (!file || typeof file.name !== 'string' || !/\.wav$/i.test(file.name)) {
    return { ok: false, code: 'WAV_REQUIRED', message: 'Choose a .wav file.' };
  }
  if (!Number.isSafeInteger(file.size) || file.size <= 0) {
    return { ok: false, code: 'EMPTY_FILE', message: 'The selected WAV file is empty.' };
  }
  if (file.size > MAX_CALLS_WAV_BYTES) {
    return { ok: false, code: 'FILE_TOO_LARGE', message: 'Choose a WAV file no larger than 20 MiB.' };
  }
  return { ok: true };
}

export function formatEncodedSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

export function formatCallsTimestamp(milliseconds) {
  const safe = Number.isFinite(milliseconds) && milliseconds >= 0 ? Math.round(milliseconds) : 0;
  const hours = Math.floor(safe / 3_600_000);
  const minutes = Math.floor((safe % 3_600_000) / 60_000);
  const seconds = Math.floor((safe % 60_000) / 1_000);
  const millis = safe % 1_000;
  const core = `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}.${String(millis).padStart(3, '0')}`;
  return hours ? `${String(hours).padStart(2, '0')}:${core}` : core;
}

export function fixedCallsError(status) {
  return STATUS_MESSAGES[status] || GENERIC_FAILURE;
}

function _validateSuccessPayload(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
      || !Number.isSafeInteger(value.duration_ms) || value.duration_ms <= 0
      || typeof value.transcript_text !== 'string' || !Array.isArray(value.segments)) {
    throw new CallsUiError('MALFORMED_RESPONSE', GENERIC_FAILURE);
  }
  const segments = value.segments.map((segment) => {
    if (!segment || typeof segment !== 'object' || Array.isArray(segment)
        || !Number.isSafeInteger(segment.start_ms) || segment.start_ms < 0
        || !Number.isSafeInteger(segment.end_ms) || segment.end_ms < segment.start_ms
        || typeof segment.text !== 'string') {
      throw new CallsUiError('MALFORMED_RESPONSE', GENERIC_FAILURE);
    }
    return { start_ms: segment.start_ms, end_ms: segment.end_ms, text: segment.text };
  });
  if (segments.map((segment) => segment.text).join('') !== value.transcript_text) {
    throw new CallsUiError('MALFORMED_RESPONSE', GENERIC_FAILURE);
  }
  return { duration_ms: value.duration_ms, transcript_text: value.transcript_text, segments };
}

export async function requestCallsTranscription(file, { fetchImpl = globalThis.fetch, signal } = {}) {
  const validation = validateCallsFile(file);
  if (!validation.ok) throw new CallsUiError(validation.code, validation.message);
  let response;
  try {
    response = await fetchImpl(ENDPOINT, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'audio/wav' },
      body: file,
      signal,
    });
  } catch (error) {
    if (error && error.name === 'AbortError') {
      throw new CallsUiError('CANCELLED', 'Transcription cancelled.');
    }
    throw new CallsUiError('REQUEST_FAILED', GENERIC_FAILURE);
  }
  if (!response || response.ok !== true) {
    const status = response && Number.isInteger(response.status) ? response.status : 0;
    throw new CallsUiError(`HTTP_${status || 'ERROR'}`, fixedCallsError(status), status);
  }
  let payload;
  try {
    payload = await response.json();
  } catch (_) {
    throw new CallsUiError('MALFORMED_RESPONSE', GENERIC_FAILURE);
  }
  return _validateSuccessPayload(payload);
}

export function setElementText(element, value) {
  if (element) element.textContent = String(value ?? '');
}

export function createCallsController({
  view,
  fetchImpl = globalThis.fetch,
  createAbortController = () => new AbortController(),
  copyText,
} = {}) {
  let selectedFile = null;
  let result = null;
  let active = false;
  let controller = null;
  let generation = 0;

  function selectFile(file) {
    if (active) return false;
    const validation = validateCallsFile(file);
    result = null;
    view.clearResult();
    if (!validation.ok) {
      selectedFile = null;
      view.showSelected(null);
      view.setStatus(validation.message, 'error');
      view.setReady(false);
      return false;
    }
    selectedFile = file;
    view.showSelected(file.name, formatEncodedSize(file.size));
    view.setStatus('Ready to transcribe. The file will not be saved.', 'selected');
    view.setReady(true);
    return true;
  }

  async function submit() {
    if (active) return false;
    const validation = validateCallsFile(selectedFile);
    if (!validation.ok) {
      view.setStatus(validation.message, 'error');
      return false;
    }
    active = true;
    const run = ++generation;
    controller = createAbortController();
    view.setBusy(true);
    view.setStatus('Transcribing locally…', 'loading');
    try {
      const next = await requestCallsTranscription(selectedFile, {
        fetchImpl,
        signal: controller.signal,
      });
      if (run !== generation) return false;
      result = next;
      view.renderResult(next);
      view.setStatus(
        next.segments.length ? 'Transcription complete.' : 'Transcription complete. No speech was detected.',
        'success',
      );
      return true;
    } catch (error) {
      if (run !== generation) return false;
      const safe = error instanceof CallsUiError ? error : new CallsUiError('REQUEST_FAILED', GENERIC_FAILURE);
      view.setStatus(safe.message, safe.code === 'CANCELLED' ? 'cancelled' : 'error');
      return false;
    } finally {
      if (run === generation) {
        active = false;
        controller = null;
        view.setBusy(false);
      }
    }
  }

  function cancel() {
    if (!active || !controller) return false;
    controller.abort();
    return true;
  }

  function reset() {
    generation += 1;
    if (controller) controller.abort();
    controller = null;
    active = false;
    selectedFile = null;
    result = null;
    view.reset();
    return true;
  }

  async function copyTranscript() {
    if (!result || !result.transcript_text || typeof copyText !== 'function') return false;
    try {
      await copyText(result.transcript_text);
      view.setStatus('Transcript copied.', 'success');
      return true;
    } catch (_) {
      view.setStatus('Transcript could not be copied.', 'error');
      return false;
    }
  }

  return { selectFile, submit, cancel, reset, copyTranscript, isActive: () => active };
}

function _domView(doc) {
  const byId = (id) => doc.getElementById(id);
  const modal = byId('calls-modal');
  const fileInput = byId('calls-file-input');
  const submit = byId('calls-submit-btn');
  const cancel = byId('calls-cancel-btn');
  const reset = byId('calls-reset-btn');
  const selected = byId('calls-file-details');
  const status = byId('calls-status');
  const resultSection = byId('calls-result');
  const transcript = byId('calls-transcript');
  const summary = byId('calls-result-summary');
  const empty = byId('calls-empty-result');
  const segmentsWrap = byId('calls-segments-wrap');
  const segmentsList = byId('calls-segments');
  const copy = byId('calls-copy-btn');

  const clearResult = () => {
    resultSection.hidden = true;
    setElementText(transcript, '');
    setElementText(summary, '');
    segmentsList.replaceChildren();
    segmentsWrap.hidden = true;
    empty.hidden = true;
  };

  return {
    clearResult,
    showSelected(name, size) {
      selected.hidden = !name;
      setElementText(selected, name ? `${name} · ${size} encoded` : '');
      reset.disabled = !name;
    },
    setReady(ready) { submit.disabled = !ready; },
    setBusy(busy) {
      modal.querySelector('.calls-modal-content').setAttribute('aria-busy', String(busy));
      fileInput.disabled = busy;
      submit.disabled = busy || !selected.textContent;
      submit.textContent = busy ? 'Transcribing…' : 'Transcribe';
      cancel.hidden = !busy;
      reset.disabled = busy || !selected.textContent;
    },
    setStatus(message, kind) {
      setElementText(status, message);
      status.dataset.state = kind;
      status.setAttribute('role', kind === 'error' ? 'alert' : 'status');
      status.setAttribute('aria-live', kind === 'error' ? 'assertive' : 'polite');
    },
    renderResult(value) {
      resultSection.hidden = false;
      setElementText(summary, `${formatCallsTimestamp(value.duration_ms)} duration · ${value.segments.length} ${value.segments.length === 1 ? 'segment' : 'segments'}`);
      setElementText(transcript, value.transcript_text);
      empty.hidden = value.transcript_text.length !== 0 || value.segments.length !== 0;
      transcript.hidden = value.transcript_text.length === 0;
      copy.disabled = value.transcript_text.length === 0;
      segmentsList.replaceChildren();
      for (const segment of value.segments) {
        const item = doc.createElement('li');
        const time = doc.createElement('span');
        const text = doc.createElement('span');
        time.className = 'calls-segment-time';
        text.className = 'calls-segment-text';
        setElementText(time, `${formatCallsTimestamp(segment.start_ms)} – ${formatCallsTimestamp(segment.end_ms)}`);
        setElementText(text, segment.text);
        item.append(time, text);
        segmentsList.appendChild(item);
      }
      segmentsWrap.hidden = value.segments.length === 0;
    },
    reset() {
      fileInput.value = '';
      selected.hidden = true;
      setElementText(selected, '');
      submit.disabled = true;
      submit.textContent = 'Transcribe';
      cancel.hidden = true;
      reset.disabled = true;
      fileInput.disabled = false;
      clearResult();
      this.setStatus('Select a canonical WAV file to begin.', 'idle');
    },
  };
}

async function _copyText(text) {
  if (!globalThis.navigator || !navigator.clipboard || typeof navigator.clipboard.writeText !== 'function') {
    throw new Error('clipboard unavailable');
  }
  await navigator.clipboard.writeText(text);
}

export function init(doc = globalThis.document) {
  if (!doc) return null;
  const modal = doc.getElementById('calls-modal');
  const openButton = doc.getElementById('tool-calls-btn');
  if (!modal || !openButton || modal.dataset.callsWired === 'true') return null;
  modal.dataset.callsWired = 'true';
  const view = _domView(doc);
  const controller = createCallsController({ view, copyText: _copyText });
  const fileInput = doc.getElementById('calls-file-input');
  const show = () => {
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
    fileInput.focus();
  };
  const close = () => {
    controller.cancel();
    modal.classList.add('hidden');
    modal.setAttribute('aria-hidden', 'true');
    openButton.focus();
  };
  openButton.addEventListener('click', show);
  openButton.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      show();
    }
  });
  doc.getElementById('calls-close-btn').addEventListener('click', close);
  fileInput.addEventListener('change', () => controller.selectFile(fileInput.files && fileInput.files[0]));
  doc.getElementById('calls-submit-btn').addEventListener('click', () => controller.submit());
  doc.getElementById('calls-cancel-btn').addEventListener('click', () => controller.cancel());
  doc.getElementById('calls-reset-btn').addEventListener('click', () => {
    controller.reset();
    fileInput.focus();
  });
  doc.getElementById('calls-copy-btn').addEventListener('click', () => controller.copyTranscript());
  modal.addEventListener('click', (event) => { if (event.target === modal) close(); });
  doc.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) close();
  });
  return controller;
}

export default { init };
