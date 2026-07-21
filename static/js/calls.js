import {
  formatDateTime,
  formatFileSize,
  formatNumber,
  getLocale,
  getTimezone,
  languageLabel,
  normalizeLocale,
  resolveAnalysisLanguage,
  subscribe as subscribeLocale,
  t,
} from './i18n.js';

const ENDPOINT = '/api/marketmatch/stt/transcribe';
const ANALYSIS_ENDPOINT = '/api/marketmatch/calls/analyze';
const DOCUMENT_ENDPOINT = '/api/document';
const DOCUMENT_HISTORY_ENDPOINT = '/api/documents/library?search=MarketMatch&sort=recent&offset=0&limit=20';
const CALLS_DOCUMENT_MARKER = [
  '# MarketMatch Calls Transcript',
  '',
  '**AI-generated transcript. Review before relying on it for operational decisions.**',
  '',
  'Source: MarketMatch Calls',
].join('\n');
const CAPTURE_DOCUMENT_MARKER = [
  '# MarketMatch Capture',
  '',
  '**AI-generated transcript. Review before relying on it for operational decisions.**',
  '',
  'Source: MarketMatch Capture',
].join('\n');
const MAX_CALLS_HISTORY_ITEMS = 20;
export const MAX_CALLS_WAV_BYTES = 20 * 1024 * 1024;
export const MAX_CALLS_AUDIO_BYTES = 200 * 1024 * 1024;
export const CALLS_WAV_SAMPLE_RATE = 16000;
export const MAX_CAPTURE_PHOTO_BYTES = 20 * 1024 * 1024;
export const MAX_CAPTURE_VIDEO_BYTES = 100 * 1024 * 1024;
export const MAX_CAPTURE_CAPTION_CHARS = 500;
export const MAX_CAPTURE_NOTES_CHARS = 4000;
export const CAPTURE_TYPES = Object.freeze([
  'meeting',
  'field_observation',
  'walkthrough',
  'training',
  'voice_note',
  'supplier_conversation',
  'other',
]);
const CAPTURE_MEDIA_TYPES = Object.freeze({
  photo: Object.freeze(['image/jpeg', 'image/png', 'image/webp']),
  video: Object.freeze(['video/mp4', 'video/webm', 'video/quicktime']),
});
const CAPTURE_VIDEO_RECORDING_MIME_TYPES = Object.freeze([
  'video/webm;codecs=vp9,opus',
  'video/webm;codecs=vp8,opus',
  'video/webm',
  'video/mp4;codecs=avc1.42E01E,mp4a.40.2',
  'video/mp4',
]);
const TIMELINE_I18N_KEYS = Object.freeze({
  CAPTURE_STARTED: 'capture.timeline.started',
  CAPTURE_RECORDING_STARTED: 'capture.timeline.recording_started',
  CAPTURE_RECORDING_STOPPED: 'capture.timeline.recording_stopped',
  CAPTURE_RECORDING_CANCELLED: 'capture.timeline.recording_cancelled',
  CAPTURE_TRANSCRIPTION_STARTED: 'capture.timeline.transcription_started',
  CAPTURE_TRANSCRIPTION_COMPLETED: 'capture.timeline.transcription_completed',
  CAPTURE_TRANSCRIPTION_FAILED: 'capture.timeline.transcription_failed',
  CAPTURE_ANALYSIS_GENERATED: 'capture.timeline.analysis_generated',
  CAPTURE_ANALYSIS_FAILED: 'capture.timeline.analysis_failed',
  CAPTURE_PHOTO_ADDED: 'capture.timeline.photo_added',
  CAPTURE_VIDEO_ADDED: 'capture.timeline.video_added',
  CAPTURE_ATTACHMENT_REMOVED: 'capture.timeline.attachment_removed',
  CAPTURE_SAVED_TO_LIBRARY: 'capture.timeline.saved',
});
const WAV_HEADER_BYTES = 44;
const MAX_RECORDING_MILLISECONDS = Math.floor(
  ((MAX_CALLS_WAV_BYTES - WAV_HEADER_BYTES) / 2 / CALLS_WAV_SAMPLE_RATE) * 1000,
);

const STATUS_MESSAGES = Object.freeze({
  401: 'capture.error.session', 403: 'capture.error.forbidden', 408: 'capture.error.timeout',
  413: 'capture.error.wav_size', 415: 'capture.error.wav_format', 422: 'capture.error.wav_invalid',
  429: 'capture.error.busy', 502: 'capture.error.invalid_result',
  503: 'capture.error.model_unavailable', 504: 'capture.error.timeout',
});
const TRANSCRIPTION_CODE_MESSAGES = Object.freeze({
  INPUT_LIMIT_EXCEEDED: 'capture.error.audio_size',
  DURATION_LIMIT_EXCEEDED: 'capture.error.audio_duration',
  DECODED_OUTPUT_LIMIT_EXCEEDED: 'capture.error.audio_duration',
  UNSUPPORTED_FORMAT: 'capture.error.audio_format',
  EXCESSIVE_STREAMS: 'capture.error.audio_format',
  EXTERNAL_MEDIA_REJECTED: 'capture.error.audio_format',
  MALFORMED_AUDIO: 'capture.error.audio_malformed',
  NO_AUDIO_STREAM: 'capture.error.no_audio_stream',
  CONVERSION_FAILED: 'capture.error.conversion_failed',
  AUDIO_PROBE_TIMEOUT: 'capture.error.inspection_timeout',
  CONVERSION_TIMEOUT: 'capture.error.conversion_timeout',
  WORKER_TIMEOUT: 'capture.error.transcription_timeout',
  WORKER_CRASHED: 'capture.error.worker_crashed',
  WORKER_FAILED: 'capture.error.transcription_failed',
  MODEL_UNAVAILABLE: 'capture.error.model_unavailable',
  BACKEND_FAILED: 'capture.error.transcription_failed',
  INVALID_BACKEND_RESULT: 'capture.error.invalid_result',
  SEGMENT_LIMIT_EXCEEDED: 'capture.error.invalid_result',
  TRANSCRIPT_LIMIT_EXCEEDED: 'capture.error.invalid_result',
  WORKER_PROTOCOL_ERROR: 'capture.error.invalid_result',
  INPUT_DISCONNECTED: 'capture.error.upload_disconnected',
  INPUT_SIZE_MISMATCH: 'capture.error.upload_disconnected',
  INPUT_READ_FAILED: 'capture.error.upload_disconnected',
  INPUT_READ_TIMEOUT: 'capture.error.upload_timeout',
  FFMPEG_UNAVAILABLE: 'capture.error.ffmpeg_unavailable',
});

const GENERIC_FAILURE = 'capture.status.transcription_failed';
const RECORDING_FAILURE = 'capture.error.recording_failed';
const DOCUMENT_SAVE_FAILURE = 'capture.status.save_failed';
const HISTORY_FAILURE = 'capture.history.failed';
const ANALYSIS_FAILURE = 'capture.status.analysis_failed';
const ANALYSIS_INVALID_RESPONSE = 'capture.status.analysis_invalid';

const ANALYSIS_STATUS_MESSAGES = Object.freeze({
  401: 'capture.error.session', 403: 'capture.error.forbidden',
  413: 'capture.status.analysis_too_long', 422: 'capture.status.analysis_failed',
  502: ANALYSIS_INVALID_RESPONSE,
  503: 'capture.error.model_unavailable', 504: 'capture.error.timeout',
});
const ANALYSIS_RESPONSE_LIMITS = Object.freeze({
  summary: 2000,
  items: 20,
  itemText: 1000,
  ownerOrDueDate: 200,
  totalText: 16000,
});

const DOCUMENT_STATUS_MESSAGES = Object.freeze({
  400: 'capture.status.title_required', 401: 'capture.error.session',
  403: 'capture.error.forbidden', 413: 'capture.status.save_failed',
  422: 'capture.status.title_required',
});

export class CallsUiError extends Error {
  constructor(code, message, status = 0) {
    super(typeof message === 'string' && (message.startsWith('capture.') || message.startsWith('common.'))
      ? t(message) : message);
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
    throw new CallsUiError('RECORDING_TOO_LARGE', 'capture.error.recording_too_large');
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
    throw new CallsUiError('RECORDING_EMPTY', 'capture.error.recording_empty');
  }
  const dataBytes = samples.length * 2;
  if (WAV_HEADER_BYTES + dataBytes > MAX_CALLS_WAV_BYTES) {
    throw new CallsUiError('RECORDING_TOO_LARGE', 'capture.error.recording_too_large');
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
    throw new CallsUiError('RECORDING_EMPTY', 'capture.error.recording_empty');
  }
  if (byteLength > MAX_CALLS_WAV_BYTES) {
    throw new CallsUiError('RECORDING_TOO_LARGE', 'capture.error.recording_too_large');
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
    throw new CallsUiError('RECORDING_EMPTY', 'capture.error.recording_empty');
  }
  let context;
  try {
    context = typeof createAudioContext === 'function' ? createAudioContext() : null;
  } catch (_) {
    throw new CallsUiError('AUDIO_UNSUPPORTED', 'capture.error.audio_unsupported');
  }
  if (!context || typeof context.decodeAudioData !== 'function') {
    throw new CallsUiError('AUDIO_UNSUPPORTED', 'capture.error.audio_unsupported');
  }
  try {
    const decoded = await context.decodeAudioData(await blob.arrayBuffer());
    if (!decoded || !Number.isFinite(decoded.sampleRate) || decoded.sampleRate <= 0
        || !Number.isSafeInteger(decoded.numberOfChannels) || decoded.numberOfChannels <= 0
        || !Number.isSafeInteger(decoded.length) || decoded.length <= 0) {
      throw new CallsUiError('RECORDING_EMPTY', 'capture.error.recording_empty');
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
  const displayName = sanitizeCallsFilename(file && file.name);
  if (!file || typeof file.name !== 'string'
      || !/\.(wav|m4a|mp3|aac|caf|flac|ogg|opus|webm|mp4|mov)$/i.test(displayName)) {
    return { ok: false, code: 'AUDIO_REQUIRED', message: t('capture.error.audio_format') };
  }
  if (!Number.isSafeInteger(file.size) || file.size <= 0) {
    return { ok: false, code: 'EMPTY_FILE', message: t('capture.error.audio_empty') };
  }
  if (file.size > MAX_CALLS_AUDIO_BYTES) {
    return { ok: false, code: 'FILE_TOO_LARGE', message: t('capture.error.audio_size') };
  }
  return { ok: true };
}

export function sanitizeCallsFilename(value) {
  if (typeof value !== 'string') return 'audio';
  const basename = value.replaceAll('\\', '/').split('/').at(-1);
  const cleaned = [...(basename || '')]
    .filter((character) => character >= ' ' && character !== '\u007f')
    .join('').trim();
  return cleaned.slice(0, 128) || 'audio';
}

export function formatEncodedSize(bytes) {
  return formatFileSize(bytes);
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
  return formatDateTime(value);
}

export function defaultCallsDocumentTitle(date = new Date()) {
  return t('capture.default_title', { value: formatCallsLocalDateTime(date) });
}

export function captureMediaFingerprint(file) {
  if (!file || typeof file !== 'object') return '';
  return [file.name, file.size, file.type, file.lastModified]
    .map((value) => String(value ?? ''))
    .join('\u001f');
}

export function validateCaptureMediaFile(file, kind) {
  if (!file || typeof file !== 'object' || typeof file.name !== 'string'
      || !Number.isSafeInteger(file.size) || typeof file.type !== 'string') {
    return { ok: false, code: 'MEDIA_INVALID', message: t('capture.status.preview_failed') };
  }
  if (!Object.prototype.hasOwnProperty.call(CAPTURE_MEDIA_TYPES, kind)) {
    return { ok: false, code: 'MEDIA_INVALID', message: t('capture.status.preview_failed') };
  }
  if (file.size <= 0) {
    return { ok: false, code: 'MEDIA_EMPTY', message: t('capture.error.media_empty') };
  }
  if (!CAPTURE_MEDIA_TYPES[kind].includes(file.type.toLowerCase())) {
    return {
      ok: false,
      code: 'MEDIA_TYPE_UNSUPPORTED',
      message: t(`capture.status.${kind}_unsupported`),
    };
  }
  const limit = kind === 'photo' ? MAX_CAPTURE_PHOTO_BYTES : MAX_CAPTURE_VIDEO_BYTES;
  if (file.size > limit) {
    return {
      ok: false,
      code: 'MEDIA_TOO_LARGE',
      message: t(`capture.status.${kind}_oversize`),
    };
  }
  return { ok: true };
}

function _captureDocumentText(value, fallback = '') {
  const normalized = typeof value === 'string'
    ? value.trim().replace(/[\r\n]+/g, ' ')
    : fallback;
  return normalized
    .replace(/\\/g, '\\\\')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function _captureMediaDocumentLines(items, emptyMessage) {
  if (!Array.isArray(items) || items.length === 0) return [emptyMessage];
  return items.map((item) => {
    const name = _captureDocumentText(item && item.name, 'Unnamed media');
    const caption = _captureDocumentText(item && item.caption, 'No caption.');
    return `- ${name} — Caption: ${caption} — In memory only; not persisted.`;
  });
}

export function buildCaptureDocumentContent({
  title = '',
  result = null,
  createdAt = new Date(),
  captureType = CAPTURE_TYPES[0],
  notes = '',
  photos = [],
  videos = [],
  interfaceLocale = getLocale(),
  displayTimezone = getTimezone(),
} = {}) {
  const value = result === null ? null : _validateSuccessPayload(result);
  const transcript = value
    ? (value.transcript_text || '(No speech was detected.)')
    : '(No transcript was created.)';
  const segments = value && value.segments.length
    ? value.segments.map((segment) => (
      `[${formatCallsTimestamp(segment.start_ms)} – ${formatCallsTimestamp(segment.end_ms)}] ${segment.text}`
    )).join('\n\n')
    : '(No speech segments were detected.)';
  const details = [
    `Capture title: ${_captureDocumentText(title, 'Untitled Capture')}`,
    `Capture type: ${_captureDocumentText(captureType, CAPTURE_TYPES[0])}`,
    `Created: ${(createdAt instanceof Date ? createdAt : new Date(createdAt)).toISOString()}`,
    `Interface locale at save time: ${normalizeLocale(interfaceLocale) || 'es'}`,
    `Display timezone: ${typeof displayTimezone === 'string' && displayTimezone ? displayTimezone : 'UTC'}`,
    `Detected transcript language: ${value ? value.language : 'und'}`,
  ];
  if (value) details.push(`Duration: ${formatCallsTimestamp(value.duration_ms)}`);
  return [
    '# MarketMatch Capture',
    '',
    '**AI-generated transcript. Review before relying on it for operational decisions.**',
    '',
    'Source: MarketMatch Capture',
    ...details,
    '',
    '## General notes',
    '',
    _captureDocumentText(notes, '(No general notes.)'),
    '',
    '## Transcript',
    '',
    transcript,
    '',
    '## Timestamped segments',
    '',
    segments,
    '',
    '## Photos',
    '',
    ..._captureMediaDocumentLines(photos, '(No photographs were added.)'),
    '',
    '## Videos',
    '',
    ..._captureMediaDocumentLines(videos, '(No videos were added.)'),
    '',
    '**Visual media was not persisted with this Capture.**',
  ].join('\n');
}

export function buildCallsDocumentContent(result, createdAt = new Date()) {
  return buildCaptureDocumentContent({ result, createdAt });
}

export function isCallsTranscriptDocument(documentValue) {
  return Boolean(
    documentValue
    && typeof documentValue === 'object'
    && !Array.isArray(documentValue)
    && typeof documentValue.id === 'string'
    && documentValue.id.trim()
    && typeof documentValue.title === 'string'
    && documentValue.title.trim()
    && typeof documentValue.preview === 'string'
    && (
      documentValue.preview.startsWith(CAPTURE_DOCUMENT_MARKER)
      || documentValue.preview.startsWith(CALLS_DOCUMENT_MARKER)
    ),
  );
}

export function filterCallsHistoryDocuments(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)
      || !Array.isArray(payload.documents)) {
    throw new CallsUiError('HISTORY_MALFORMED_RESPONSE', HISTORY_FAILURE);
  }
  const seen = new Set();
  const history = [];
  for (const documentValue of payload.documents.slice(0, MAX_CALLS_HISTORY_ITEMS)) {
    if (!isCallsTranscriptDocument(documentValue)) continue;
    const documentId = documentValue.id.trim();
    if (seen.has(documentId)) continue;
    seen.add(documentId);
    history.push({
      id: documentId,
      title: documentValue.title.trim(),
      created_at: typeof documentValue.created_at === 'string' ? documentValue.created_at : null,
      updated_at: typeof documentValue.updated_at === 'string' ? documentValue.updated_at : null,
    });
  }
  return history;
}

export function formatCallsHistoryDate(value) {
  if (typeof value !== 'string' || !value) return '';
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) return '';
  return formatDateTime(parsed);
}

export function fixedCallsError(status) {
  return t(STATUS_MESSAGES[status] || GENERIC_FAILURE);
}

export function fixedCallsCodeError(code, status) {
  return t(TRANSCRIPTION_CODE_MESSAGES[code] || STATUS_MESSAGES[status] || GENERIC_FAILURE);
}

export function fixedCallsAnalysisError(status) {
  return t(ANALYSIS_STATUS_MESSAGES[status] || ANALYSIS_FAILURE);
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
    const normalized = segment.language === undefined ? null : segment.language;
    if (normalized !== null && !['es', 'en', 'zh', 'und'].includes(normalized)) {
      throw new CallsUiError('MALFORMED_RESPONSE', GENERIC_FAILURE);
    }
    return {
      start_ms: segment.start_ms,
      end_ms: segment.end_ms,
      text: segment.text,
      ...(normalized ? { language: normalized } : {}),
    };
  });
  if (segments.map((segment) => segment.text).join('') !== value.transcript_text) {
    throw new CallsUiError('MALFORMED_RESPONSE', GENERIC_FAILURE);
  }
  const language = value.language === undefined ? 'und' : value.language;
  if (!['es', 'en', 'zh', 'und'].includes(language)) {
    throw new CallsUiError('MALFORMED_RESPONSE', GENERIC_FAILURE);
  }
  const confidence = value.language_confidence;
  if (confidence !== undefined && confidence !== null
      && (!Number.isFinite(confidence) || confidence < 0 || confidence > 1)) {
    throw new CallsUiError('MALFORMED_RESPONSE', GENERIC_FAILURE);
  }
  return {
    duration_ms: value.duration_ms,
    transcript_text: value.transcript_text,
    segments,
    language,
    language_confidence: confidence == null ? null : confidence,
  };
}

export async function requestCallsTranscription(
  file,
  { sourceLanguage = 'auto', fetchImpl = globalThis.fetch, signal } = {},
) {
  const validation = validateCallsFile(file);
  if (!validation.ok) throw new CallsUiError(validation.code, validation.message);
  if (!['auto', 'es', 'en', 'zh'].includes(sourceLanguage)) {
    throw new CallsUiError('TRANSCRIPTION_LANGUAGE_INVALID', GENERIC_FAILURE);
  }
  let response;
  try {
    response = await fetchImpl(ENDPOINT, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/octet-stream',
        'X-MarketMatch-Transcription-Language': sourceLanguage,
      },
      body: file,
      signal,
    });
  } catch (error) {
    if (error && error.name === 'AbortError') {
      throw new CallsUiError('CANCELLED', 'capture.status.transcription_cancelled');
    }
    throw new CallsUiError('REQUEST_FAILED', GENERIC_FAILURE);
  }
  if (!response || response.ok !== true) {
    const status = response && Number.isInteger(response.status) ? response.status : 0;
    let code = '';
    try {
      const failure = response && typeof response.json === 'function' ? await response.json() : null;
      if (failure && typeof failure.error === 'string') code = failure.error;
    } catch (_) { /* fixed status fallback */ }
    throw new CallsUiError(code || `HTTP_${status || 'ERROR'}`, fixedCallsCodeError(code, status), status);
  }
  let payload;
  try {
    payload = await response.json();
  } catch (_) {
    throw new CallsUiError('MALFORMED_RESPONSE', GENERIC_FAILURE);
  }
  return _validateSuccessPayload(payload);
}

function _validateAnalysisPayload(value) {
  const hasOwn = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
  const exactKeys = (object, expected) => {
    const keys = Object.keys(object);
    return keys.length === expected.length && expected.every((key) => hasOwn(object, key));
  };
  if (!value || typeof value !== 'object' || Array.isArray(value)
      || !exactKeys(value, ['summary', 'decisions', 'action_items', 'open_questions'])
      || typeof value.summary !== 'string' || !value.summary.trim()
      || _analysisTextCharacters(value.summary) > ANALYSIS_RESPONSE_LIMITS.summary
      || !Array.isArray(value.decisions) || !Array.isArray(value.action_items)
      || !Array.isArray(value.open_questions)
      || value.decisions.length > ANALYSIS_RESPONSE_LIMITS.items
      || value.action_items.length > ANALYSIS_RESPONSE_LIMITS.items
      || value.open_questions.length > ANALYSIS_RESPONSE_LIMITS.items) {
    throw new CallsUiError('ANALYSIS_MALFORMED_RESPONSE', ANALYSIS_INVALID_RESPONSE);
  }
  const stringList = (items) => items.map((item) => {
    if (typeof item !== 'string' || !item.trim()
        || _analysisTextCharacters(item) > ANALYSIS_RESPONSE_LIMITS.itemText) {
      throw new CallsUiError('ANALYSIS_MALFORMED_RESPONSE', ANALYSIS_INVALID_RESPONSE);
    }
    return item;
  });
  const actionItems = value.action_items.map((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)
        || !exactKeys(item, ['task', 'owner', 'due_date'])
        || typeof item.task !== 'string' || !item.task.trim()
        || _analysisTextCharacters(item.task) > ANALYSIS_RESPONSE_LIMITS.itemText
        || (item.owner !== null && typeof item.owner !== 'string')
        || (item.due_date !== null && typeof item.due_date !== 'string')
        || (typeof item.owner === 'string'
          && (!item.owner.trim()
            || _analysisTextCharacters(item.owner) > ANALYSIS_RESPONSE_LIMITS.ownerOrDueDate))
        || (typeof item.due_date === 'string'
          && (!item.due_date.trim()
            || _analysisTextCharacters(item.due_date) > ANALYSIS_RESPONSE_LIMITS.ownerOrDueDate))) {
      throw new CallsUiError('ANALYSIS_MALFORMED_RESPONSE', ANALYSIS_INVALID_RESPONSE);
    }
    return { task: item.task, owner: item.owner, due_date: item.due_date };
  });
  const analysis = {
    summary: value.summary,
    decisions: stringList(value.decisions),
    action_items: actionItems,
    open_questions: stringList(value.open_questions),
  };
  let totalText = _analysisTextCharacters(analysis.summary);
  totalText += analysis.decisions.reduce(
    (total, item) => total + _analysisTextCharacters(item), 0,
  );
  totalText += analysis.open_questions.reduce(
    (total, item) => total + _analysisTextCharacters(item), 0,
  );
  totalText += analysis.action_items.reduce(
    (total, item) => total + _analysisTextCharacters(item.task)
      + _analysisTextCharacters(item.owner || '') + _analysisTextCharacters(item.due_date || ''),
    0,
  );
  if (totalText > ANALYSIS_RESPONSE_LIMITS.totalText) {
    throw new CallsUiError('ANALYSIS_MALFORMED_RESPONSE', ANALYSIS_INVALID_RESPONSE);
  }
  return analysis;
}

function _analysisTextCharacters(value) {
  return Array.from(value).length;
}

export async function requestCallsAnalysis(
  transcript,
  {
    outputLanguage = 'auto', transcriptLanguage = 'und', fetchImpl = globalThis.fetch, signal,
  } = {},
) {
  if (typeof transcript !== 'string' || !transcript.trim()) {
    throw new CallsUiError('ANALYSIS_EMPTY', 'capture.status.analysis_empty');
  }
  const canonicalOutputLanguage = outputLanguage === 'auto' || outputLanguage === 'zh-Hant'
    ? outputLanguage : normalizeLocale(outputLanguage);
  if (!canonicalOutputLanguage || !['auto', 'es', 'en', 'zh-Hans', 'zh-Hant'].includes(canonicalOutputLanguage)) {
    throw new CallsUiError('ANALYSIS_LANGUAGE_INVALID', ANALYSIS_FAILURE);
  }
  if (!['und', 'es', 'en', 'zh'].includes(transcriptLanguage)) {
    throw new CallsUiError('ANALYSIS_LANGUAGE_INVALID', ANALYSIS_FAILURE);
  }
  let response;
  try {
    response = await fetchImpl(ANALYSIS_ENDPOINT, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        transcript,
        output_language: canonicalOutputLanguage,
        transcript_language: transcriptLanguage,
      }),
      signal,
    });
  } catch (error) {
    if (error && error.name === 'AbortError') {
      throw new CallsUiError('ANALYSIS_CANCELLED', 'capture.status.analysis_cancelled');
    }
    throw new CallsUiError('ANALYSIS_REQUEST_FAILED', ANALYSIS_FAILURE);
  }
  if (!response || response.ok !== true) {
    const status = response && Number.isInteger(response.status) ? response.status : 0;
    throw new CallsUiError(
      `ANALYSIS_HTTP_${status || 'ERROR'}`,
      fixedCallsAnalysisError(status),
      status,
    );
  }
  let payload;
  try {
    payload = await response.json();
  } catch (_) {
    throw new CallsUiError('ANALYSIS_MALFORMED_RESPONSE', ANALYSIS_INVALID_RESPONSE);
  }
  return _validateAnalysisPayload(payload);
}

export function formatCallsAnalysisText(value) {
  const analysis = _validateAnalysisPayload(value);
  const listText = (items) => items.length
    ? items.map((item) => `- ${item}`).join('\n')
    : t('common.none_identified');
  const actions = analysis.action_items.length
    ? analysis.action_items.map((item, index) => [
      `${index + 1}. ${t('capture.analysis.task', { value: item.task })}`,
      t('capture.analysis.owner', { value: item.owner === null ? t('capture.analysis.not_stated') : item.owner }),
      t('capture.analysis.due_date', { value: item.due_date === null ? t('capture.analysis.not_stated') : item.due_date }),
    ].join('\n')).join('\n\n')
    : t('common.none_identified');
  return [
    t('capture.analysis.summary'),
    analysis.summary,
    '',
    t('capture.analysis.decisions'),
    listText(analysis.decisions),
    '',
    t('capture.analysis.actions'),
    actions,
    '',
    t('capture.analysis.questions'),
    listText(analysis.open_questions),
  ].join('\n');
}

export async function requestCallsDocumentSave(
  {
    title,
    result = null,
    createdAt,
    captureType = CAPTURE_TYPES[0],
    notes = '',
    photos = [],
    videos = [],
    interfaceLocale = getLocale(),
    displayTimezone = getTimezone(),
  },
  { fetchImpl = globalThis.fetch, signal } = {},
) {
  const normalizedTitle = typeof title === 'string' ? title.trim() : '';
  if (!normalizedTitle) {
    throw new CallsUiError('DOCUMENT_TITLE_REQUIRED', 'capture.status.title_required');
  }
  const content = buildCaptureDocumentContent({
    title: normalizedTitle,
    result,
    createdAt,
    captureType,
    notes,
    photos,
    videos,
    interfaceLocale,
    displayTimezone,
  });
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
      throw new CallsUiError('SAVE_CANCELLED', 'capture.status.save_cancelled');
    }
    throw new CallsUiError('DOCUMENT_SAVE_FAILED', DOCUMENT_SAVE_FAILURE);
  }
  if (!response || response.ok !== true) {
    const status = response && Number.isInteger(response.status) ? response.status : 0;
    throw new CallsUiError(
      `DOCUMENT_HTTP_${status || 'ERROR'}`,
      t(DOCUMENT_STATUS_MESSAGES[status] || DOCUMENT_SAVE_FAILURE),
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

export async function requestCallsHistory({ fetchImpl = globalThis.fetch, signal } = {}) {
  let response;
  try {
    response = await fetchImpl(DOCUMENT_HISTORY_ENDPOINT, {
      method: 'GET',
      credentials: 'same-origin',
      signal,
    });
  } catch (error) {
    if (error && error.name === 'AbortError') {
      throw new CallsUiError('HISTORY_CANCELLED', 'capture.history.cancelled');
    }
    throw new CallsUiError('HISTORY_REQUEST_FAILED', HISTORY_FAILURE);
  }
  if (!response || response.ok !== true) {
    const status = response && Number.isInteger(response.status) ? response.status : 0;
    const message = status === 401
      ? 'capture.history.session'
      : status === 403
        ? 'capture.history.forbidden'
        : HISTORY_FAILURE;
    throw new CallsUiError(`HISTORY_HTTP_${status || 'ERROR'}`, message, status);
  }
  let payload;
  try {
    payload = await response.json();
  } catch (_) {
    throw new CallsUiError('HISTORY_MALFORMED_RESPONSE', HISTORY_FAILURE);
  }
  return filterCallsHistoryDocuments(payload);
}

export function setElementText(element, value) {
  if (element) element.textContent = String(value ?? '');
}

export function selectCaptureVideoMimeType(MediaRecorderClass = globalThis.MediaRecorder) {
  if (!MediaRecorderClass || typeof MediaRecorderClass.isTypeSupported !== 'function') return null;
  return CAPTURE_VIDEO_RECORDING_MIME_TYPES.find((type) => MediaRecorderClass.isTypeSupported(type)) || null;
}

export function capturePhotoBlobFromVideo(video, doc = globalThis.document) {
  return new Promise((resolve, reject) => {
    const width = Number(video && video.videoWidth);
    const height = Number(video && video.videoHeight);
    if (!doc || !Number.isFinite(width) || width <= 0 || !Number.isFinite(height) || height <= 0) {
      reject(new Error('camera frame unavailable'));
      return;
    }
    const canvas = doc.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext('2d');
    if (!context || typeof context.drawImage !== 'function' || typeof canvas.toBlob !== 'function') {
      reject(new Error('camera capture unavailable'));
      return;
    }
    context.drawImage(video, 0, 0, width, height);
    canvas.toBlob((blob) => {
      if (blob && blob.type === 'image/jpeg') resolve(blob);
      else reject(new Error('camera capture failed'));
    }, 'image/jpeg', 0.92);
  });
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
  createMediaFile = (parts, name, options) => new File(parts, name, options),
  capturePhotoFrame = null,
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
  openDocument,
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
  let historyActive = false;
  let historyController = null;
  let historyGeneration = 0;
  let panelVisible = false;
  let historyHasLoaded = false;
  let historyRefreshPending = false;
  let analysis = null;
  let analysisActive = false;
  let analysisController = null;
  let analysisGeneration = 0;
  let captureCreatedAt = new Date(now());
  let captureTitle = defaultCallsDocumentTitle(captureCreatedAt);
  let captureType = CAPTURE_TYPES[0];
  let captureNotes = '';
  let photos = [];
  let videos = [];
  let mediaFingerprints = new Set();
  let mediaSequence = 0;
  let timelineSequence = 1;
  let timeline = [{ id: 'capture-event-1', code: 'CAPTURE_STARTED', params: {}, at: captureCreatedAt }];
  let historyDocuments = [];
  let transcriptionLanguage = 'auto';
  let analysisLanguage = 'auto';
  let resolvedAnalysisOutputLanguage = getLocale();
  let cameraMode = null;
  let cameraStream = null;
  let cameraFacingMode = 'environment';
  let cameraGeneration = 0;
  let cameraRecorder = null;
  let cameraRecordingState = 'idle';
  let cameraRecordingChunks = [];
  let cameraRecordingBytes = 0;
  let cameraRecordingOversized = false;
  let cameraRecordingMime = null;
  let cameraSwitchSupported = false;

  const callView = (method, ...args) => {
    if (view && typeof view[method] === 'function') view[method](...args);
  };

  function syncResolvedAnalysisLanguage() {
    resolvedAnalysisOutputLanguage = resolveAnalysisLanguage(
      analysisLanguage,
      result && result.language,
      result ? result.segments.map((segment) => segment.language).filter(Boolean) : [],
    ) || 'es';
    callView('setResolvedAnalysisLanguage', resolvedAnalysisOutputLanguage);
    return resolvedAnalysisOutputLanguage;
  }

  function captureDetails() {
    return {
      title: captureTitle,
      type: captureType,
      notes: captureNotes,
      createdAt: captureCreatedAt,
    };
  }

  function hasSavableCapture() {
    return Boolean(result || captureNotes.trim() || photos.length || videos.length);
  }

  function syncCaptureSaveAvailability() {
    callView('setCaptureSaveReady', hasSavableCapture() && !saved);
  }

  function addTimelineEvent(code, params = {}) {
    timelineSequence += 1;
    timeline.push({
      id: `capture-event-${timelineSequence}`,
      code,
      params: { ...params },
      at: new Date(now()),
    });
    callView('renderTimeline', timeline.map((item) => ({ ...item, params: { ...item.params } })));
  }

  function markCaptureChanged() {
    if (saved) {
      saved = false;
      callView('setSaved', false);
      callView('setSaveStatus', t('capture.status.changed'), 'idle');
    }
    syncCaptureSaveAvailability();
  }

  function mediaItems(kind) {
    return kind === 'photo' ? photos : videos;
  }

  function renderMedia(kind) {
    callView(
      'renderCaptureMedia',
      kind,
      mediaItems(kind).map((item) => ({ ...item, file: undefined })),
      updateMediaCaption,
      removeCaptureMedia,
    );
  }

  function clearAllMedia() {
    const hadMedia = photos.length > 0 || videos.length > 0;
    for (const item of [...photos, ...videos]) {
      if (!item.url) continue;
      try { revokeObjectURL(item.url); } catch (_) { /* best-effort release */ }
    }
    photos = [];
    videos = [];
    mediaFingerprints = new Set();
    renderMedia('photo');
    renderMedia('video');
    callView('setMediaStatus', 'photo', t('capture.photos.empty'), 'idle');
    callView('setMediaStatus', 'video', t('capture.videos.empty'), 'idle');
    if (hadMedia && saved) {
      saved = false;
      callView('setSaved', false);
    }
    syncCaptureSaveAvailability();
  }

  function addCaptureMedia(kind, file) {
    if (active || saveActive || analysisActive
        || ['permission', 'recording', 'processing'].includes(recordingState)) return false;
    const validation = validateCaptureMediaFile(file, kind);
    if (!validation.ok) {
      callView('setMediaStatus', kind, validation.message, 'error');
      return false;
    }
    const fingerprint = captureMediaFingerprint(file);
    if (!fingerprint || mediaFingerprints.has(fingerprint)) {
      callView('setMediaStatus', kind, t(`capture.status.${kind}_duplicate`), 'error');
      return false;
    }
    let url = null;
    try { url = createObjectURL(file); } catch (_) { url = null; }
    if (!url) {
      callView('setMediaStatus', kind, t('capture.status.preview_failed'), 'error');
      return false;
    }
    mediaSequence += 1;
    const item = {
      id: `capture-media-${mediaSequence}`,
      kind,
      file,
      url,
      name: file.name,
      mime: file.type.toLowerCase(),
      size: file.size,
      lastModified: Number.isFinite(file.lastModified) ? file.lastModified : 0,
      addedAt: new Date(now()),
      caption: '',
      persistence: 'IN_MEMORY_ONLY',
      fingerprint,
    };
    if (kind === 'photo') photos.push(item);
    else videos.push(item);
    mediaFingerprints.add(fingerprint);
    renderMedia(kind);
    callView('setMediaStatus', kind, t(`capture.status.${kind}_added`), 'success');
    addTimelineEvent(kind === 'photo' ? 'CAPTURE_PHOTO_ADDED' : 'CAPTURE_VIDEO_ADDED', { filename: file.name });
    markCaptureChanged();
    return true;
  }

  function updateMediaCaption(kind, id, caption) {
    const item = mediaItems(kind).find((candidate) => candidate.id === id);
    if (!item || typeof caption !== 'string') return false;
    item.caption = Array.from(caption.trim()).slice(0, MAX_CAPTURE_CAPTION_CHARS).join('');
    markCaptureChanged();
    return true;
  }

  function removeCaptureMedia(kind, id) {
    const items = mediaItems(kind);
    const index = items.findIndex((item) => item.id === id);
    if (index < 0) return false;
    const [removed] = items.splice(index, 1);
    mediaFingerprints.delete(removed.fingerprint);
    if (removed.url) {
      try { revokeObjectURL(removed.url); } catch (_) { /* best-effort release */ }
    }
    renderMedia(kind);
    callView('setMediaStatus', kind, t(`capture.status.${kind}_removed`), 'cancelled');
    addTimelineEvent('CAPTURE_ATTACHMENT_REMOVED', { filename: removed.name });
    markCaptureChanged();
    return true;
  }

  function updateCaptureDetails({ title, type, notes } = {}) {
    if (typeof title === 'string') captureTitle = Array.from(title).slice(0, 200).join('');
    if (typeof type === 'string' && CAPTURE_TYPES.includes(type)) captureType = type;
    if (typeof notes === 'string') {
      captureNotes = Array.from(notes).slice(0, MAX_CAPTURE_NOTES_CHARS).join('');
    }
    markCaptureChanged();
    return captureDetails();
  }

  function resetCaptureSession() {
    clearAllMedia();
    captureCreatedAt = new Date(now());
    captureTitle = defaultCallsDocumentTitle(captureCreatedAt);
    captureType = CAPTURE_TYPES[0];
    captureNotes = '';
    timelineSequence += 1;
    timeline = [{
      id: `capture-event-${timelineSequence}`,
      code: 'CAPTURE_STARTED',
      params: {},
      at: captureCreatedAt,
    }];
    callView('renderCaptureDetails', captureDetails());
    callView('renderTimeline', timeline.map((item) => ({ ...item, params: { ...item.params } })));
    callView('setCaptureStatus', t('capture.status.ready'), 'idle');
    syncCaptureSaveAvailability();
  }

  function stopTracks(value = stream) {
    if (!value || typeof value.getTracks !== 'function') return;
    for (const track of value.getTracks()) {
      try { track.stop(); } catch (_) { /* best-effort release */ }
    }
  }

  function cameraStatusKey() {
    if (cameraMode === 'photo') return 'capture.camera.ready_photo';
    if (cameraRecordingState === 'recording') return 'capture.camera.video_recording';
    if (cameraRecordingState === 'processing') return 'capture.camera.video_processing';
    return 'capture.camera.ready_video';
  }

  function renderCameraState() {
    if (!cameraMode) return;
    callView('setCaptureCameraState', cameraMode, cameraRecordingState, {
      canSwitch: cameraSwitchSupported && cameraRecordingState === 'ready',
      status: t(cameraStatusKey()),
    });
  }

  function stopCameraResources({ closeView = true } = {}) {
    cameraGeneration += 1;
    const activeRecorder = cameraRecorder;
    cameraRecorder = null;
    if (activeRecorder && activeRecorder.state !== 'inactive') {
      try { activeRecorder.stop(); } catch (_) { /* best-effort recorder stop */ }
    }
    stopTracks(cameraStream);
    cameraStream = null;
    cameraMode = null;
    cameraRecordingState = 'idle';
    cameraRecordingChunks = [];
    cameraRecordingBytes = 0;
    cameraRecordingOversized = false;
    cameraRecordingMime = null;
    cameraSwitchSupported = false;
    if (closeView) callView('closeCaptureCamera');
  }

  function cameraConstraints(mode) {
    return {
      video: { facingMode: { ideal: cameraFacingMode } },
      audio: mode === 'video',
    };
  }

  async function openCaptureCamera(mode) {
    if (!['photo', 'video'].includes(mode) || cameraMode || active || saveActive || analysisActive
        || ['permission', 'recording', 'processing'].includes(recordingState)) return false;
    const mediaStatusKind = mode;
    if (!isSecureContext) {
      callView('setMediaStatus', mediaStatusKind, t('capture.camera.secure_context'), 'error');
      return false;
    }
    if (!mediaDevices || typeof mediaDevices.getUserMedia !== 'function') {
      callView('setMediaStatus', mediaStatusKind, t('capture.camera.unavailable'), 'error');
      return false;
    }
    if (mode === 'video' && !selectCaptureVideoMimeType(MediaRecorderClass)) {
      callView('setMediaStatus', mediaStatusKind, t('capture.camera.recorder_unsupported'), 'error');
      return false;
    }
    const run = ++cameraGeneration;
    cameraMode = mode;
    cameraRecordingState = 'permission';
    callView('openCaptureCamera', mode, null, { canSwitch: false });
    callView('setCaptureCameraState', mode, 'permission', {
      canSwitch: false,
      status: t(mode === 'video' ? 'capture.camera.permission_wait_av' : 'capture.camera.permission_wait'),
    });
    callView('setMediaStatus', mode, t(mode === 'video'
      ? 'capture.camera.permission_wait_av' : 'capture.camera.permission_wait'), 'loading');
    try {
      const acquired = await mediaDevices.getUserMedia(cameraConstraints(mode));
      if (run !== cameraGeneration || cameraMode !== mode) {
        stopTracks(acquired);
        return false;
      }
      cameraStream = acquired;
      cameraRecordingState = 'ready';
      const canSwitch = Boolean(mediaDevices.getSupportedConstraints
        && mediaDevices.getSupportedConstraints().facingMode);
      cameraSwitchSupported = canSwitch;
      callView('openCaptureCamera', mode, acquired, { canSwitch });
      callView('setCaptureCameraState', mode, 'ready', {
        canSwitch,
        status: t(cameraStatusKey()),
      });
      return true;
    } catch (error) {
      if (run !== cameraGeneration) return false;
      const denied = error && ['NotAllowedError', 'SecurityError'].includes(error.name);
      stopCameraResources();
      callView('setMediaStatus', mode, t(denied
        ? (mode === 'video' ? 'capture.camera.permission_denied_av' : 'capture.camera.permission_denied')
        : 'capture.camera.unavailable'), 'error');
      return false;
    }
  }

  async function switchCaptureCamera() {
    if (!cameraMode || cameraRecordingState !== 'ready' || !cameraStream || !cameraSwitchSupported) return false;
    const mode = cameraMode;
    const oldStream = cameraStream;
    const run = ++cameraGeneration;
    cameraFacingMode = cameraFacingMode === 'environment' ? 'user' : 'environment';
    cameraStream = null;
    stopTracks(oldStream);
    callView('setCaptureCameraState', mode, 'permission', {
      canSwitch: false,
      status: t(mode === 'video' ? 'capture.camera.permission_wait_av' : 'capture.camera.permission_wait'),
    });
    try {
      const acquired = await mediaDevices.getUserMedia(cameraConstraints(mode));
      if (run !== cameraGeneration || cameraMode !== mode) {
        stopTracks(acquired);
        return false;
      }
      cameraStream = acquired;
      cameraRecordingState = 'ready';
      callView('openCaptureCamera', mode, acquired, { canSwitch: true });
      renderCameraState();
      return true;
    } catch (_) {
      stopCameraResources();
      callView('setMediaStatus', mode, t('capture.camera.unavailable'), 'error');
      return false;
    }
  }

  function capturedFilename(kind, mime) {
    const stamp = new Date(now()).toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
    const extension = kind === 'photo' ? 'jpg' : mime === 'video/mp4' ? 'mp4' : 'webm';
    return `Capture-${kind}-${stamp}.${extension}`;
  }

  async function captureCameraPhoto() {
    if (cameraMode !== 'photo' || cameraRecordingState !== 'ready' || !cameraStream) return false;
    const run = cameraGeneration;
    cameraRecordingState = 'processing';
    callView('setCaptureCameraState', 'photo', 'processing', {
      canSwitch: false,
      status: t('capture.camera.video_processing'),
    });
    try {
      const blob = typeof capturePhotoFrame === 'function'
        ? await capturePhotoFrame()
        : view && typeof view.capturePhotoBlob === 'function'
          ? await view.capturePhotoBlob()
          : null;
      if (run !== cameraGeneration) return false;
      if (!blob || blob.type !== 'image/jpeg') throw new Error('invalid camera photo');
      const file = createMediaFile([blob], capturedFilename('photo', 'image/jpeg'), {
        type: 'image/jpeg', lastModified: now(),
      });
      stopCameraResources();
      return addCaptureMedia('photo', file);
    } catch (_) {
      if (run === cameraGeneration) {
        stopCameraResources();
        callView('setMediaStatus', 'photo', t('capture.camera.photo_failed'), 'error');
      }
      return false;
    }
  }

  function finishCameraVideo(run) {
    if (run !== cameraGeneration || cameraMode !== 'video') return false;
    const chunksToSave = cameraRecordingChunks.slice();
    const mimeWithCodecs = cameraRecordingMime;
    const oversized = cameraRecordingOversized || cameraRecordingBytes > MAX_CAPTURE_VIDEO_BYTES;
    stopTracks(cameraStream);
    cameraStream = null;
    cameraRecorder = null;
    if (oversized) {
      stopCameraResources();
      callView('setMediaStatus', 'video', t('capture.status.video_oversize'), 'error');
      return false;
    }
    try {
      const baseMime = mimeWithCodecs.split(';')[0].toLowerCase();
      const blob = new BlobClass(chunksToSave, { type: baseMime });
      if (!blob.size) throw new Error('empty video');
      const file = createMediaFile([blob], capturedFilename('video', baseMime), {
        type: baseMime, lastModified: now(),
      });
      stopCameraResources();
      const added = addCaptureMedia('video', file);
      if (added) callView('setMediaStatus', 'video', t('capture.camera.video_complete'), 'success');
      return added;
    } catch (_) {
      stopCameraResources();
      callView('setMediaStatus', 'video', t('capture.camera.video_failed'), 'error');
      return false;
    }
  }

  function startCaptureVideoRecording() {
    if (cameraMode !== 'video' || cameraRecordingState !== 'ready' || !cameraStream) return false;
    const mimeType = selectCaptureVideoMimeType(MediaRecorderClass);
    if (!mimeType) {
      callView('setMediaStatus', 'video', t('capture.camera.recorder_unsupported'), 'error');
      return false;
    }
    const run = cameraGeneration;
    try {
      cameraRecorder = new MediaRecorderClass(cameraStream, { mimeType });
      cameraRecordingChunks = [];
      cameraRecordingBytes = 0;
      cameraRecordingOversized = false;
      cameraRecordingMime = mimeType;
      cameraRecorder.ondataavailable = (event) => {
        if (run !== cameraGeneration || !event.data || !event.data.size) return;
        cameraRecordingBytes += event.data.size;
        if (cameraRecordingBytes <= MAX_CAPTURE_VIDEO_BYTES) cameraRecordingChunks.push(event.data);
        else {
          cameraRecordingOversized = true;
          if (cameraRecorder && cameraRecorder.state !== 'inactive') {
            try { cameraRecorder.stop(); } catch (_) { /* handled by final validation */ }
          }
        }
      };
      cameraRecorder.onerror = () => {
        if (run !== cameraGeneration) return;
        stopCameraResources();
        callView('setMediaStatus', 'video', t('capture.camera.video_failed'), 'error');
      };
      cameraRecorder.onstop = () => finishCameraVideo(run);
      cameraRecorder.start(1000);
      cameraRecordingState = 'recording';
      renderCameraState();
      return true;
    } catch (_) {
      stopCameraResources();
      callView('setMediaStatus', 'video', t('capture.camera.video_failed'), 'error');
      return false;
    }
  }

  function stopCaptureVideoRecording() {
    if (cameraMode !== 'video' || cameraRecordingState !== 'recording' || !cameraRecorder) return false;
    cameraRecordingState = 'processing';
    renderCameraState();
    try { cameraRecorder.stop(); } catch (_) {
      stopCameraResources();
      callView('setMediaStatus', 'video', t('capture.camera.video_failed'), 'error');
      return false;
    }
    stopTracks(cameraStream);
    return true;
  }

  function closeCaptureCamera({ announce = false } = {}) {
    if (!cameraMode && !cameraStream && !cameraRecorder) return false;
    const mode = cameraMode;
    stopCameraResources();
    if (announce && mode) callView('setMediaStatus', mode, t('capture.camera.cancelled'), 'cancelled');
    return true;
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

  function clearAnalysisState({ clearView = true } = {}) {
    analysisGeneration += 1;
    if (analysisController) analysisController.abort();
    analysisController = null;
    analysisActive = false;
    analysis = null;
    callView('setAnalysisBusy', false);
    if (clearView) callView('clearAnalysis');
  }

  function abortHistory() {
    historyGeneration += 1;
    if (historyController) historyController.abort();
    historyController = null;
    historyActive = false;
    historyRefreshPending = false;
    callView('setHistoryLoading', false, false);
  }

  async function loadHistory({ refreshing = false } = {}) {
    if (!panelVisible || historyActive) return false;
    historyActive = true;
    const run = ++historyGeneration;
    historyController = createAbortController();
    callView('setHistoryLoading', true, refreshing || historyHasLoaded);
    try {
      const documents = await requestCallsHistory({
        fetchImpl,
        signal: historyController.signal,
      });
      if (run !== historyGeneration || !panelVisible) return false;
      historyHasLoaded = true;
      historyDocuments = documents;
      callView('renderHistory', documents, openHistoryDocument);
      return true;
    } catch (error) {
      if (run !== historyGeneration || !panelVisible) return false;
      const safe = error instanceof CallsUiError
        ? error : new CallsUiError('HISTORY_REQUEST_FAILED', HISTORY_FAILURE);
      if (safe.code !== 'HISTORY_CANCELLED') callView('setHistoryError', safe.message);
      return false;
    } finally {
      if (run === historyGeneration) {
        historyActive = false;
        historyController = null;
        callView('setHistoryLoading', false, false);
        if (historyRefreshPending && panelVisible) {
          historyRefreshPending = false;
          void loadHistory({ refreshing: true });
        }
      }
    }
  }

  async function openHistoryDocument(documentId) {
    if (typeof documentId !== 'string' || !documentId || typeof openDocument !== 'function') return false;
    try {
      await openDocument(documentId);
      return true;
    } catch (_) {
      callView('setHistoryError', t('capture.history.open_failed'));
      return false;
    }
  }

  function selectFile(file) {
    if (active || saveActive || ['permission', 'recording', 'processing'].includes(recordingState)) return false;
    const validation = validateCallsFile(file);
    clearAnalysisState();
    result = null;
    clearSaveState();
    view.clearResult();
    syncResolvedAnalysisLanguage();
    discardRecordedSelection();
    setRecordingState('idle');
    if (!validation.ok) {
      selectedFile = null;
      selectedSource = null;
      view.showSelected(null);
      view.setStatus(validation.message, 'error');
      view.setReady(false, 'upload');
      syncCaptureSaveAvailability();
      return false;
    }
    selectedFile = file;
    selectedSource = 'upload';
    view.showSelected(sanitizeCallsFilename(file.name), formatEncodedSize(file.size));
    view.setStatus(t('capture.status.file_ready'), 'selected');
    view.setReady(true, 'upload');
    syncCaptureSaveAvailability();
    return true;
  }

  async function startRecording() {
    if (active || saveActive || ['permission', 'recording', 'processing'].includes(recordingState)) return false;
    if (!isSecureContext) {
      callView('setRecordingStatus', t('capture.status.microphone_secure'), 'error');
      return false;
    }
    if (!mediaDevices || typeof mediaDevices.getUserMedia !== 'function'
        || typeof MediaRecorderClass !== 'function' || typeof BlobClass !== 'function') {
      callView('setRecordingStatus', t('capture.status.microphone_unavailable'), 'error');
      return false;
    }

    clearAnalysisState();
    const run = ++recordingGeneration;
    discardCapture();
    discardRecordedSelection();
    selectedFile = null;
    selectedSource = null;
    result = null;
    clearSaveState();
    callView('clearUpload');
    callView('clearResult');
    syncResolvedAnalysisLanguage();
    callView('setReady', false, 'upload');
    syncCaptureSaveAvailability();
    setRecordingState('permission');
    callView('setRecordingStatus', t('capture.status.permission_wait'), 'loading');

    let nextStream;
    try {
      nextStream = await mediaDevices.getUserMedia({ audio: true, video: false });
    } catch (error) {
      if (run !== recordingGeneration) return false;
      setRecordingState('idle');
      const name = error && error.name;
      if (name === 'NotAllowedError' || name === 'SecurityError') {
        callView('setRecordingStatus', t('capture.status.permission_denied'), 'error');
      } else if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
        callView('setRecordingStatus', t('capture.status.no_microphone'), 'error');
      } else {
        callView('setRecordingStatus', t('capture.status.microphone_failed'), 'error');
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
      callView('setRecordingStatus', t('capture.status.no_microphone'), 'error');
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
      failCapture(new CallsUiError('AUDIO_UNSUPPORTED', 'capture.status.microphone_unavailable'));
      return false;
    }

    recordingStartedAt = now();
    callView('setRecordingElapsed', 0);
    setRecordingState('recording');
    callView('setRecordingStatus', t('capture.status.recording'), 'recording');
    addTimelineEvent('CAPTURE_RECORDING_STARTED');
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
    callView('setRecordingStatus', t('capture.status.preparing_wav'), 'loading');

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
        throw new CallsUiError('RECORDING_EMPTY', 'capture.error.recording_empty');
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
      callView('setRecordingStatus', t('capture.status.recording_ready'), 'success');
      addTimelineEvent('CAPTURE_RECORDING_STOPPED');
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
    clearAnalysisState();
    clearSaveState();
    callView('clearResult');
    syncResolvedAnalysisLanguage();
    setRecordingState('idle');
    if (announce) {
      callView('setRecordingStatus', t('capture.status.recording_cancelled'), 'cancelled');
      addTimelineEvent('CAPTURE_RECORDING_CANCELLED');
    }
    syncCaptureSaveAvailability();
    return true;
  }

  async function submit() {
    if (active || saveActive || ['permission', 'recording', 'processing'].includes(recordingState)) return false;
    const validation = validateCallsFile(selectedFile);
    if (!validation.ok) {
      setTranscriptionStatus(validation.message, 'error');
      return false;
    }
    clearAnalysisState();
    active = true;
    clearSaveState();
    syncCaptureSaveAvailability();
    const run = ++generation;
    controller = createAbortController();
    view.setBusy(true);
    setTranscriptionStatus(t('capture.status.transcribing'), 'loading');
    addTimelineEvent('CAPTURE_TRANSCRIPTION_STARTED');
    try {
      const next = await requestCallsTranscription(selectedFile, {
        sourceLanguage: transcriptionLanguage,
        fetchImpl,
        signal: controller.signal,
      });
      if (run !== generation) return false;
      result = next;
      resultCreatedAt = new Date(now());
      syncResolvedAnalysisLanguage();
      view.renderResult(next);
      callView('showSave', captureTitle, true);
      callView('showAnalysisReady', next.transcript_text.length > 0);
      setTranscriptionStatus(
        t(next.segments.length ? 'capture.status.transcription_complete' : 'capture.status.transcription_silence'),
        'success',
      );
      addTimelineEvent('CAPTURE_TRANSCRIPTION_COMPLETED');
      markCaptureChanged();
      return true;
    } catch (error) {
      if (run !== generation) return false;
      const safe = error instanceof CallsUiError ? error : new CallsUiError('REQUEST_FAILED', GENERIC_FAILURE);
      setTranscriptionStatus(safe.message, safe.code === 'CANCELLED' ? 'cancelled' : 'error');
      if (safe.code !== 'CANCELLED') addTimelineEvent('CAPTURE_TRANSCRIPTION_FAILED');
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
    if (saveActive || saved || active || analysisActive
        || ['permission', 'recording', 'processing'].includes(recordingState)) return false;
    const normalizedTitle = typeof title === 'string' ? title.trim() : '';
    if (!normalizedTitle) {
      callView('setSaveStatus', t('capture.status.title_required'), 'error');
      return false;
    }
    if (!hasSavableCapture()) {
      callView('setSaveStatus', t('capture.status.content_required'), 'error');
      return false;
    }
    captureTitle = normalizedTitle;
    saveActive = true;
    const run = ++saveGeneration;
    saveController = createAbortController();
    callView('setSaveBusy', true);
    callView('setSaveStatus', t('capture.status.saving'), 'loading');
    try {
      await requestCallsDocumentSave(
        {
          title: normalizedTitle,
          result,
          createdAt: captureCreatedAt,
          captureType,
          notes: captureNotes,
          photos,
          videos,
        },
        { fetchImpl, signal: saveController.signal },
      );
      if (run !== saveGeneration) return false;
      saved = true;
      callView('setSaved', true);
      callView('setSaveStatus', t('capture.status.saved'), 'success');
      addTimelineEvent('CAPTURE_SAVED_TO_LIBRARY');
      if (panelVisible) {
        if (historyActive) historyRefreshPending = true;
        else void loadHistory({ refreshing: true });
      }
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

  async function generateAnalysis() {
    if (!result || analysisActive || active || saveActive
        || ['permission', 'recording', 'processing'].includes(recordingState)) return false;
    const transcript = result.transcript_text;
    if (typeof transcript !== 'string' || !transcript.trim()) {
      callView('setAnalysisStatus', t('capture.status.analysis_empty'), 'error');
      return false;
    }
    analysisActive = true;
    analysis = null;
    syncResolvedAnalysisLanguage();
    const run = ++analysisGeneration;
    analysisController = createAbortController();
    callView('beginAnalysisAttempt');
    callView('setAnalysisBusy', true);
    const cjkCount = (transcript.match(/[\u3400-\u9fff]/gu) || []).length;
    const estimatedBlockSize = cjkCount * 2 >= transcript.length ? 1800 : 9000;
    const estimatedBlocks = Math.max(1, Math.min(512, Math.ceil(transcript.length / estimatedBlockSize)));
    let progressBlock = 1;
    const renderProgress = () => callView(
      'setAnalysisStatus',
      estimatedBlocks > 1
        ? t('capture.status.analysis_progress', { current: progressBlock, total: estimatedBlocks })
        : t('capture.status.analysis_generating'),
      'loading',
    );
    renderProgress();
    const progressTimer = estimatedBlocks > 1 ? setIntervalFn(() => {
      progressBlock = Math.min(estimatedBlocks, progressBlock + 1);
      renderProgress();
    }, 1500) : null;
    try {
      const next = await requestCallsAnalysis(transcript, {
        outputLanguage: analysisLanguage,
        transcriptLanguage: result.language,
        fetchImpl,
        signal: analysisController.signal,
      });
      if (run !== analysisGeneration) return false;
      analysis = next;
      callView('renderAnalysis', next);
      callView('setAnalysisStatus', t('capture.status.analysis_ready'), 'success');
      addTimelineEvent('CAPTURE_ANALYSIS_GENERATED');
      return true;
    } catch (error) {
      if (run !== analysisGeneration) return false;
      const safe = error instanceof CallsUiError
        ? error : new CallsUiError('ANALYSIS_REQUEST_FAILED', ANALYSIS_FAILURE);
      if (safe.code !== 'ANALYSIS_CANCELLED') {
        callView('setAnalysisStatus', safe.message, 'error');
        addTimelineEvent('CAPTURE_ANALYSIS_FAILED');
      }
      return false;
    } finally {
      if (progressTimer !== null) clearIntervalFn(progressTimer);
      if (run === analysisGeneration) {
        analysisActive = false;
        analysisController = null;
        callView('setAnalysisBusy', false);
      }
    }
  }

  async function copyAnalysis() {
    if (!analysis || analysisActive || typeof copyText !== 'function') return false;
    try {
      await copyText(formatCallsAnalysisText(analysis));
      callView('setAnalysisStatus', t('capture.status.analysis_copied'), 'success');
      return true;
    } catch (_) {
      callView('setAnalysisStatus', t('capture.status.analysis_copy_failed'), 'error');
      return false;
    }
  }

  function setAnalysisLanguage(value) {
    if (!['auto', 'es', 'en', 'zh-Hans', 'zh-Hant'].includes(value)) return false;
    if (analysisActive) clearAnalysisState();
    analysisLanguage = value;
    analysis = null;
    callView('clearAnalysis');
    syncResolvedAnalysisLanguage();
    callView('setAnalysisLanguage', analysisLanguage);
    callView('showAnalysisReady', Boolean(result && result.transcript_text));
    return true;
  }

  function setTranscriptionLanguage(value) {
    if (!['auto', 'es', 'en', 'zh'].includes(value) || active) return false;
    transcriptionLanguage = value;
    callView('setTranscriptionLanguage', value);
    return true;
  }

  function onLocaleChanged() {
    callView('renderCaptureDetails', captureDetails());
    callView('renderTimeline', timeline.map((item) => ({ ...item, params: { ...item.params } })));
    renderMedia('photo');
    renderMedia('video');
    callView(
      'setCaptureStatus',
      t(hasSavableCapture() ? 'capture.status.changed' : 'capture.status.ready'),
      'idle',
    );
    callView(
      'setMediaStatus',
      'photo',
      t(photos.length ? 'capture.status.photo_added' : 'capture.photos.empty'),
      photos.length ? 'success' : 'idle',
    );
    callView(
      'setMediaStatus',
      'video',
      t(videos.length ? 'capture.status.video_added' : 'capture.videos.empty'),
      videos.length ? 'success' : 'idle',
    );
    if (active) setTranscriptionStatus(t('capture.status.transcribing'), 'loading');
    else if (result) {
      setTranscriptionStatus(
        t(result.segments.length ? 'capture.status.transcription_complete' : 'capture.status.transcription_silence'),
        'success',
      );
    } else if (selectedFile) setTranscriptionStatus(t('capture.status.file_ready'), 'selected');
    else setTranscriptionStatus(t('capture.transcription.initial'), 'idle');
    const recordingStatusKeys = {
      permission: 'capture.status.permission_wait',
      recording: 'capture.status.recording',
      processing: 'capture.status.preparing_wav',
      ready: 'capture.status.recording_ready',
    };
    callView(
      'setRecordingStatus',
      t(recordingStatusKeys[recordingState] || 'capture.voice.ready_initial'),
      ['permission', 'processing'].includes(recordingState)
        ? 'loading' : recordingState === 'recording' ? 'recording' : recordingState === 'ready' ? 'success' : 'idle',
    );
    if (historyHasLoaded) callView('renderHistory', historyDocuments, openHistoryDocument);
    if (result) callView('renderResult', result);
    if (analysis) {
      callView('renderAnalysis', analysis);
      callView('setAnalysisStatus', t('capture.status.analysis_ready'), 'success');
    } else if (analysisActive) {
      callView('setAnalysisStatus', t('capture.status.analysis_generating'), 'loading');
    }
    if (saved) callView('setSaveStatus', t('capture.status.saved'), 'success');
    else if (saveActive) callView('setSaveStatus', t('capture.status.saving'), 'loading');
    else callView('setSaveStatus', t('capture.save.initial'), 'idle');
    syncResolvedAnalysisLanguage();
    callView('setAnalysisLanguage', analysisLanguage);
    renderCameraState();
    return true;
  }

  function reset() {
    generation += 1;
    if (controller) controller.abort();
    controller = null;
    active = false;
    clearAnalysisState();
    clearSaveState();
    recordingGeneration += 1;
    closeCaptureCamera();
    discardCapture();
    discardRecordedSelection();
    selectedFile = null;
    selectedSource = null;
    result = null;
    setRecordingState('idle');
    view.reset();
    syncResolvedAnalysisLanguage();
    resetCaptureSession();
    return true;
  }

  function onPanelHidden() {
    panelVisible = false;
    closeCaptureCamera();
    if (historyActive) abortHistory();
    if (active) cancel();
    if (analysisActive) {
      clearAnalysisState();
      callView('showAnalysisReady', Boolean(result && result.transcript_text));
    }
    if (saveActive) {
      clearSaveState({ clearView: false });
      callView('setSaveStatus', t('capture.status.save_cancelled'), 'cancelled');
    }
    if (cancelRecording({ announce: false })) {
      callView('setRecordingStatus', t('capture.status.recording_cancelled'), 'cancelled');
    }
    if (selectedSource === 'recording') {
      discardRecordedSelection();
      selectedFile = null;
      selectedSource = null;
      setRecordingState('idle');
    } else if (selectedSource === 'upload') {
      selectedFile = null;
      selectedSource = null;
      callView('clearUpload');
      callView('setReady', false, 'upload');
    }
    clearAllMedia();
    return true;
  }

  function onPanelOpened() {
    panelVisible = true;
    callView('renderCaptureDetails', captureDetails());
    callView('renderTimeline', timeline.slice());
    renderMedia('photo');
    renderMedia('video');
    callView('showSave', captureTitle, hasSavableCapture());
    return loadHistory({ refreshing: historyHasLoaded });
  }

  function destroy() {
    generation += 1;
    if (controller) controller.abort();
    controller = null;
    active = false;
    clearAnalysisState({ clearView: false });
    clearSaveState({ clearView: false });
    panelVisible = false;
    abortHistory();
    recordingGeneration += 1;
    closeCaptureCamera();
    discardCapture();
    revokePreview();
    clearAllMedia();
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
      view.setStatus(t('capture.status.transcript_copied'), 'success');
      return true;
    } catch (_) {
      view.setStatus(t('capture.status.transcript_copy_failed'), 'error');
      return false;
    }
  }

    return {
    selectFile,
    submit,
    cancel,
    reset,
    copyTranscript,
    generateAnalysis,
    setAnalysisLanguage,
    setTranscriptionLanguage,
    onLocaleChanged,
    copyAnalysis,
    addCaptureMedia,
    updateMediaCaption,
    removeCaptureMedia,
    openCaptureCamera,
    switchCaptureCamera,
    captureCameraPhoto,
    startCaptureVideoRecording,
    stopCaptureVideoRecording,
    closeCaptureCamera,
    updateCaptureDetails,
    saveToLibrary,
    loadHistory,
    openHistoryDocument,
    startRecording,
    stopRecording,
    cancelRecording,
    onPanelOpened,
    onPanelHidden,
    destroy,
    isActive: () => active,
    isSaveActive: () => saveActive,
    isHistoryActive: () => historyActive,
    isAnalysisActive: () => analysisActive,
    isCameraOpen: () => Boolean(cameraMode),
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
  const transcriptLanguage = byId('calls-transcript-language');
  const transcriptionLanguageInput = byId('calls-transcription-language');
  const empty = byId('calls-empty-result');
  const segmentsWrap = byId('calls-segments-wrap');
  const segmentsList = byId('calls-segments');
  const copy = byId('calls-copy-btn');
  const analysisSection = byId('calls-analysis');
  const analysisGenerate = byId('calls-analysis-generate-btn');
  const analysisRegenerate = byId('calls-analysis-regenerate-btn');
  const analysisCopy = byId('calls-analysis-copy-btn');
  const analysisStatus = byId('calls-analysis-status');
  const analysisResult = byId('calls-analysis-result');
  const analysisSummary = byId('calls-analysis-summary');
  const analysisDecisions = byId('calls-analysis-decisions');
  const analysisActions = byId('calls-analysis-actions');
  const analysisQuestions = byId('calls-analysis-questions');
  const analysisLanguageInput = byId('calls-analysis-language');
  const analysisLanguageResolved = byId('calls-analysis-language-resolved');
  const saveSection = byId('calls-save');
  const saveTitle = byId('calls-save-title');
  const saveButton = byId('calls-save-btn');
  const saveStatus = byId('calls-save-status');
  const captureTypeInput = byId('calls-capture-type');
  const captureNotesInput = byId('calls-capture-notes');
  const captureCreated = byId('calls-capture-created');
  const captureStatus = byId('calls-capture-status');
  const photoInput = byId('calls-photo-input');
  const photoCameraButton = byId('calls-photo-camera-btn');
  const photoStatus = byId('calls-photo-status');
  const photoList = byId('calls-photo-list');
  const videoInput = byId('calls-video-input');
  const videoCameraButton = byId('calls-video-camera-btn');
  const videoStatus = byId('calls-video-status');
  const videoList = byId('calls-video-list');
  const cameraPanel = byId('calls-camera-panel');
  const cameraPreview = byId('calls-camera-preview');
  const cameraStatus = byId('calls-camera-status');
  const cameraPhotoCapture = byId('calls-camera-photo-capture-btn');
  const cameraVideoStart = byId('calls-camera-video-start-btn');
  const cameraVideoStop = byId('calls-camera-video-stop-btn');
  const cameraSwitch = byId('calls-camera-switch-btn');
  const timelineList = byId('calls-timeline-list');
  const historyRefresh = byId('calls-history-refresh-btn');
  const historyStatus = byId('calls-history-status');
  const historyEmpty = byId('calls-history-empty');
  const historyList = byId('calls-history-list');
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
  let analysisBusy = false;
  let analysisReady = false;
  let analysisAttempted = false;
  let analysisComplete = false;
  let cameraUiOpen = false;
  let cameraUiMode = null;
  let cameraUiState = 'idle';
  let cameraCanSwitch = false;

  const captureBusy = () => ['permission', 'recording', 'processing'].includes(recordingUiState);
  const syncControls = () => {
    const operationBusy = transcriptionBusy || saveBusy || analysisBusy || captureBusy() || cameraUiOpen;
    fileInput.disabled = operationBusy;
    photoInput.disabled = operationBusy;
    photoCameraButton.disabled = operationBusy;
    videoInput.disabled = operationBusy;
    videoCameraButton.disabled = operationBusy;
    captureTypeInput.disabled = operationBusy;
    captureNotesInput.disabled = operationBusy;
    submit.disabled = operationBusy || !uploadReady;
    reset.disabled = false;
    recordStart.disabled = operationBusy;
    recordStop.disabled = recordingUiState !== 'recording';
    recordCancel.hidden = !captureBusy();
    recordSubmit.disabled = operationBusy || !recordingReady;
    recordClear.disabled = false;
    saveTitle.disabled = saveBusy || saveComplete;
    saveButton.disabled = operationBusy || !saveReady || saveComplete;
    analysisGenerate.disabled = operationBusy || !analysisReady || analysisAttempted;
    analysisRegenerate.disabled = operationBusy || !analysisReady;
    analysisCopy.disabled = operationBusy || !analysisComplete;
    analysisLanguageInput.disabled = transcriptionBusy || saveBusy || captureBusy();
    transcriptionLanguageInput.disabled = operationBusy;
    cameraPhotoCapture.hidden = cameraUiMode !== 'photo';
    cameraPhotoCapture.disabled = cameraUiState !== 'ready';
    cameraVideoStart.hidden = cameraUiMode !== 'video' || cameraUiState !== 'ready';
    cameraVideoStart.disabled = cameraUiState !== 'ready';
    cameraVideoStop.hidden = cameraUiMode !== 'video' || cameraUiState !== 'recording';
    cameraVideoStop.disabled = cameraUiState !== 'recording';
    cameraSwitch.hidden = !cameraCanSwitch || cameraUiState !== 'ready';
    cameraSwitch.disabled = cameraUiState !== 'ready';
  };

  const clearAnalysis = () => {
    analysisBusy = false;
    analysisReady = false;
    analysisAttempted = false;
    analysisComplete = false;
    analysisSection.hidden = true;
    analysisResult.hidden = true;
    analysisGenerate.hidden = false;
    analysisGenerate.textContent = t('capture.analysis.generate');
    analysisRegenerate.hidden = true;
    analysisRegenerate.textContent = t('capture.analysis.regenerate');
    analysisCopy.hidden = true;
    setElementText(analysisStatus, '');
    analysisStatus.dataset.state = 'idle';
    setElementText(analysisSummary, '');
    analysisDecisions.replaceChildren();
    analysisActions.replaceChildren();
    analysisQuestions.replaceChildren();
    syncControls();
  };

  const clearSave = () => {
    saveBusy = false;
    saveReady = false;
    saveComplete = false;
    saveSection.hidden = false;
    saveButton.textContent = t('capture.save.action');
    setElementText(saveStatus, t('capture.save.initial'));
    saveStatus.dataset.state = 'idle';
    syncControls();
  };

  const clearResult = () => {
    resultSection.hidden = true;
    setElementText(transcript, '');
    setElementText(summary, '');
    setElementText(transcriptLanguage, '');
    segmentsList.replaceChildren();
    segmentsWrap.hidden = true;
    empty.hidden = true;
    clearAnalysis();
    clearSave();
  };

  return {
    clearResult,
    renderCaptureDetails(value) {
      saveTitle.value = value.title;
      captureTypeInput.value = value.type;
      captureNotesInput.value = value.notes;
      setElementText(captureCreated, t('capture.details.created', { value: formatCallsLocalDateTime(value.createdAt) }));
    },
    setCaptureStatus(message, kind) {
      setElementText(captureStatus, message);
      captureStatus.dataset.state = kind;
      captureStatus.setAttribute('role', kind === 'error' ? 'alert' : 'status');
      captureStatus.setAttribute('aria-live', kind === 'error' ? 'assertive' : 'polite');
    },
    renderCaptureMedia(kind, items, onCaption, onRemove) {
      const list = kind === 'photo' ? photoList : videoList;
      if (items.length === 0) {
        if (kind === 'photo') photoInput.value = '';
        else videoInput.value = '';
      }
      list.replaceChildren();
      for (const [index, media] of items.entries()) {
        const item = doc.createElement('li');
        const previewWrap = doc.createElement(kind === 'photo' ? 'button' : 'div');
        const preview = doc.createElement(kind === 'photo' ? 'img' : 'video');
        const details = doc.createElement('div');
        const name = doc.createElement('strong');
        const meta = doc.createElement('span');
        const persistence = doc.createElement('span');
        const captionLabel = doc.createElement('label');
        const caption = doc.createElement('input');
        const remove = doc.createElement('button');
        item.className = 'calls-media-item';
        previewWrap.className = 'calls-media-preview-wrap';
        preview.className = 'calls-media-preview';
        preview.src = media.url;
        if (kind === 'photo') {
          previewWrap.type = 'button';
          previewWrap.setAttribute('aria-expanded', 'false');
          previewWrap.setAttribute('aria-label', t('capture.photo.preview_large_aria', { filename: media.name }));
          preview.alt = media.caption || t('capture.photo.preview_aria', { position: index + 1, filename: media.name });
          previewWrap.addEventListener('click', () => {
            const expanded = item.classList.toggle('calls-media-item-expanded');
            previewWrap.setAttribute('aria-expanded', String(expanded));
          });
        } else {
          preview.controls = true;
          preview.preload = 'metadata';
          preview.setAttribute('aria-label', t('capture.video.preview_aria', { position: index + 1, filename: media.name }));
        }
        previewWrap.appendChild(preview);
        details.className = 'calls-media-details';
        setElementText(name, media.name);
        setElementText(
          meta,
          `${t(`capture.media.${kind}`)} · ${formatEncodedSize(media.size)} · ${t('capture.media.added', { value: formatCallsLocalDateTime(media.addedAt) })}`,
        );
        persistence.className = 'calls-media-persistence';
        setElementText(persistence, t('capture.media.memory_only'));
        captionLabel.className = 'calls-field';
        captionLabel.setAttribute('for', `${media.id}-caption`);
        const captionText = doc.createElement('span');
        setElementText(captionText, t('capture.media.caption'));
        caption.id = `${media.id}-caption`;
        caption.type = 'text';
        caption.maxLength = MAX_CAPTURE_CAPTION_CHARS;
        caption.value = media.caption;
        caption.autocomplete = 'off';
        caption.addEventListener('change', () => onCaption(kind, media.id, caption.value));
        captionLabel.append(captionText, caption);
        remove.type = 'button';
        remove.className = 'calls-button calls-media-remove';
        setElementText(remove, t('common.remove'));
        remove.setAttribute('aria-label', t('capture.media.remove_aria', { filename: media.name }));
        remove.addEventListener('click', () => onRemove(kind, media.id));
        details.append(name, meta, persistence, captionLabel, remove);
        item.append(previewWrap, details);
        list.appendChild(item);
      }
    },
    openCaptureCamera(mode, stream, { canSwitch = false } = {}) {
      cameraUiOpen = true;
      cameraUiMode = mode;
      cameraUiState = 'ready';
      cameraCanSwitch = canSwitch;
      cameraPanel.hidden = false;
      cameraPreview.srcObject = stream || null;
      if (stream) {
        const playResult = cameraPreview.play();
        if (playResult && typeof playResult.catch === 'function') playResult.catch(() => {});
      }
      syncControls();
    },
    setCaptureCameraState(mode, state, { canSwitch = false, status: message = '' } = {}) {
      cameraUiMode = mode;
      cameraUiState = state;
      cameraCanSwitch = canSwitch;
      setElementText(cameraStatus, message);
      cameraStatus.dataset.state = state === 'recording' ? 'recording' : state === 'processing' ? 'loading' : 'idle';
      syncControls();
    },
    closeCaptureCamera() {
      cameraUiOpen = false;
      cameraUiMode = null;
      cameraUiState = 'idle';
      cameraCanSwitch = false;
      try { cameraPreview.pause(); } catch (_) { /* best-effort preview cleanup */ }
      cameraPreview.srcObject = null;
      cameraPanel.hidden = true;
      setElementText(cameraStatus, '');
      syncControls();
    },
    capturePhotoBlob() {
      return capturePhotoBlobFromVideo(cameraPreview, doc);
    },
    setMediaStatus(kind, message, state) {
      const element = kind === 'photo' ? photoStatus : videoStatus;
      setElementText(element, message);
      element.dataset.state = state;
      element.setAttribute('role', state === 'error' ? 'alert' : 'status');
      element.setAttribute('aria-live', state === 'error' ? 'assertive' : 'polite');
    },
    renderTimeline(events) {
      timelineList.replaceChildren();
      for (const event of events) {
        const item = doc.createElement('li');
        const time = doc.createElement('time');
        const message = doc.createElement('span');
        time.dateTime = event.at.toISOString();
        setElementText(time, formatDateTime(event.at, { year: undefined, month: undefined, day: undefined }));
        setElementText(message, t(TIMELINE_I18N_KEYS[event.code] || 'capture.timeline.started', event.params || {}));
        item.append(time, message);
        timelineList.appendChild(item);
      }
    },
    showSelected(name, size) {
      selected.hidden = !name;
      setElementText(selected, name ? `${name} · ${size}` : '');
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
      submit.textContent = busy ? t('capture.status.transcribing') : t('capture.transcribe');
      recordSubmit.textContent = busy ? t('capture.status.transcribing') : t('capture.voice.transcribe_recording');
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
      setElementText(summary, t('capture.transcript.summary', {
        duration: formatCallsTimestamp(value.duration_ms), count: formatNumber(value.segments.length),
      }));
      const confidence = value.language !== 'und' && Number.isFinite(value.language_confidence)
        ? formatNumber(value.language_confidence * 100, { maximumFractionDigits: 0 })
        : null;
      setElementText(transcriptLanguage, t(
        confidence === null
          ? 'capture.transcript.detected_language'
          : 'capture.transcript.detected_language_confidence',
        { language: languageLabel(value.language), confidence },
      ));
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
    showAnalysisReady(enabled) {
      analysisReady = Boolean(enabled);
      analysisAttempted = false;
      analysisComplete = false;
      analysisSection.hidden = false;
      analysisResult.hidden = true;
      analysisGenerate.hidden = false;
      analysisRegenerate.hidden = true;
      analysisCopy.hidden = true;
      setElementText(
        analysisStatus,
        enabled
          ? t('capture.analysis.boundary')
          : t('capture.status.analysis_empty'),
      );
      analysisStatus.dataset.state = enabled ? 'idle' : 'error';
      syncControls();
    },
    clearAnalysis,
    beginAnalysisAttempt() {
      const previousAttempt = analysisAttempted;
      analysisAttempted = true;
      analysisComplete = false;
      analysisResult.hidden = true;
      analysisGenerate.hidden = previousAttempt;
      analysisRegenerate.hidden = !previousAttempt;
      analysisCopy.hidden = true;
      setElementText(analysisSummary, '');
      analysisDecisions.replaceChildren();
      analysisActions.replaceChildren();
      analysisQuestions.replaceChildren();
      syncControls();
    },
    setAnalysisBusy(busy) {
      analysisBusy = busy;
      if (!busy && analysisAttempted) {
        analysisGenerate.hidden = true;
        analysisRegenerate.hidden = false;
      }
      analysisGenerate.textContent = busy ? t('capture.status.analysis_generating') : t('capture.analysis.generate');
      analysisRegenerate.textContent = busy ? t('capture.status.analysis_generating') : t('capture.analysis.regenerate');
      syncControls();
    },
    setAnalysisStatus(message, kind) {
      setElementText(analysisStatus, message);
      analysisStatus.dataset.state = kind;
      analysisStatus.setAttribute('role', kind === 'error' ? 'alert' : 'status');
      analysisStatus.setAttribute('aria-live', kind === 'error' ? 'assertive' : 'polite');
    },
    renderAnalysis(value) {
      const appendList = (element, values) => {
        const items = values.length ? values : [t('common.none_identified')];
        for (const itemValue of items) {
          const item = doc.createElement('li');
          setElementText(item, itemValue);
          element.appendChild(item);
        }
      };
      setElementText(analysisSummary, value.summary);
      analysisDecisions.replaceChildren();
      analysisActions.replaceChildren();
      analysisQuestions.replaceChildren();
      appendList(analysisDecisions, value.decisions);
      appendList(analysisQuestions, value.open_questions);
      if (value.action_items.length === 0) {
        const item = doc.createElement('li');
        setElementText(item, t('common.none_identified'));
        analysisActions.appendChild(item);
      } else {
        for (const action of value.action_items) {
          const item = doc.createElement('li');
          const task = doc.createElement('p');
          const owner = doc.createElement('p');
          const dueDate = doc.createElement('p');
          setElementText(task, t('capture.analysis.task', { value: action.task }));
          setElementText(owner, t('capture.analysis.owner', {
            value: action.owner === null ? t('capture.analysis.not_stated') : action.owner,
          }));
          setElementText(
            dueDate,
            t('capture.analysis.due_date', {
              value: action.due_date === null ? t('capture.analysis.not_stated') : action.due_date,
            }),
          );
          item.append(task, owner, dueDate);
          analysisActions.appendChild(item);
        }
      }
      analysisComplete = true;
      analysisResult.hidden = false;
      analysisCopy.hidden = false;
      syncControls();
    },
    setAnalysisLanguage(value) {
      analysisLanguageInput.value = value;
    },
    setTranscriptionLanguage(value) {
      transcriptionLanguageInput.value = value;
    },
    setResolvedAnalysisLanguage(value) {
      setElementText(analysisLanguageResolved, t('capture.analysis.resolved_language', {
        language: languageLabel(value),
      }));
    },
    showSave(title, ready = true) {
      saveReady = Boolean(ready);
      saveComplete = false;
      saveSection.hidden = false;
      saveTitle.disabled = false;
      if (typeof title === 'string' && title) saveTitle.value = title;
      setElementText(
        saveStatus,
        saveReady
          ? t('capture.status.changed')
          : t('capture.save.initial'),
      );
      saveStatus.dataset.state = 'idle';
      syncControls();
    },
    clearSave,
    setCaptureSaveReady(value) {
      saveReady = Boolean(value);
      if (!saveBusy && !saveComplete) {
        setElementText(
          saveStatus,
          saveReady
            ? t('capture.status.changed')
            : t('capture.save.initial'),
        );
        saveStatus.dataset.state = 'idle';
      }
      syncControls();
    },
    setSaveBusy(busy) {
      saveBusy = busy;
      saveButton.textContent = busy ? t('capture.status.saving') : t('capture.save.action');
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
    setHistoryLoading(busy, refreshing) {
      historyRefresh.disabled = busy;
      historyRefresh.textContent = busy
        ? t(refreshing ? 'capture.history.refreshing' : 'capture.history.loading')
        : t('capture.history.refresh');
      if (busy) {
        setElementText(historyStatus, t(refreshing ? 'capture.history.refreshing' : 'capture.history.loading'));
        historyStatus.dataset.state = 'loading';
        historyStatus.setAttribute('role', 'status');
        historyStatus.setAttribute('aria-live', 'polite');
      }
    },
    renderHistory(documents, onOpen) {
      historyList.replaceChildren();
      for (const documentValue of documents) {
        const item = doc.createElement('li');
        const details = doc.createElement('div');
        const title = doc.createElement('strong');
        const meta = doc.createElement('span');
        const open = doc.createElement('button');
        item.className = 'calls-history-item';
        details.className = 'calls-history-details';
        title.className = 'calls-history-title';
        meta.className = 'calls-history-meta';
        open.type = 'button';
        open.className = 'calls-button calls-history-open';
        setElementText(title, documentValue.title);
        setElementText(
          meta,
          `MarketMatch Capture · ${formatCallsHistoryDate(documentValue.updated_at || documentValue.created_at)}`,
        );
        setElementText(open, t('common.open'));
        open.setAttribute('aria-label', t('capture.history.open_aria', { title: documentValue.title }));
        open.addEventListener('click', () => onOpen(documentValue.id));
        details.append(title, meta);
        item.append(details, open);
        historyList.appendChild(item);
      }
      const empty = documents.length === 0;
      historyList.hidden = empty;
      historyEmpty.hidden = !empty;
      setElementText(
        historyStatus,
        empty ? t('capture.history.empty') : t('capture.history.loaded', { count: formatNumber(documents.length) }),
      );
      historyStatus.dataset.state = 'success';
      historyStatus.setAttribute('role', 'status');
      historyStatus.setAttribute('aria-live', 'polite');
    },
    setHistoryError(message) {
      setElementText(historyStatus, message);
      historyStatus.dataset.state = 'error';
      historyStatus.setAttribute('role', 'alert');
      historyStatus.setAttribute('aria-live', 'assertive');
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
        `${name} · ${size} · ${formatCallsTimestamp(durationMs)}`,
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
      submit.textContent = t('capture.transcribe');
      recordSubmit.textContent = t('capture.voice.transcribe_recording');
      cancel.hidden = true;
      recordIndicator.hidden = true;
      clearResult();
      this.setStatus(t('capture.transcription.initial'), 'idle');
      this.setRecordingStatus(t('capture.voice.ready_initial'), 'idle');
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

export function init(doc = globalThis.document, { openDocument } = {}) {
  if (!doc) return null;
  const modal = doc.getElementById('calls-modal');
  const openButton = doc.getElementById('tool-calls-btn');
  if (!modal || !openButton || modal.dataset.callsWired === 'true') return null;
  modal.dataset.callsWired = 'true';
  const view = _domView(doc);
  let closePanel = () => {};
  const controller = createCallsController({
    view,
    copyText: _copyText,
    openDocument: async (documentId) => {
      closePanel();
      if (typeof openDocument === 'function') await openDocument(documentId);
    },
  });
  const fileInput = doc.getElementById('calls-file-input');
  const recordStart = doc.getElementById('calls-record-start-btn');
  const saveTitle = doc.getElementById('calls-save-title');
  const captureType = doc.getElementById('calls-capture-type');
  const captureNotes = doc.getElementById('calls-capture-notes');
  const transcriptionLanguage = doc.getElementById('calls-transcription-language');
  const analysisLanguage = doc.getElementById('calls-analysis-language');
  const photoInput = doc.getElementById('calls-photo-input');
  const photoCameraButton = doc.getElementById('calls-photo-camera-btn');
  const videoInput = doc.getElementById('calls-video-input');
  const videoCameraButton = doc.getElementById('calls-video-camera-btn');
  const show = () => {
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
    void controller.onPanelOpened();
    saveTitle.focus();
  };
  const close = () => {
    controller.onPanelHidden();
    modal.classList.add('hidden');
    modal.setAttribute('aria-hidden', 'true');
    openButton.focus();
  };
  closePanel = close;
  openButton.addEventListener('click', show);
  openButton.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      show();
    }
  });
  doc.getElementById('calls-close-btn').addEventListener('click', close);
  fileInput.addEventListener('change', () => controller.selectFile(fileInput.files && fileInput.files[0]));
  photoInput.addEventListener('change', () => {
    controller.addCaptureMedia('photo', photoInput.files && photoInput.files[0]);
    photoInput.value = '';
  });
  photoCameraButton.addEventListener('click', () => controller.openCaptureCamera('photo'));
  videoInput.addEventListener('change', () => {
    controller.addCaptureMedia('video', videoInput.files && videoInput.files[0]);
    videoInput.value = '';
  });
  videoCameraButton.addEventListener('click', () => controller.openCaptureCamera('video'));
  doc.getElementById('calls-camera-photo-capture-btn').addEventListener('click', () => controller.captureCameraPhoto());
  doc.getElementById('calls-camera-video-start-btn').addEventListener('click', () => controller.startCaptureVideoRecording());
  doc.getElementById('calls-camera-video-stop-btn').addEventListener('click', () => controller.stopCaptureVideoRecording());
  doc.getElementById('calls-camera-switch-btn').addEventListener('click', () => controller.switchCaptureCamera());
  doc.getElementById('calls-camera-cancel-btn').addEventListener('click', () => controller.closeCaptureCamera({ announce: true }));
  doc.getElementById('calls-camera-close-btn').addEventListener('click', () => controller.closeCaptureCamera({ announce: true }));
  saveTitle.addEventListener('input', () => {
    controller.updateCaptureDetails({ title: saveTitle.value });
  });
  captureType.addEventListener('change', () => {
    controller.updateCaptureDetails({ type: captureType.value });
  });
  captureNotes.addEventListener('input', () => {
    controller.updateCaptureDetails({ notes: captureNotes.value });
  });
  analysisLanguage.addEventListener('change', () => {
    controller.setAnalysisLanguage(analysisLanguage.value);
  });
  transcriptionLanguage.addEventListener('change', () => {
    controller.setTranscriptionLanguage(transcriptionLanguage.value);
  });
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
  doc.getElementById('calls-analysis-generate-btn').addEventListener('click', () => controller.generateAnalysis());
  doc.getElementById('calls-analysis-regenerate-btn').addEventListener('click', () => controller.generateAnalysis());
  doc.getElementById('calls-analysis-copy-btn').addEventListener('click', () => controller.copyAnalysis());
  doc.getElementById('calls-save-btn').addEventListener('click', () => controller.saveToLibrary(saveTitle.value));
  doc.getElementById('calls-history-refresh-btn').addEventListener('click', () => controller.loadHistory({ refreshing: true }));
  modal.addEventListener('click', (event) => { if (event.target === modal) close(); });
  doc.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || modal.classList.contains('hidden')) return;
    if (controller.isCameraOpen()) controller.closeCaptureCamera({ announce: true });
    else close();
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
    const unsubscribeLocale = subscribeLocale(() => controller.onLocaleChanged());
    const destroy = () => { unsubscribeLocale(); controller.destroy(); };
    globalThis.addEventListener('pagehide', destroy);
    globalThis.addEventListener('beforeunload', destroy);
  }
  controller.setAnalysisLanguage('auto');
  return controller;
}

export default { init };
