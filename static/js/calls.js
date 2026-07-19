const ENDPOINT = '/api/marketmatch/stt/transcribe';
const ANALYSIS_ENDPOINT = '/api/marketmatch/calls/analyze';
const DOCUMENT_ENDPOINT = '/api/document';
const DOCUMENT_HISTORY_ENDPOINT = '/api/documents/library?search=MarketMatch+Calls+Transcript&sort=recent&offset=0&limit=20';
const CALLS_DOCUMENT_MARKER = [
  '# MarketMatch Calls Transcript',
  '',
  '**AI-generated transcript. Review before relying on it for operational decisions.**',
  '',
  'Source: MarketMatch Calls',
].join('\n');
const MAX_CALLS_HISTORY_ITEMS = 20;
export const MAX_CALLS_WAV_BYTES = 20 * 1024 * 1024;
export const CALLS_WAV_SAMPLE_RATE = 16000;
export const MAX_CALLS_ANALYSIS_TRANSCRIPT_CHARS = 12000;
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
const HISTORY_FAILURE = 'Saved transcripts could not be loaded. Please try again.';
const ANALYSIS_FAILURE = 'Call analysis could not be generated. Please try again.';
const ANALYSIS_INVALID_RESPONSE = 'The local analysis result could not be validated.';

const ANALYSIS_STATUS_MESSAGES = Object.freeze({
  401: 'Your browser session has expired. Sign in again, then retry.',
  403: 'Your account is not allowed to use Calls analysis.',
  413: 'This transcript is too long for the analysis pilot.',
  422: 'This transcript cannot be analyzed. Review it and try again.',
  502: ANALYSIS_INVALID_RESPONSE,
  503: 'Local call analysis is unavailable right now.',
  504: 'Local call analysis timed out. Please try again.',
});
const ANALYSIS_RESPONSE_LIMITS = Object.freeze({
  summary: 2000,
  items: 20,
  itemText: 1000,
  ownerOrDueDate: 200,
  totalText: 16000,
});

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
    && documentValue.preview.startsWith(CALLS_DOCUMENT_MARKER),
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
  if (typeof value !== 'string' || !value) return 'Date unavailable';
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) return 'Date unavailable';
  return parsed.toLocaleString();
}

export function fixedCallsError(status) {
  return STATUS_MESSAGES[status] || GENERIC_FAILURE;
}

export function fixedCallsAnalysisError(status) {
  return ANALYSIS_STATUS_MESSAGES[status] || ANALYSIS_FAILURE;
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
  { fetchImpl = globalThis.fetch, signal } = {},
) {
  if (typeof transcript !== 'string' || !transcript.trim()) {
    throw new CallsUiError('ANALYSIS_EMPTY', 'There is no transcript text to analyze.');
  }
  if (_analysisTextCharacters(transcript) > MAX_CALLS_ANALYSIS_TRANSCRIPT_CHARS) {
    throw new CallsUiError(
      'ANALYSIS_TOO_LONG',
      'This transcript is longer than the 12,000-character analysis pilot limit.',
    );
  }
  let response;
  try {
    response = await fetchImpl(ANALYSIS_ENDPOINT, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ transcript }),
      signal,
    });
  } catch (error) {
    if (error && error.name === 'AbortError') {
      throw new CallsUiError('ANALYSIS_CANCELLED', 'Call analysis cancelled.');
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
    : 'None identified.';
  const actions = analysis.action_items.length
    ? analysis.action_items.map((item, index) => [
      `${index + 1}. Task: ${item.task}`,
      `Owner: ${item.owner === null ? 'Not stated.' : item.owner}`,
      `Due date: ${item.due_date === null ? 'Not stated.' : item.due_date}`,
    ].join('\n')).join('\n\n')
    : 'None identified.';
  return [
    'Summary',
    analysis.summary,
    '',
    'Decisions',
    listText(analysis.decisions),
    '',
    'Action Items',
    actions,
    '',
    'Open Questions',
    listText(analysis.open_questions),
  ].join('\n');
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
      throw new CallsUiError('HISTORY_CANCELLED', 'History loading cancelled.');
    }
    throw new CallsUiError('HISTORY_REQUEST_FAILED', HISTORY_FAILURE);
  }
  if (!response || response.ok !== true) {
    const status = response && Number.isInteger(response.status) ? response.status : 0;
    const message = status === 401
      ? 'Your browser session has expired. Sign in again, then retry.'
      : status === 403
        ? 'Your account is not allowed to view saved documents.'
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
      callView('setHistoryError', 'The saved transcript could not be opened. Please try again.');
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
    clearAnalysisState();
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
    clearAnalysisState();
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
      callView('showAnalysisReady', next.transcript_text.length > 0);
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
    if (!result || saveActive || saved || active || analysisActive
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
      callView('setAnalysisStatus', 'There is no transcript text to analyze.', 'error');
      return false;
    }
    if (_analysisTextCharacters(transcript) > MAX_CALLS_ANALYSIS_TRANSCRIPT_CHARS) {
      callView('beginAnalysisAttempt');
      callView(
        'setAnalysisStatus',
        'This transcript is longer than the 12,000-character analysis pilot limit.',
        'error',
      );
      callView('setAnalysisBusy', false);
      return false;
    }
    analysisActive = true;
    analysis = null;
    const run = ++analysisGeneration;
    analysisController = createAbortController();
    callView('beginAnalysisAttempt');
    callView('setAnalysisBusy', true);
    callView('setAnalysisStatus', 'Generating a reviewable draft locally…', 'loading');
    try {
      const next = await requestCallsAnalysis(transcript, {
        fetchImpl,
        signal: analysisController.signal,
      });
      if (run !== analysisGeneration) return false;
      analysis = next;
      callView('renderAnalysis', next);
      callView('setAnalysisStatus', 'Call analysis ready for review.', 'success');
      return true;
    } catch (error) {
      if (run !== analysisGeneration) return false;
      const safe = error instanceof CallsUiError
        ? error : new CallsUiError('ANALYSIS_REQUEST_FAILED', ANALYSIS_FAILURE);
      if (safe.code !== 'ANALYSIS_CANCELLED') {
        callView('setAnalysisStatus', safe.message, 'error');
      }
      return false;
    } finally {
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
      callView('setAnalysisStatus', 'Call analysis copied.', 'success');
      return true;
    } catch (_) {
      callView('setAnalysisStatus', 'Call analysis could not be copied.', 'error');
      return false;
    }
  }

  function reset() {
    generation += 1;
    if (controller) controller.abort();
    controller = null;
    active = false;
    clearAnalysisState();
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
    panelVisible = false;
    if (historyActive) abortHistory();
    if (active) cancel();
    if (analysisActive) {
      clearAnalysisState();
      callView('showAnalysisReady', Boolean(result && result.transcript_text));
    }
    if (saveActive) {
      clearSaveState({ clearView: false });
      callView('setSaveStatus', 'Save cancelled when Calls was closed.', 'cancelled');
    }
    if (cancelRecording({ announce: false })) {
      callView('setRecordingStatus', 'Recording stopped and discarded when Calls was hidden.', 'cancelled');
    }
    return true;
  }

  function onPanelOpened() {
    panelVisible = true;
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
    generateAnalysis,
    copyAnalysis,
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
  const saveSection = byId('calls-save');
  const saveTitle = byId('calls-save-title');
  const saveButton = byId('calls-save-btn');
  const saveStatus = byId('calls-save-status');
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

  const captureBusy = () => ['permission', 'recording', 'processing'].includes(recordingUiState);
  const syncControls = () => {
    const operationBusy = transcriptionBusy || saveBusy || analysisBusy || captureBusy();
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
    analysisGenerate.disabled = operationBusy || !analysisReady || analysisAttempted;
    analysisRegenerate.disabled = operationBusy || !analysisReady;
    analysisCopy.disabled = operationBusy || !analysisComplete;
  };

  const clearAnalysis = () => {
    analysisBusy = false;
    analysisReady = false;
    analysisAttempted = false;
    analysisComplete = false;
    analysisSection.hidden = true;
    analysisResult.hidden = true;
    analysisGenerate.hidden = false;
    analysisGenerate.textContent = 'Generate call analysis';
    analysisRegenerate.hidden = true;
    analysisRegenerate.textContent = 'Regenerate analysis';
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
    clearAnalysis();
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
          ? 'Generate only when you are ready to review an AI-produced draft.'
          : 'No transcript text is available to analyze.',
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
      analysisGenerate.textContent = busy ? 'Generating…' : 'Generate call analysis';
      analysisRegenerate.textContent = busy ? 'Generating…' : 'Regenerate analysis';
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
        const items = values.length ? values : ['None identified.'];
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
        setElementText(item, 'None identified.');
        analysisActions.appendChild(item);
      } else {
        for (const action of value.action_items) {
          const item = doc.createElement('li');
          const task = doc.createElement('p');
          const owner = doc.createElement('p');
          const dueDate = doc.createElement('p');
          setElementText(task, `Task: ${action.task}`);
          setElementText(owner, `Owner: ${action.owner === null ? 'Not stated.' : action.owner}`);
          setElementText(
            dueDate,
            `Due date: ${action.due_date === null ? 'Not stated.' : action.due_date}`,
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
    setHistoryLoading(busy, refreshing) {
      historyRefresh.disabled = busy;
      historyRefresh.textContent = busy ? (refreshing ? 'Refreshing…' : 'Loading…') : 'Refresh history';
      if (busy) {
        setElementText(historyStatus, refreshing ? 'Refreshing saved transcripts…' : 'Loading saved transcripts…');
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
          `MarketMatch Calls · ${formatCallsHistoryDate(documentValue.updated_at || documentValue.created_at)}`,
        );
        setElementText(open, 'Open');
        open.setAttribute('aria-label', `Open ${documentValue.title} from Library`);
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
        empty
          ? 'Saved transcripts loaded. No Calls transcripts were found.'
          : `${documents.length} saved ${documents.length === 1 ? 'transcript' : 'transcripts'} loaded.`,
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
  const show = () => {
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
    void controller.onPanelOpened();
    recordStart.focus();
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
