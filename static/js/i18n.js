import { CATALOGS, NATIVE_LOCALE_LABELS } from './locales.js';

export const SUPPORTED_LOCALES = Object.freeze(['es', 'en', 'zh-Hans']);
export const DEFAULT_LOCALE = 'es';
export const DEFAULT_TIMEZONE = 'America/Santo_Domingo';
export const LOCALE_ENDPOINT = '/api/marketmatch/locale';

const ALIASES = Object.freeze({
  es: 'es', 'es-es': 'es', 'es-do': 'es',
  en: 'en', 'en-us': 'en', 'en-gb': 'en',
  zh: 'zh-Hans', 'zh-cn': 'zh-Hans', 'zh-sg': 'zh-Hans',
  'zh-hans': 'zh-Hans', 'zh-hans-cn': 'zh-Hans',
});
const INTL_LOCALES = Object.freeze({ es: 'es-DO', en: 'en-US', 'zh-Hans': 'zh-Hans-CN' });

let activeLocale = DEFAULT_LOCALE;
let persistedLocale = null;
let temporaryLocale = null;
let resolvedBaseLocale = DEFAULT_LOCALE;
let displayTimezone = DEFAULT_TIMEZONE;
const listeners = new Set();

export function normalizeLocale(value) {
  return typeof value === 'string' ? (ALIASES[value.trim().toLowerCase()] || null) : null;
}

export function normalizeTranscriptLanguage(value) {
  if (typeof value !== 'string') return 'und';
  const key = value.trim().toLowerCase();
  if (['zh', 'zh-cn', 'zh-sg', 'zh-hans', 'zh-hans-cn', 'cmn', 'cmn-hans'].includes(key)) return 'zh';
  if (['es', 'es-es', 'es-do'].includes(key)) return 'es';
  if (['en', 'en-us', 'en-gb'].includes(key)) return 'en';
  return 'und';
}

export function resolveLocale({
  temporary = null,
  persisted = null,
  project = null,
  organization = null,
  deployment = DEFAULT_LOCALE,
} = {}) {
  return normalizeLocale(temporary)
    || normalizeLocale(persisted)
    || normalizeLocale(project)
    || normalizeLocale(organization)
    || normalizeLocale(deployment)
    || DEFAULT_LOCALE;
}

export function getLocale() { return activeLocale; }
export function getPersistedLocale() { return persistedLocale; }
export function getTemporaryLocale() { return temporaryLocale; }
export function getTimezone() { return displayTimezone; }

function interpolate(template, params) {
  return template.replace(/\{([a-zA-Z0-9_]+)\}/g, (_, key) => (
    Object.prototype.hasOwnProperty.call(params, key) ? String(params[key]) : `{${key}}`
  ));
}

export function t(key, params = {}, locale = activeLocale) {
  const canonical = normalizeLocale(locale) || DEFAULT_LOCALE;
  const fallback = CATALOGS[DEFAULT_LOCALE][key];
  const value = CATALOGS[canonical][key] || fallback;
  if (typeof value !== 'string' || !value) return `[${key}]`;
  return interpolate(value, params);
}

export function languageLabel(code, locale = activeLocale) {
  const canonical = code === 'zh' ? 'zh-Hans'
    : (code === 'zh-Hant' ? 'zh-Hant' : (code === 'und' ? 'und' : (normalizeLocale(code) || 'und')));
  return t(`language.${canonical}`, {}, locale);
}

export function nativeLocaleLabel(locale) {
  return NATIVE_LOCALE_LABELS[normalizeLocale(locale)] || NATIVE_LOCALE_LABELS.es;
}

export function formatDateTime(value, options = {}) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat(INTL_LOCALES[activeLocale], {
    timeZone: displayTimezone,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
    hour12: false,
    ...options,
  }).format(date);
}

export function formatNumber(value, options = {}) {
  return new Intl.NumberFormat(INTL_LOCALES[activeLocale], options).format(value);
}

export function formatFileSize(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return '';
  if (bytes < 1024) return `${formatNumber(bytes)} B`;
  if (bytes < 1024 * 1024) return `${formatNumber(bytes / 1024, { maximumFractionDigits: 1 })} KiB`;
  return `${formatNumber(bytes / (1024 * 1024), { maximumFractionDigits: 2 })} MiB`;
}

export function applyTranslations(root = globalThis.document) {
  if (!root || typeof root.querySelectorAll !== 'function') return;
  root.querySelectorAll('[data-i18n]').forEach((element) => {
    element.textContent = t(element.dataset.i18n);
  });
  root.querySelectorAll('[data-i18n-aria-label]').forEach((element) => {
    element.setAttribute('aria-label', t(element.dataset.i18nAriaLabel));
  });
  root.querySelectorAll('[data-i18n-title]').forEach((element) => {
    element.setAttribute('title', t(element.dataset.i18nTitle));
  });
  root.querySelectorAll('[data-i18n-placeholder]').forEach((element) => {
    element.setAttribute('placeholder', t(element.dataset.i18nPlaceholder));
  });
  if (root.documentElement) root.documentElement.lang = activeLocale;
}

function notify() {
  applyTranslations();
  listeners.forEach((listener) => listener({
    locale: activeLocale,
    persistedLocale,
    temporaryLocale,
    timezone: displayTimezone,
  }));
}

export function subscribe(listener) {
  if (typeof listener !== 'function') return () => {};
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function setTemporaryLocale(locale) {
  const canonical = normalizeLocale(locale);
  if (!canonical) return false;
  temporaryLocale = canonical;
  activeLocale = canonical;
  notify();
  return true;
}

export function clearTemporaryLocale() {
  temporaryLocale = null;
  activeLocale = persistedLocale || resolvedBaseLocale || DEFAULT_LOCALE;
  notify();
}

function safeSettings(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const locale = normalizeLocale(value.locale);
  const persisted = value.persisted_locale == null ? null : normalizeLocale(value.persisted_locale);
  const timezone = typeof value.timezone === 'string' && value.timezone ? value.timezone : 'UTC';
  if (!locale || (value.persisted_locale != null && !persisted)) return null;
  return { locale, persisted, timezone };
}

export async function initializeI18n({ fetchImpl = globalThis.fetch } = {}) {
  activeLocale = DEFAULT_LOCALE;
  persistedLocale = null;
  resolvedBaseLocale = DEFAULT_LOCALE;
  displayTimezone = DEFAULT_TIMEZONE;
  applyTranslations();
  try {
    const response = await fetchImpl(LOCALE_ENDPOINT, {
      method: 'GET', credentials: 'same-origin', headers: { Accept: 'application/json' },
    });
    if (!response.ok) return false;
    const settings = safeSettings(await response.json());
    if (!settings) return false;
    persistedLocale = settings.persisted;
    resolvedBaseLocale = settings.locale;
    displayTimezone = settings.timezone;
    if (!temporaryLocale) activeLocale = settings.locale;
    notify();
    return true;
  } catch (_) {
    return false;
  }
}

export async function saveLocalePreference(locale = activeLocale, { fetchImpl = globalThis.fetch } = {}) {
  const canonical = normalizeLocale(locale);
  if (!canonical) return false;
  try {
    const response = await fetchImpl(LOCALE_ENDPOINT, {
      method: 'PUT', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ locale: canonical }),
    });
    if (!response.ok) return false;
    const settings = safeSettings(await response.json());
    if (!settings || settings.persisted !== canonical) return false;
    persistedLocale = settings.persisted;
    resolvedBaseLocale = settings.locale;
    displayTimezone = settings.timezone;
    temporaryLocale = null;
    activeLocale = settings.locale;
    notify();
    return true;
  } catch (_) {
    return false;
  }
}

export function resolveAnalysisLanguage(selection, transcriptLanguage, segmentLanguages = []) {
  const explicit = selection === 'zh-Hant' ? 'zh-Hant' : normalizeLocale(selection);
  if (selection !== 'auto') return explicit;
  const normalizedTranscript = normalizeTranscriptLanguage(transcriptLanguage);
  const globalLanguage = normalizedTranscript === 'zh' ? 'zh-Hans'
    : (normalizedTranscript === 'und' ? null : normalizedTranscript);
  if (globalLanguage) return globalLanguage;
  const supported = segmentLanguages.map(normalizeTranscriptLanguage).filter((item) => item !== 'und')
    .map((item) => item === 'zh' ? 'zh-Hans' : item);
  if (supported.length) {
    const counts = supported.reduce((acc, item) => ({ ...acc, [item]: (acc[item] || 0) + 1 }), {});
    const ordered = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    if (ordered.length === 1 || ordered[0][1] > ordered[1][1]) return ordered[0][0];
  }
  return DEFAULT_LOCALE;
}

export function bindLocaleControls(doc = globalThis.document) {
  const controls = doc && doc.getElementById('marketmatch-locale-controls');
  const trigger = doc && doc.getElementById('marketmatch-locale-trigger');
  const triggerLabel = doc && doc.getElementById('marketmatch-locale-trigger-label');
  const panel = doc && doc.getElementById('marketmatch-locale-panel');
  const closeButton = doc && doc.getElementById('marketmatch-locale-close');
  const select = doc && doc.getElementById('marketmatch-locale-select');
  const save = doc && doc.getElementById('marketmatch-locale-save');
  const reset = doc && doc.getElementById('marketmatch-locale-reset');
  const status = doc && doc.getElementById('marketmatch-locale-status');
  if (!controls || !trigger || !triggerLabel || !panel || !closeButton
      || !select || !save || !reset || !status || controls.dataset.localeBound === 'true') return;
  controls.dataset.localeBound = 'true';
  const setPanelOpen = (open, { restoreFocus = false } = {}) => {
    panel.hidden = !open;
    trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (!open && restoreFocus && typeof trigger.focus === 'function') trigger.focus();
  };
  const render = () => {
    select.value = activeLocale;
    triggerLabel.textContent = nativeLocaleLabel(activeLocale);
    reset.hidden = !temporaryLocale;
    status.textContent = t(temporaryLocale ? 'locale.status.temporary' : 'locale.status.default');
  };
  trigger.addEventListener('click', () => setPanelOpen(panel.hidden));
  closeButton.addEventListener('click', () => setPanelOpen(false, { restoreFocus: true }));
  select.addEventListener('change', () => {
    setTemporaryLocale(select.value);
    render();
    setPanelOpen(false);
  });
  reset.addEventListener('click', () => {
    clearTemporaryLocale();
    render();
    setPanelOpen(false);
  });
  save.addEventListener('click', async () => {
    save.disabled = true;
    const ok = await saveLocalePreference(activeLocale);
    save.disabled = false;
    render();
    status.textContent = t(ok ? 'locale.status.saved' : 'locale.status.save_failed');
    if (ok) setPanelOpen(false);
  });
  doc.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !panel.hidden) {
      event.preventDefault();
      setPanelOpen(false, { restoreFocus: true });
    }
  });
  doc.addEventListener('click', (event) => {
    if (!panel.hidden && event.target && !controls.contains(event.target)) setPanelOpen(false);
  });
  subscribe(render);
  setPanelOpen(false);
  render();
}

export { CATALOGS, NATIVE_LOCALE_LABELS };
