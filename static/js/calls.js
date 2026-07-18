const ENDPOINT = '/api/marketmatch/stt/transcribe';
const DOCUMENT_ENDPOINT = '/api/document';
export const MAX_CALLS_WAV_BYTES = 20 * 1024 * 1024;
export const CALLS_WAV_SAMPLE_RATE = 16000;
const WAV_HEADER_BYTES = 44;
const MAX_RECORDING_MILLISECONDS = Math.floor(
  ((MAX_CALLS_WAV_BYTES - WAV_HEADER_BYTES) / 2 / CALLS_WAV_SAMPLE_RATE) * 1000,
);

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
const RECORDING_FAILURE = 'The recording could not be prepared safely. Please try again.';
const DOCUMENT_SAVE_FAILURE = 'The transcript could not be saved. Please try again.';

const DOCUMENT_STATUS_MESSAGES = Object.freeze({
  400: 'Check the document title and try again.',
  401: 'Your browser session has expired. Sign in again, then retry.',
  403: 'Your account is not allowed to save documents.',
  413: 'The transcript is too large to save as a document.',
  422: 'Check the document title and try again.',
});

export class CallsUiError extends Error {
  constructor(code, message, status = 0) {
    super(message);
    this.name = 'CallsUiError';
    this.code = code;
    this.status = status;
  }
}

export function downmixToMono(channels) {
  if (!Array.isArray(channels) || channels.length === 0) return new Float32Array(0);
  const valid = channels.filter((channel) => channel && Number.isSafeInteger(channel.length));
  if (valid.length !== channels.length || valid.length === 0) return new Float32Array(0);
  const length = Math.min(...valid.map((channel) => channel.length));
  const mono = new Float32Array(length);
  for (let frame = 0; frame < length; frame += 1) {
    let sum = 0;
    for (const channel of valid) sum += Number.isFinite(channel[frame]) ? channel[frame] : 0;
    mono[frame] = sum / valid.length;
  }
  return mono;
}

export function resamplePcm(samples, sourceRate, targetRate = CALLS_WAV_SAMPLE_RATE) {
  if (!samples || !Number.isSafeInteger(samples.length) || samples.length === 0) {
    return new Float32Array(0);
  }
  if (!Number.isFinite(sourceRate) || sourceRate <= 0
      || !Number.isFinite(targetRate) || targetRate <= 0) {
    throw new CallsUiError('RECORDING_FAILED', RECORDING_FAILURE);
  }
  const outputLength = Math.max(1, Math.round(samples.length * targetRate / sourceRate));
  if (targetRate === CALLS_WAV_SAMPLE_RATE
      && WAV_HEADER_BYTES + (outputLength * 2) > MAX_CALLS_WAV_BYTES) {
    throw new CallsUiError('RECORDING_TOO_LARGE', 'The recording exceeds the 20 MiB pilot limit. Record a shorter call.');
  }
  if (sourceRate === targetRate) return Float32Array.from(samples);
  const output = new Float32Array(outputLength);
  const scale = sourceRate / targetRate;
  if (sourceRate > targetRate) {
    for (let index = 0; index < outputLength; index += 1) {
      const start = index * scale;
      const end = Math.min((index + 1) * scale, samples.length);
      let sum = 0;
      let weight = 0;
      for (let source = Math.floor(start); source < Math.ceil(end); source += 1) {
        const overlap = Math.max(0, Math.min(end, source + 1) - Math.max(start, source));
        if (overlap > 0 && source < samples.length) {
          sum += samples[source] * overlap;
          weight += overlap;
        }
      }
      output[index] = weight > 0 ? sum / weight : 0;
    }
    return output;
  }
  for (let index = 0; index < outputLength; index += 1) {
    const position = Math.min(index * scale, samples.length - 1);
    const lower = Math.floor(position);
    const upper = Math.min(lower + 1, samples.length - 1);
    const weight = position - lower;
    output[index] = samples[lower] + ((samples[upper] - samples[lower]) * weight);
  }
  return output;
}

export function float32ToPcm16(samples) {
  const pcm = new Int16Array(samples ? samples.length : 0);
  for (let index = 0; index < pcm.length; index += 1) {
    const value = Math.max(-1, Math.min(1, Number.isFinite(samples[index]) ? samples[index] : 0));
    pcm[index] = value < 0 ? Math.round(value * 0x8000) : Math.round(value * 0x7fff);
  }
  return pcm;
}

export function encodeCanonicalWav(samples) {
  if (!samples || !Number.isSafeInteger(samples.length) || samples.length === 0) {
    throw new CallsUiError('RECORDING_EMPTY', 'No microphone audio was captured. Please record again.');
  }
  const dataBytes = samples.length * 2;
  if (WAV_HEADER_BYTES + dataBytes > MAX_CALLS_WAV_BYTES) {
    throw new CallsUiError('RECORDING_TOO_LARGE', 'The recording exceeds the 20 MiB pilot limit. Record a shorter call.');
  }
  const output = new Uint8Array(WAV_HEADER_BYTES + dataBytes);
  const view = new DataView(output.buffer);
  const writeAscii = (offset, value) => {
    for (let index = 0; index < value.length; index += 1) {
      view.setUint8(offset + index, value.charCodeAt(index));
    }
  };
  writeAscii(0, 'RIFF');
  view.setUint32(4, 36 + dataBytes, true);
  writeAscii(8, 'WAVE');
  writeAscii(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, CALLS_WAV_SAMPLE_RATE, true);
  view.setUint32(28, CALLS_WAV_SAMPLE_RATE * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(36, 'data');
  view.setUint32(40, dataBytes, true);
  const pcm = float32ToPcm16(samples);
  for (let index = 0; index < pcm.length; index += 1) {
    view.setInt16(WAV_HEADER_BYTES + (index * 2), pcm[index], true);
  }
  validateGeneratedWavBytes(output);
  return output;
}

export function validateGeneratedWavBytes(bytes) {
  const byteLength = bytes && Number.isSafeInteger(bytes.byteLength) ? bytes.byteLength : 0;
  if (byteLength <= WAV_HEADER_BYTES) {
    throw new CallsUiError('RECORDING_EMPTY', 'No microphone audio was captured. Please record again.');
  }
  if (byteLength > MAX_CALLS_WAV_BYTES) {
    throw new CallsUiError('RECORDING_TOO_LARGE', 'The recording exceeds the 20 MiB pilot limit. Record a shorter call.');
  }
  return true;
}

export function recordingFilename(date = new Date()) {
  const value = date instanceof Date && Number.isFinite(date.getTime()) ? date : new Date(0);
  const stamp = [
    value.getUTCFullYear(),
    String(value.getUTCMonth() + 1).padStart(2, '0'),
    String(value.getUTCDate()).padStart(2, '0'),
    '-',
    String(value.getUTCHours()).padStart(2, '0'),
    String(value.getUTCMinutes()).padStart(2, '0'),
    String(value.getUTCSeconds()).padStart(2, '0'),
  ].join('');
  return `marketmatch-recording-${stamp}.wav`;
}

export async function decodeRecordingToCanonicalWav(blob, createAudioContext) {
  if (!blob || blob.size <= 0 || typeof blob.arrayBuffer !== 'function') {
    throw new CallsUiError('RECORDING_EMPTY', 'No microphone audio was captured. Please record again.');
  }
  let context;
  try {
    context = typeof createAudioContext === 'function' ? createAudioContext() : null;
  } catch (_) {
    throw new CallsUiError('AUDIO_UNSUPPORTED', 'Microphone audio processing is unavailable in this browser.');
  }
  if (!context || typeof context.decodeAudioData !== 'function') {
    throw new CallsUiError('AUDIO_UNSUPPORTED', 'Microphone audio processing is unavailable in this browser.');
  }
  try {
    const decoded = await context.decodeAudioData(await blob.arrayBuffer());
    if (!decoded || !Number.isFinite(decoded.sampleRate) || decoded.sampleRate <= 0
        || !Number.isSafeInteger(decoded.numberOfChannels) || decoded.numberOfChannels <= 0
        || !Number.isSafeInteger(decoded.length) || decoded.length <= 0) {
      throw new CallsUiError('RECORDING_EMPTY', 'No microphone audio was captured. Please record again.');
    }
    const channels = [];
    for (let index = 0; index < decoded.numberOfChannels; index += 1) {
      channels.push(decoded.getChannelData(index));
    }
    const mono = downmixToMono(channels);
    const resampled = resamplePcm(mono, decoded.sampleRate);
    const bytes = encodeCanonicalWav(resampled);
    return {
      bytes,
      durationMs: Math.round((resampled.length / CALLS_WAV_SAMPLE_RATE) * 1000),
    };
  } catch (error) {
    if (error instanceof CallsUiError) throw error;
    throw new CallsUiError('RECORDING_FAILED', RECORDING_FAILURE);
  } finally {
    if (typeof context.close === 'function') {
      try { await context.close(); } catch (_) { /* best-effort release */ }
    }
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

export function formatCallsLocalDateTime(date = new Date()) {
  const value = date instanceof Date && Number.isFinite(date.getTime()) ? date : new Date(0);
  return [
    value.getFullYear(),
    '-',
    String(value.getMonth() + 1).padStart(2, '0'),
    '-',
    String(value.getDate()).padStart(2, '0'),
    ' ',
    String(value.getHours()).padStart(2, '0'),
    ':',
    String(value.getMinutes()).padStart(2, '0'),
  ].join('');
}

export function defaultCallsDocumentTitle(date = new Date()) {
  return `Call transcript — ${formatCallsLocalDateTime(date)}`;
}

export function buildCallsDocumentContent(result, createdAt = new Date()) {
  const value = _validateSuccessPayload(result);
  const transcript = value.transcript_text || '(No speech was detected.)';
  const segments = value.segments.length
    ? value.segments.map((segment) => (
      `[${formatCallsTimestamp(segment.start_ms)} – ${formatCallsTimestamp(segment.end_ms)}] ${segment.text}`
    )).join('\n\n')
    : '(No speech segments were detected.)';
  return [
    '# MarketMatch Calls Transcript',
    '',
    '**AI-generated transcript. Review before relying on it for operational decisions.**',
    '',
    'Source: MarketMatch Calls',
    `Created: ${formatCallsLocalDateTime(createdAt)}`,
    `Duration: ${formatCallsTimestamp(value.duration_ms)}`,
    '',
    '## Transcript',
    '',
    transcript,
    '',
    '## Timestamped segments',
    '',
    segments,
  ].join('\n');
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

export async function requestCallsDocumentSave(
  { title, result, createdAt },
  { fetchImpl = globalThis.fetch, signal } = {},
) {
  const normalizedTitle = typeof title === 'string' ? title.trim() : '';
  if (!normalizedTitle) {
    throw new CallsUiError('DOCUMENT_TITLE_REQUIRED', 'Enter a document title before saving.');
  }
  const content = buildCallsDocumentContent(result, createdAt);
  let response;
  try {
    response = await fetchImpl(DOCUMENT_ENDPOINT, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: normalizedTitle,
        language: 'markdown',
        content,
      }),
      signal,
    });
  } catch (error) {
    if (error && error.name === 'AbortError') {
      throw new CallsUiError('SAVE_CANCELLED', 'Save cancelled.');
    }
    throw new CallsUiError('DOCUMENT_SAVE_FAILED', DOCUMENT_SAVE_FAILURE);
  }
  if (!response || response.ok !== true) {
    const status = response && Number.isInteger(response.status) ? response.status : 0;
    throw new CallsUiError(
      `DOCUMENT_HTTP_${status || 'ERROR'}`,
      DOCUMENT_STATUS_MESSAGES[status] || DOCUMENT_SAVE_FAILURE,
      status,
    );
  }
  let payload;
  try {
    payload = await response.json();
  } catch (_) {
    throw new CallsUiError('DOCUMENT_MALFORMED_RESPONSE', DOCUMENT_SAVE_FAILURE);
  }
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)
      || typeof payload.id !== 'string' || !payload.id.trim()) {
    throw new CallsUiError('DOCUMENT_MALFORMED_RESPONSE', DOCUMENT_SAVE_FAILURE);
  }
  return { id: payload.id };
}

export function setElementText(element, value) {
  if (element) element.textContent = String(value ?? '');
}

export function createCallsController({
  view,
  fetchImpl = globalThis.fetch,
  createAbortController = () => new AbortController(),
  copyText,
  mediaDevices = globalThis.navigator && globalThis.navigator.mediaDevices,
  MediaRecorderClass = globalThis.MediaRecorder,
  BlobClass = globalThis.Blob,
  createAudioContext = () => {
    const AudioContextClass = globalThis.AudioContext || globalThis.webkitAudioContext;
    return AudioContextClass ? new AudioContextClass() : null;
  },
  decodeRecording = decodeRecordingToCanonicalWav,
  createWavFile = (bytes, name) => new File([bytes], name, { type: 'audio/wav' }),
  createObjectURL = (file) => globalThis.URL && globalThis.URL.createObjectURL
    ? globalThis.URL.createObjectURL(file) : null,
  revokeObjectURL = (url) => {
    if (globalThis.URL && typeof globalThis.URL.revokeObjectURL === 'function') {
      globalThis.URL.revokeObjectURL(url);
    }
  },
  setIntervalFn = globalThis.setInterval,
  clearIntervalFn = globalThis.clearInterval,
  now = () => Date.now(),
  isSecureContext = globalThis.isSecureContext !== false,
} = {}) {
  let selectedFile = null;
  let selectedSource = null;
  let result = null;
  let active = false;
  let controller = null;
  let generation = 0;
  let recordingGeneration = 0;
  let recordingState = 'idle';
  let recorder = null;
  let stream = null;
  let chunks = [];
  let timer = null;
  let recordingStartedAt = 0;
  let previewUrl = null;
  let resultCreatedAt = null;
  let saveActive = false;
  let saveController = null;
  let saveGeneration = 0;
  let saved = false;

  const callView = (method, ...args) => {
    if (view && typeof view[method] === 'function') view[method](...args);
  };

  function stopTracks(value = stream) {
    if (!value || typeof value.getTracks !== 'function') return;
    for (const track of value.getTracks()) {
      try { track.stop(); } catch (_) { /* best-effort release */ }
    }
  }

  function clearRecordingTimer() {
    if (timer !== null && typeof clearIntervalFn === 'function') clearIntervalFn(timer);
    timer = null;
  }

  function revokePreview() {
    if (!previewUrl) return;
    try { revokeObjectURL(previewUrl); } catch (_) { /* best-effort release */ }
    previewUrl = null;
  }

  function setRecordingState(next) {
    recordingState = next;
    callView('setRecordingState', next);
  }

  function discardCapture() {
    clearRecordingTimer();
    if (recorder && recorder.state !== 'inactive') {
      recorder.ondataavailable = null;
      recorder.onerror = null;
      try { recorder.stop(); } catch (_) { /* already stopped */ }
    }
    stopTracks();
    recorder = null;
    stream = null;
    chunks = [];
  }

  function discardRecordedSelection() {
    revokePreview();
    if (selectedSource === 'recording') {
      selectedFile = null;
      selectedSource = null;
    }
    callView('clearRecording');
  }

  function recordingError(error) {
    if (error instanceof CallsUiError) return error;
    return new CallsUiError('RECORDING_FAILED', RECORDING_FAILURE);
  }

  function setTranscriptionStatus(message, kind) {
    callView('setStatus', message, kind);
    if (selectedSource === 'recording') callView('setRecordingStatus', message, kind);
  }

  function failCapture(error) {
    recordingGeneration += 1;
    discardCapture();
    discardRecordedSelection();
    setRecordingState('idle');
    callView('setRecordingStatus', recordingError(error).message, 'error');
  }

  function clearSaveState({ clearView = true } = {}) {
    saveGeneration += 1;
    if (saveController) saveController.abort();
    saveController = null;
    saveActive = false;
    saved = false;
    resultCreatedAt = null;
    callView('setSaveBusy', false);
    if (clearView) callView('clearSave');
  }

  function selectFile(file) {
    if (active || saveActive || ['permission', 'recording', 'processing'].includes(recordingState)) return false;
    const validation = validateCallsFile(file);
    result = null;
    clearSaveState();
    view.clearResult();
    discardRecordedSelection();
    setRecordingState('idle');
    if (!validation.ok) {
      selectedFile = null;
      selectedSource = null;
      view.showSelected(null);
      view.setStatus(validation.message, 'error');
      view.setReady(false, 'upload');
      return false;
    }
    selectedFile = file;
    selectedSource = 'upload';
    view.showSelected(file.name, formatEncodedSize(file.size));
    view.setStatus('Ready to transcribe. The file will not be saved.', 'selected');
    view.setReady(true, 'upload');
    return true;
  }

  async function startRecording() {
    if (active || saveActive || ['permission', 'recording', 'processing'].includes(recordingState)) return false;
    if (!isSecureContext) {
      callView('setRecordingStatus', 'Microphone recording requires HTTPS or localhost.', 'error');
      return false;
    }
    if (!mediaDevices || typeof mediaDevices.getUserMedia !== 'function'
        || typeof MediaRecorderClass !== 'function' || typeof BlobClass !== 'function') {
      callView('setRecordingStatus', 'Microphone recording is unavailable in this browser.', 'error');
      return false;
    }

    const run = ++recordingGeneration;
    discardCapture();
    discardRecordedSelection();
    selectedFile = null;
    selectedSource = null;
    result = null;
    clearSaveState();
    callView('clearUpload');
    callView('clearResult');
    callView('setReady', false, 'upload');
    setRecordingState('permission');
    callView('setRecordingStatus', 'Waiting for microphone permission…', 'loading');

    let nextStream;
    try {
      nextStream = await mediaDevices.getUserMedia({ audio: true, video: false });
    } catch (error) {
      if (run !== recordingGeneration) return false;
      setRecordingState('idle');
      const name = error && error.name;
      if (name === 'NotAllowedError' || name === 'SecurityError') {
        callView('setRecordingStatus', 'Microphone permission was denied. Allow access and try again.', 'error');
      } else if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
        callView('setRecordingStatus', 'No microphone is available.', 'error');
      } else {
        callView('setRecordingStatus', 'The microphone could not be started. Check the device and try again.', 'error');
      }
      return false;
    }
    if (run !== recordingGeneration) {
      stopTracks(nextStream);
      return false;
    }
    if (!nextStream || typeof nextStream.getTracks !== 'function'
        || nextStream.getTracks().length === 0) {
      stopTracks(nextStream);
      setRecordingState('idle');
      callView('setRecordingStatus', 'No microphone is available.', 'error');
      return false;
    }

    stream = nextStream;
    try {
      recorder = new MediaRecorderClass(stream);
      chunks = [];
      recorder.ondataavailable = (event) => {
        if (run === recordingGeneration && event && event.data && event.data.size > 0) {
          chunks.push(event.data);
        }
      };
      recorder.onerror = () => failCapture(new CallsUiError('RECORDING_FAILED', RECORDING_FAILURE));
      recorder.start(250);
    } catch (_) {
      failCapture(new CallsUiError('AUDIO_UNSUPPORTED', 'Microphone recording is unavailable in this browser.'));
      return false;
    }

    recordingStartedAt = now();
    callView('setRecordingElapsed', 0);
    setRecordingState('recording');
    callView('setRecordingStatus', 'Recording. Speak clearly, then choose Stop recording.', 'recording');
    if (typeof setIntervalFn === 'function') {
      timer = setIntervalFn(() => {
        const elapsed = Math.max(0, now() - recordingStartedAt);
        callView('setRecordingElapsed', elapsed);
        if (elapsed >= MAX_RECORDING_MILLISECONDS) void stopRecording();
      }, 250);
    }
    return true;
  }

  async function stopRecording() {
    if (recordingState !== 'recording' || !recorder) return false;
    const run = recordingGeneration;
    const localRecorder = recorder;
    const localStream = stream;
    clearRecordingTimer();
    setRecordingState('processing');
    callView('setRecordingStatus', 'Preparing canonical WAV audio…', 'loading');

    const stopped = new Promise((resolve, reject) => {
      localRecorder.onstop = resolve;
      localRecorder.onerror = () => reject(new CallsUiError('RECORDING_FAILED', RECORDING_FAILURE));
      try { localRecorder.stop(); } catch (_) { reject(new CallsUiError('RECORDING_FAILED', RECORDING_FAILURE)); }
    });
    stopTracks(localStream);
    stream = null;

    try {
      await stopped;
      if (run !== recordingGeneration) return false;
      const nativeBlob = new BlobClass(chunks, { type: localRecorder.mimeType || 'application/octet-stream' });
      chunks = [];
      const prepared = await decodeRecording(nativeBlob, createAudioContext);
      if (run !== recordingGeneration) return false;
      validateGeneratedWavBytes(prepared.bytes);
      if (!Number.isSafeInteger(prepared.durationMs) || prepared.durationMs <= 0) {
        throw new CallsUiError('RECORDING_EMPTY', 'No microphone audio was captured. Please record again.');
      }
      const file = createWavFile(prepared.bytes, recordingFilename(new Date(now())));
      const validation = validateCallsFile(file);
      if (!validation.ok) throw new CallsUiError(validation.code, validation.message);
      selectedFile = file;
      selectedSource = 'recording';
      recorder = null;
      revokePreview();
      try { previewUrl = createObjectURL(file); } catch (_) { previewUrl = null; }
      setRecordingState('ready');
      callView('showRecording', file.name, formatEncodedSize(file.size), prepared.durationMs, previewUrl);
      callView('setReady', true, 'recording');
      callView('setRecordingStatus', 'Recording ready. Review it, then transcribe.', 'success');
      return true;
    } catch (error) {
      if (run !== recordingGeneration) return false;
      recorder = null;
      chunks = [];
      selectedFile = null;
      selectedSource = null;
      revokePreview();
      setRecordingState('idle');
      callView('clearRecording');
      callView('setRecordingStatus', recordingError(error).message, 'error');
      return false;
    }
  }

  function cancelRecording({ announce = true } = {}) {
    if (!['permission', 'recording', 'processing'].includes(recordingState)) return false;
    recordingGeneration += 1;
    discardCapture();
    discardRecordedSelection();
    selectedFile = null;
    selectedSource = null;
    result = null;
    clearSaveState();
    callView('clearResult');
    setRecordingState('idle');
    if (announce) callView('setRecordingStatus', 'Recording cancelled and discarded.', 'cancelled');
    return true;
  }

  async function submit() {
    if (active || saveActive || ['permission', 'recording', 'processing'].includes(recordingState)) return false;
    const validation = validateCallsFile(selectedFile);
    if (!validation.ok) {
      setTranscriptionStatus(validation.message, 'error');
      return false;
    }
    active = true;
    clearSaveState();
    const run = ++generation;
    controller = createAbortController();
    view.setBusy(true);
    setTranscriptionStatus('Transcribing locally…', 'loading');
    try {
      const next = await requestCallsTranscription(selectedFile, {
        fetchImpl,
        signal: controller.signal,
      });
      if (run !== generation) return false;
      result = next;
      resultCreatedAt = new Date(now());
      view.renderResult(next);
      callView('showSave', defaultCallsDocumentTitle(resultCreatedAt));
      setTranscriptionStatus(
        next.segments.length ? 'Transcription complete.' : 'Transcription complete. No speech was detected.',
        'success',
      );
      return true;
    } catch (error) {
      if (run !== generation) return false;
      const safe = error instanceof CallsUiError ? error : new CallsUiError('REQUEST_FAILED', GENERIC_FAILURE);
      setTranscriptionStatus(safe.message, safe.code === 'CANCELLED' ? 'cancelled' : 'error');
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

  async function saveToLibrary(title) {
    if (!result || saveActive || saved || active
        || ['permission', 'recording', 'processing'].includes(recordingState)) return false;
    const normalizedTitle = typeof title === 'string' ? title.trim() : '';
    if (!normalizedTitle) {
      callView('setSaveStatus', 'Enter a document title before saving.', 'error');
      return false;
    }
    saveActive = true;
    const run = ++saveGeneration;
    saveController = createAbortController();
    callView('setSaveBusy', true);
    callView('setSaveStatus', 'Saving transcript to Library…', 'loading');
    try {
      await requestCallsDocumentSave(
        { title: normalizedTitle, result, createdAt: resultCreatedAt },
        { fetchImpl, signal: saveController.signal },
      );
      if (run !== saveGeneration) return false;
      saved = true;
      callView('setSaved', true);
      callView('setSaveStatus', 'Saved to Library.', 'success');
      return true;
    } catch (error) {
      if (run !== saveGeneration) return false;
      const safe = error instanceof CallsUiError
        ? error : new CallsUiError('DOCUMENT_SAVE_FAILED', DOCUMENT_SAVE_FAILURE);
      callView('setSaveStatus', safe.message, safe.code === 'SAVE_CANCELLED' ? 'cancelled' : 'error');
      return false;
    } finally {
      if (run === saveGeneration) {
        saveActive = false;
        saveController = null;
        callView('setSaveBusy', false);
      }
    }
  }

  function reset() {
    generation += 1;
    if (controller) controller.abort();
    controller = null;
    active = false;
    clearSaveState();
    recordingGeneration += 1;
    discardCapture();
    discardRecordedSelection();
    selectedFile = null;
    selectedSource = null;
    result = null;
    setRecordingState('idle');
    view.reset();
    return true;
  }

  function onPanelHidden() {
    if (active) cancel();
    if (saveActive) {
      clearSaveState({ clearView: false });
      callView('setSaveStatus', 'Save cancelled when Calls was closed.', 'cancelled');
    }
    if (cancelRecording({ announce: false })) {
      callView('setRecordingStatus', 'Recording stopped and discarded when Calls was hidden.', 'cancelled');
    }
    return true;
  }

  function destroy() {
    generation += 1;
    if (controller) controller.abort();
    controller = null;
    active = false;
    clearSaveState({ clearView: false });
    recordingGeneration += 1;
    discardCapture();
    revokePreview();
    selectedFile = null;
    selectedSource = null;
    result = null;
    resultCreatedAt = null;
    recordingState = 'idle';
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

  return {
    selectFile,
    submit,
    cancel,
    reset,
    copyTranscript,
    saveToLibrary,
    startRecording,
    stopRecording,
    cancelRecording,
    onPanelHidden,
    destroy,
    isActive: () => active,
    isSaveActive: () => saveActive,
    getRecordingState: () => recordingState,
  };
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
  const saveSection = byId('calls-save');
  const saveTitle = byId('calls-save-title');
  const saveButton = byId('calls-save-btn');
  const saveStatus = byId('calls-save-status');
  const recordStart = byId('calls-record-start-btn');
  const recordStop = byId('calls-record-stop-btn');
  const recordCancel = byId('calls-record-cancel-btn');
  const recordSubmit = byId('calls-record-submit-btn');
  const recordClear = byId('calls-record-clear-btn');
  const recordIndicator = byId('calls-recording-indicator');
  const recordElapsed = byId('calls-recording-elapsed');
  const recordDetails = byId('calls-recording-details');
  const recordPreview = byId('calls-recording-preview');
  const recordStatus = byId('calls-recording-status');
  let transcriptionBusy = false;
  let uploadReady = false;
  let recordingReady = false;
  let recordingUiState = 'idle';
  let saveBusy = false;
  let saveReady = false;
  let saveComplete = false;

  const captureBusy = () => ['permission', 'recording', 'processing'].includes(recordingUiState);
  const syncControls = () => {
    const operationBusy = transcriptionBusy || saveBusy || captureBusy();
    fileInput.disabled = operationBusy;
    submit.disabled = operationBusy || !uploadReady;
    reset.disabled = !uploadReady;
    recordStart.disabled = operationBusy;
    recordStop.disabled = recordingUiState !== 'recording';
    recordCancel.hidden = !captureBusy();
    recordSubmit.disabled = operationBusy || !recordingReady;
    recordClear.disabled = !(recordingReady || captureBusy() || (transcriptionBusy && recordingReady));
    saveTitle.disabled = saveBusy || saveComplete;
    saveButton.disabled = operationBusy || !saveReady || saveComplete;
  };

  const clearSave = () => {
    saveBusy = false;
    saveReady = false;
    saveComplete = false;
    saveSection.hidden = true;
    saveTitle.value = '';
    saveButton.textContent = 'Save to Library';
    setElementText(saveStatus, '');
    saveStatus.dataset.state = 'idle';
    syncControls();
  };

  const clearResult = () => {
    resultSection.hidden = true;
    setElementText(transcript, '');
    setElementText(summary, '');
    segmentsList.replaceChildren();
    segmentsWrap.hidden = true;
    empty.hidden = true;
    clearSave();
  };

  return {
    clearResult,
    showSelected(name, size) {
      selected.hidden = !name;
      setElementText(selected, name ? `${name} · ${size} encoded` : '');
      uploadReady = Boolean(name);
      syncControls();
    },
    setReady(ready, source = 'upload') {
      if (source === 'recording') recordingReady = ready;
      else uploadReady = ready;
      syncControls();
    },
    setBusy(busy) {
      transcriptionBusy = busy;
      modal.querySelector('.calls-modal-content').setAttribute('aria-busy', String(busy));
      submit.textContent = busy ? 'Transcribing…' : 'Transcribe';
      recordSubmit.textContent = busy ? 'Transcribing…' : 'Transcribe recorded audio';
      cancel.hidden = !busy;
      syncControls();
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
    showSave(title) {
      saveReady = true;
      saveComplete = false;
      saveSection.hidden = false;
      saveTitle.disabled = false;
      saveTitle.value = title;
      setElementText(saveStatus, 'Review the title, then save explicitly when ready.');
      saveStatus.dataset.state = 'idle';
      syncControls();
    },
    clearSave,
    setSaveBusy(busy) {
      saveBusy = busy;
      saveButton.textContent = busy ? 'Saving…' : 'Save to Library';
      syncControls();
    },
    setSaved(value) {
      saveComplete = value;
      syncControls();
    },
    setSaveStatus(message, kind) {
      setElementText(saveStatus, message);
      saveStatus.dataset.state = kind;
      saveStatus.setAttribute('role', kind === 'error' ? 'alert' : 'status');
      saveStatus.setAttribute('aria-live', kind === 'error' ? 'assertive' : 'polite');
    },
    clearUpload() {
      fileInput.value = '';
      selected.hidden = true;
      setElementText(selected, '');
      uploadReady = false;
      syncControls();
    },
    setRecordingState(state) {
      recordingUiState = state;
      recordIndicator.hidden = state !== 'recording';
      recordIndicator.dataset.state = state;
      syncControls();
    },
    setRecordingElapsed(milliseconds) {
      const seconds = Math.max(0, Math.floor(milliseconds / 1000));
      setElementText(
        recordElapsed,
        `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`,
      );
    },
    setRecordingStatus(message, kind) {
      setElementText(recordStatus, message);
      recordStatus.dataset.state = kind;
      recordStatus.setAttribute('role', kind === 'error' ? 'alert' : 'status');
      recordStatus.setAttribute('aria-live', kind === 'error' ? 'assertive' : 'polite');
    },
    showRecording(name, size, durationMs, url) {
      recordingReady = true;
      recordDetails.hidden = false;
      setElementText(
        recordDetails,
        `${name} · ${size} encoded · ${formatCallsTimestamp(durationMs)} duration`,
      );
      if (url) {
        recordPreview.src = url;
        recordPreview.hidden = false;
      } else {
        recordPreview.hidden = true;
        recordPreview.removeAttribute('src');
      }
      syncControls();
    },
    clearRecording() {
      recordingReady = false;
      recordDetails.hidden = true;
      setElementText(recordDetails, '');
      if (typeof recordPreview.pause === 'function') recordPreview.pause();
      recordPreview.removeAttribute('src');
      if (typeof recordPreview.load === 'function') recordPreview.load();
      recordPreview.hidden = true;
      this.setRecordingElapsed(0);
      syncControls();
    },
    reset() {
      this.clearUpload();
      this.clearRecording();
      transcriptionBusy = false;
      recordingUiState = 'idle';
      submit.textContent = 'Transcribe';
      recordSubmit.textContent = 'Transcribe recorded audio';
      cancel.hidden = true;
      recordIndicator.hidden = true;
      clearResult();
      this.setStatus('Select a canonical WAV file to begin.', 'idle');
      this.setRecordingStatus('Start a recording when you are ready.', 'idle');
      syncControls();
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
  const recordStart = doc.getElementById('calls-record-start-btn');
  const saveTitle = doc.getElementById('calls-save-title');
  const show = () => {
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
    recordStart.focus();
  };
  const close = () => {
    controller.onPanelHidden();
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
  recordStart.addEventListener('click', () => controller.startRecording());
  doc.getElementById('calls-record-stop-btn').addEventListener('click', () => controller.stopRecording());
  doc.getElementById('calls-record-cancel-btn').addEventListener('click', () => controller.cancelRecording());
  doc.getElementById('calls-record-submit-btn').addEventListener('click', () => controller.submit());
  doc.getElementById('calls-record-clear-btn').addEventListener('click', () => {
    controller.reset();
    recordStart.focus();
  });
  doc.getElementById('calls-reset-btn').addEventListener('click', () => {
    controller.reset();
    fileInput.focus();
  });
  doc.getElementById('calls-copy-btn').addEventListener('click', () => controller.copyTranscript());
  doc.getElementById('calls-save-btn').addEventListener('click', () => controller.saveToLibrary(saveTitle.value));
  modal.addEventListener('click', (event) => { if (event.target === modal) close(); });
  doc.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) close();
  });
  if (typeof globalThis.MutationObserver === 'function') {
    const observer = new globalThis.MutationObserver(() => {
      if (modal.classList.contains('hidden') || modal.style.display === 'none') {
        controller.onPanelHidden();
      }
    });
    observer.observe(modal, { attributes: true, attributeFilter: ['class', 'style'] });
  }
  if (typeof globalThis.addEventListener === 'function') {
    globalThis.addEventListener('pagehide', () => controller.destroy());
    globalThis.addEventListener('beforeunload', () => controller.destroy());
  }
  return controller;
}

export default { init };
