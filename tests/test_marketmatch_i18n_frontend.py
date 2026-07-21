"""Focused shared MarketMatch i18n runtime and enforcement tests."""

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
I18N = (ROOT / "static/js/i18n.js").as_uri()
LOCALES = (ROOT / "static/js/locales.js").as_uri()
INDEX = (ROOT / "static/index.html").read_text(encoding="utf-8")
LOGIN = (ROOT / "static/login.html").read_text(encoding="utf-8")
CALLS = (ROOT / "static/js/calls.js").read_text(encoding="utf-8")
APP = (ROOT / "static/app.js").read_text(encoding="utf-8")
CALLS_MODULE = (ROOT / "static/js/calls.js").as_uri()
HAS_NODE = shutil.which("node") is not None


def _node(script):
    if not HAS_NODE:
        pytest.skip("node binary not on PATH")
    completed = subprocess.run(
        ["node", "--input-type=module"],
        input=(textwrap.dedent(script)
               .replace("I18N_MODULE", I18N)
               .replace("LOCALES_MODULE", LOCALES)
               .replace("CALLS_MODULE", CALLS_MODULE)),
        text=True, capture_output=True, cwd=ROOT, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_catalog_parity_nonempty_placeholders_and_native_labels():
    result = _node("""
      import { CATALOGS, NATIVE_LOCALE_LABELS } from 'LOCALES_MODULE';
      const keys = Object.fromEntries(Object.entries(CATALOGS).map(([locale, catalog]) => [locale, Object.keys(catalog).sort()]));
      const placeholders = (value) => [...value.matchAll(/\\{([a-zA-Z0-9_]+)\\}/g)].map((m) => m[1]).sort();
      const mismatches = [];
      for (const key of keys.es) {
        const expected = JSON.stringify(placeholders(CATALOGS.es[key]));
        for (const locale of ['en', 'zh-Hans']) {
          if (JSON.stringify(placeholders(CATALOGS[locale][key])) !== expected) mismatches.push([locale, key]);
        }
      }
      console.log(JSON.stringify({ keys, empty: Object.values(CATALOGS).some((c) => Object.values(c).some((v) => typeof v !== 'string' || !v)), mismatches, labels: NATIVE_LOCALE_LABELS }));
    """)
    assert result["keys"]["es"] == result["keys"]["en"] == result["keys"]["zh-Hans"]
    assert result["empty"] is False
    assert result["mismatches"] == []
    assert result["labels"] == {"es": "Español", "en": "English", "zh-Hans": "简体中文"}


def test_every_literal_capture_and_shell_translation_reference_exists():
    locale_source = (ROOT / "static/js/locales.js").read_text(encoding="utf-8")
    catalog_keys = set(re.findall(r"^\s*'([^']+)'\s*:", locale_source, re.M))
    html_keys = set(re.findall(
        r'data-i18n(?:-aria-label|-title|-placeholder)?="([^"]+)"', INDEX + "\n" + LOGIN,
    ))
    javascript_keys = set(re.findall(r"\bt\(\s*['\"]([^'\"]+)['\"]", CALLS))
    timeline_keys = set(re.findall(r"CAPTURE_[A-Z_]+:\s*['\"]([^'\"]+)['\"]", CALLS))
    # Dynamic capture.status.${kind}_* keys are separately covered by media-flow tests.
    missing = (html_keys | javascript_keys | timeline_keys) - catalog_keys
    assert missing == set()


def test_locale_normalization_resolution_fallback_and_timezone_separation():
    result = _node("""
      import { DEFAULT_LOCALE, DEFAULT_TIMEZONE, SUPPORTED_LOCALES, normalizeLocale, resolveLocale, resolveAnalysisLanguage, setTemporaryLocale, getTimezone } from 'I18N_MODULE';
      const before = getTimezone();
      setTemporaryLocale('en-US');
      const afterEn = getTimezone();
      setTemporaryLocale('zh-CN');
      console.log(JSON.stringify({
        supported: SUPPORTED_LOCALES, defaultLocale: DEFAULT_LOCALE, defaultTimezone: DEFAULT_TIMEZONE,
        aliases: ['es-DO','en-US','zh-CN','zh-Hans-CN','fr'].map(normalizeLocale),
        persisted: resolveLocale({ persisted: 'en', deployment: 'es' }),
        temporary: resolveLocale({ temporary: 'zh-CN', persisted: 'en', deployment: 'es' }),
        project: resolveLocale({ project: 'zh-Hans', organization: 'en', deployment: 'es' }),
        organization: resolveLocale({ organization: 'en-US', deployment: 'es' }),
        fallback: resolveLocale({ deployment: 'invalid' }),
        timezones: [before, afterEn, getTimezone()],
        analysis: [
          resolveAnalysisLanguage('auto', 'es'), resolveAnalysisLanguage('auto', 'en'),
          resolveAnalysisLanguage('auto', 'zh-CN'), resolveAnalysisLanguage('es', 'und'),
        ],
      }));
    """)
    assert result["supported"] == ["es", "en", "zh-Hans"]
    assert result["defaultLocale"] == "es"
    assert result["defaultTimezone"] == "America/Santo_Domingo"
    assert result["aliases"] == ["es", "en", "zh-Hans", "zh-Hans", None]
    assert result["persisted"] == "en"
    assert result["temporary"] == "zh-Hans"
    assert result["project"] == "zh-Hans"
    assert result["organization"] == "en"
    assert result["fallback"] == "es"
    assert len(set(result["timezones"])) == 1
    assert result["analysis"] == ["es", "en", "zh-Hans", "es"]


def test_detected_transcript_language_labels_localize_without_changing_content():
    result = _node("""
      import { languageLabel, setTemporaryLocale } from 'I18N_MODULE';
      const transcript = 'Texto original / original text';
      const labels = {};
      for (const locale of ['es', 'en', 'zh-Hans']) {
        setTemporaryLocale(locale);
        labels[locale] = [languageLabel('es'), languageLabel('en'), languageLabel('zh-Hans'), languageLabel('und')];
      }
      console.log(JSON.stringify({ transcript, labels }));
    """)
    assert result["transcript"] == "Texto original / original text"
    assert result["labels"]["es"] == ["Español", "Inglés", "Chino simplificado", "No determinado"]
    assert result["labels"]["en"] == ["Spanish", "English", "Simplified Chinese", "Undetermined"]
    assert result["labels"]["zh-Hans"] == ["西班牙语", "英语", "简体中文", "未确定"]


def test_dynamic_translation_updates_html_text_accessibility_and_preserves_timezone():
    result = _node("""
      import { applyTranslations, getTimezone, setTemporaryLocale } from 'I18N_MODULE';
      const textNode = { dataset: { i18n: 'capture.photos.add' }, textContent: '' };
      const ariaNode = { dataset: { i18nAriaLabel: 'capture.close.aria' }, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } };
      const titleNode = { dataset: { i18nTitle: 'nav.documents' }, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } };
      const placeholderNode = { dataset: { i18nPlaceholder: 'chat.message_placeholder' }, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } };
      const root = {
        documentElement: { lang: '' },
        querySelectorAll(selector) {
          if (selector === '[data-i18n]') return [textNode];
          if (selector === '[data-i18n-aria-label]') return [ariaNode];
          if (selector === '[data-i18n-title]') return [titleNode];
          if (selector === '[data-i18n-placeholder]') return [placeholderNode];
          return [];
        },
      };
      const timezone = getTimezone();
      setTemporaryLocale('zh-Hans');
      applyTranslations(root);
      console.log(JSON.stringify({ text: textNode.textContent, aria: ariaNode.attrs['aria-label'], title: titleNode.attrs.title, placeholder: placeholderNode.attrs.placeholder, lang: root.documentElement.lang, timezone: [timezone, getTimezone()] }));
    """)
    assert result == {
        "text": "添加照片", "aria": "关闭 Capture", "title": "文档", "placeholder": "给 Odysseus 发消息…",
        "lang": "zh-Hans", "timezone": ["America/Santo_Domingo", "America/Santo_Domingo"],
    }


def test_datetime_format_uses_active_locale_and_independent_dominican_timezone():
    result = _node("""
      import { formatDateTime, getTimezone, setTemporaryLocale } from 'I18N_MODULE';
      const instant = '2026-07-19T03:00:00.000Z';
      const formatted = {};
      for (const locale of ['es', 'en', 'zh-Hans']) {
        setTemporaryLocale(locale);
        formatted[locale] = formatDateTime(instant);
      }
      console.log(JSON.stringify({ instant: new Date(instant).toISOString(), timezone: getTimezone(), formatted }));
    """)
    assert result["instant"] == "2026-07-19T03:00:00.000Z"
    assert result["timezone"] == "America/Santo_Domingo"
    assert result["formatted"] == {
        "es": "18/07/2026, 23:00",
        "en": "07/18/2026, 23:00",
        "zh-Hans": "2026/07/18 23:00",
    }


def test_locale_rerender_preserves_capture_codes_original_content_media_and_timeline():
    result = _node("""
      import { setTemporaryLocale } from 'I18N_MODULE';
      import { createCallsController } from 'CALLS_MODULE';
      const snapshots = { details: [], media: [], timelines: [] };
      const view = {
        renderCaptureDetails(value) { snapshots.details.push({ title: value.title, type: value.type, notes: value.notes }); },
        renderCaptureMedia(kind, values) { snapshots.media.push([kind, values.map((v) => ({ name: v.name, caption: v.caption }))]); },
        renderTimeline(values) { snapshots.timelines.push(values.map((v) => ({ code: v.code, params: v.params }))); },
        setCaptureSaveReady() {}, setMediaStatus() {}, setCaptureStatus() {}, setAnalysisLanguage() {},
        setResolvedAnalysisLanguage() {}, showAnalysisReady() {}, clearAnalysis() {}, setAnalysisBusy() {},
      };
      const file = { name: 'MARE B.jpg', size: 10, type: 'image/jpeg', lastModified: 7 };
      const controller = createCallsController({
        view,
        now: () => Date.parse('2026-07-19T12:00:00Z'),
        createObjectURL: () => 'blob:temporary-preview',
        revokeObjectURL() {},
      });
      controller.updateCaptureDetails({ title: 'ARENA T1', type: 'field_observation', notes: 'Nota original 中文' });
      controller.addCaptureMedia('photo', file);
      setTemporaryLocale('zh-Hans');
      controller.onLocaleChanged();
      console.log(JSON.stringify({
        details: snapshots.details.at(-1),
        photo: snapshots.media.filter(([kind]) => kind === 'photo').at(-1)[1][0],
        timeline: snapshots.timelines.at(-1),
        saveActive: controller.isSaveActive(), analysisActive: controller.isAnalysisActive(),
      }));
    """)
    assert result["details"] == {
        "title": "ARENA T1", "type": "field_observation", "notes": "Nota original 中文",
    }
    assert result["photo"] == {"name": "MARE B.jpg", "caption": ""}
    assert [event["code"] for event in result["timeline"]] == ["CAPTURE_STARTED", "CAPTURE_PHOTO_ADDED"]
    assert result["timeline"][1]["params"] == {"filename": "MARE B.jpg"}
    assert result["saveActive"] is False
    assert result["analysisActive"] is False


def test_temporary_override_is_memory_only_and_explicit_save_contract_is_owner_free():
    result = _node("""
      import { initializeI18n, setTemporaryLocale, getLocale, getPersistedLocale, saveLocalePreference, clearTemporaryLocale } from 'I18N_MODULE';
      const calls = [];
      const response = (payload) => ({ ok: true, json: async () => payload });
      const fetchImpl = async (url, options) => {
        calls.push([url, options]);
        return response({ locale: options.method === 'PUT' ? 'en' : 'es', persisted_locale: options.method === 'PUT' ? 'en' : null, locale_source: options.method === 'PUT' ? 'user' : 'deployment', timezone: 'America/Santo_Domingo', timezone_source: 'deployment' });
      };
      await initializeI18n({ fetchImpl });
      setTemporaryLocale('en');
      const temporary = [getLocale(), getPersistedLocale()];
      const saved = await saveLocalePreference('en', { fetchImpl });
      clearTemporaryLocale();
      console.log(JSON.stringify({ temporary, saved, final: [getLocale(), getPersistedLocale()], calls: calls.map(([url, options]) => ({ url, method: options.method, credentials: options.credentials, body: options.body || null, headers: options.headers })) }));
    """)
    assert result["temporary"] == ["en", None]
    assert result["saved"] is True
    assert result["final"] == ["en", "en"]
    assert result["calls"][0]["method"] == "GET"
    update = result["calls"][1]
    assert update["url"] == "/api/marketmatch/locale"
    assert update["method"] == "PUT"
    assert update["credentials"] == "same-origin"
    assert json.loads(update["body"]) == {"locale": "en"}
    assert not ({"owner", "user", "username", "timezone", "session"} & set(json.loads(update["body"])))


def test_failed_preference_save_keeps_confirmed_preference_and_temporary_locale():
    result = _node("""
      import { initializeI18n, setTemporaryLocale, getLocale, getPersistedLocale, getTemporaryLocale, saveLocalePreference } from 'I18N_MODULE';
      await initializeI18n({ fetchImpl: async () => ({ ok: true, json: async () => ({ locale: 'en', persisted_locale: 'en', timezone: 'America/Santo_Domingo' }) }) });
      setTemporaryLocale('zh-Hans');
      const saved = await saveLocalePreference('zh-Hans', { fetchImpl: async () => ({ ok: false, status: 503 }) });
      console.log(JSON.stringify({ saved, locale: getLocale(), persisted: getPersistedLocale(), temporary: getTemporaryLocale() }));
    """)
    assert result == {"saved": False, "locale": "zh-Hans", "persisted": "en", "temporary": "zh-Hans"}


def test_clearing_temporary_override_returns_to_resolved_deployment_locale():
    result = _node("""
      import { initializeI18n, setTemporaryLocale, clearTemporaryLocale, getLocale } from 'I18N_MODULE';
      await initializeI18n({ fetchImpl: async () => ({ ok: true, json: async () => ({ locale: 'en', persisted_locale: null, timezone: 'America/Santo_Domingo' }) }) });
      setTemporaryLocale('zh-Hans');
      const temporary = getLocale();
      clearTemporaryLocale();
      console.log(JSON.stringify({ temporary, restored: getLocale() }));
    """)
    assert result == {"temporary": "zh-Hans", "restored": "en"}


def test_capture_html_controlled_text_and_accessibility_use_catalog_keys():
    capture = INDEX.split('id="calls-modal"', 1)[1].split('<!-- Memory Management Modal -->', 1)[0]
    for tag in re.findall(r"<(?:h2|h3|button|label|option)\b[^>]*>.*?</(?:h2|h3|button|label|option)>", capture, re.S):
        visible = re.sub(r"<[^>]+>", "", tag).strip()
        if not visible or visible in {"Español", "English", "简体中文", "✖"}:
            continue
        assert "data-i18n=" in tag, tag
    for tag in re.findall(r"<[^>]+aria-label=\"[^\"]+\"[^>]*>", capture):
        assert "data-i18n-aria-label=" in tag, tag
    assert '<html lang="es">' in INDEX
    assert 'id="marketmatch-locale-status" class="marketmatch-locale-status sr-only"' in INDEX
    assert 'value="field_observation"' in capture
    assert '<option>Field Observation</option>' not in capture

    for shell_marker in (
        'id="chats-section-label" class="section-title-label" data-i18n="nav.chats"',
        'id="rail-documents" title="Documentos" data-i18n-title="nav.documents"',
        'id="rail-research" title="Investigación" data-i18n-title="nav.research"',
        'id="session-bulk-archive" title="Archivo" data-i18n-title="nav.archive"',
        'id="tool-library-btn"',
        '<span class="grow" data-i18n="nav.library">Biblioteca</span>',
        'id="sidebar-new-chat-btn" title="Nuevo chat" data-i18n-title="nav.new_chat"',
        'data-i18n-placeholder="chat.message_placeholder"',
        'id="mode-agent-btn" aria-pressed="true" data-i18n="nav.agent"',
    ):
        assert shell_marker in INDEX


def test_compact_language_panel_lifecycle_and_native_trigger_label():
    result = _node("""
      import { bindLocaleControls, getLocale } from 'I18N_MODULE';
      class Target {
        constructor(id) { this.id = id; this.dataset = {}; this.listeners = {}; this.attrs = {}; this.hidden = false; this.disabled = false; this.value = ''; this.textContent = ''; this.focused = false; }
        addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
        dispatch(type, extra = {}) { for (const callback of this.listeners[type] || []) callback({ target: this, preventDefault() {}, ...extra }); }
        setAttribute(key, value) { this.attrs[key] = value; }
        focus() { this.focused = true; }
      }
      const ids = ['marketmatch-locale-controls','marketmatch-locale-trigger','marketmatch-locale-trigger-label','marketmatch-locale-panel','marketmatch-locale-close','marketmatch-locale-select','marketmatch-locale-save','marketmatch-locale-reset','marketmatch-locale-status'];
      const nodes = Object.fromEntries(ids.map((id) => [id, new Target(id)]));
      nodes['marketmatch-locale-panel'].hidden = true;
      nodes['marketmatch-locale-controls'].contains = (target) => Object.values(nodes).includes(target);
      const documentListeners = {};
      const doc = {
        getElementById(id) { return nodes[id] || null; },
        addEventListener(type, callback) { (documentListeners[type] ||= []).push(callback); },
      };
      const dispatchDocument = (type, event) => { for (const callback of documentListeners[type] || []) callback(event); };
      globalThis.fetch = async () => ({ ok: true, json: async () => ({ locale: getLocale(), persisted_locale: getLocale(), timezone: 'America/Santo_Domingo' }) });
      bindLocaleControls(doc);
      const panel = nodes['marketmatch-locale-panel'];
      const trigger = nodes['marketmatch-locale-trigger'];
      const select = nodes['marketmatch-locale-select'];
      const initial = [panel.hidden, trigger.attrs['aria-expanded'], nodes['marketmatch-locale-trigger-label'].textContent];
      trigger.dispatch('click');
      const opened = [panel.hidden, trigger.attrs['aria-expanded']];
      select.value = 'en'; select.dispatch('change');
      const selected = [panel.hidden, getLocale(), nodes['marketmatch-locale-trigger-label'].textContent];
      trigger.dispatch('click'); trigger.dispatch('click');
      const toggledClosed = panel.hidden;
      trigger.dispatch('click');
      dispatchDocument('click', { target: new Target('outside') });
      const outsideClosed = panel.hidden;
      trigger.dispatch('click');
      dispatchDocument('keydown', { key: 'Escape', preventDefault() {} });
      const escape = [panel.hidden, trigger.focused];
      trigger.dispatch('click'); nodes['marketmatch-locale-reset'].dispatch('click');
      const resetClosed = panel.hidden;
      select.value = 'zh-Hans'; select.dispatch('change'); trigger.dispatch('click');
      nodes['marketmatch-locale-save'].dispatch('click');
      await new Promise((resolve) => setTimeout(resolve, 0));
      console.log(JSON.stringify({ initial, opened, selected, toggledClosed, outsideClosed, escape, resetClosed, saveClosed: panel.hidden }));
    """)
    assert result == {
        "initial": [True, "false", "Español"],
        "opened": [False, "true"],
        "selected": [True, "en", "English"],
        "toggledClosed": True,
        "outsideClosed": True,
        "escape": [True, True],
        "resetClosed": True,
        "saveClosed": True,
    }


def test_language_panel_is_bounded_and_closed_by_default_in_html():
    styles = (ROOT / "static/style.css").read_text(encoding="utf-8")
    assert 'id="marketmatch-locale-trigger"' in INDEX
    assert 'aria-expanded="false"' in INDEX
    assert 'id="marketmatch-locale-panel"' in INDEX and 'data-i18n-aria-label="locale.panel.aria" hidden' in INDEX
    assert ".marketmatch-locale-panel[hidden] { display: none; }" in styles
    assert "calc(100vw - 12px)" in styles


def test_primary_shell_commands_render_in_all_catalogs_and_brands_are_explicit():
    result = _node("""
      import { t } from 'I18N_MODULE';
      const keys = ['nav.new_chat','nav.search','nav.calendar','nav.compare','nav.email','nav.gallery','nav.documents','nav.notes','nav.tasks','nav.theme','nav.tools','nav.agent','nav.chat','chat.message_placeholder'];
      const values = Object.fromEntries(['es','en','zh-Hans'].map((locale) => [locale, Object.fromEntries(keys.map((key) => [key, t(key, {}, locale)]))]));
      console.log(JSON.stringify(values));
    """)
    assert result["es"]["nav.new_chat"] == "Nuevo chat"
    assert result["es"]["nav.search"] == "Buscar"
    assert result["en"]["nav.new_chat"] == "New Chat"
    assert result["zh-Hans"]["nav.calendar"] == "日历"
    assert [result[locale]["nav.tools"] for locale in ("es", "en", "zh-Hans")] == ["Herramientas", "Tools", "工具"]
    assert result["zh-Hans"]["chat.message_placeholder"] == "给 Odysseus 发消息…"

    controlled_ids = {
        "rail-search-btn": "data-i18n-title=\"nav.search_conversations\"",
        "rail-new-session": "data-i18n-title=\"nav.new_chat\"",
        "rail-calendar": "data-i18n-title=\"nav.calendar\"",
        "tool-calendar-btn": "data-i18n=\"nav.calendar\"",
        "tool-compare-btn": "data-i18n=\"nav.compare\"",
        "tool-gallery-btn": "data-i18n=\"nav.gallery\"",
        "tool-notes-btn": "data-i18n=\"nav.notes\"",
        "tool-tasks-btn": "data-i18n=\"nav.tasks\"",
        "tool-theme-btn": "data-i18n=\"nav.theme\"",
        "tools-section": "data-i18n=\"nav.tools\"",
        "message": "data-i18n-placeholder=\"chat.message_placeholder\"",
        "mode-agent-btn": "data-i18n=\"nav.agent\"",
        "mode-chat-btn": "data-i18n=\"nav.chat\"",
    }
    for element_id, marker in controlled_ids.items():
        start = INDEX.index(f'id="{element_id}"')
        window = INDEX[start:start + 1000]
        assert marker in window, element_id

    immutable_product_names = {"MarketMatch", "Odysseus", "Brain", "Cookbook", "Capture"}
    assert immutable_product_names == {name for name in immutable_product_names if name in INDEX}
    hover_labels = APP.split("function initRailHoverLabels()", 1)[1].split("// Redirect to login", 1)[0]
    for accidental_literal in ("'Calendar'", "'Compare'", "'Gallery'", "'Notes'", "'Tasks'", "'Theme'", "'Settings'"):
        assert accidental_literal not in hover_labels
    assert "t(value)" in hover_labels
    assert "'Message Odysseus...'" not in APP
    assert "t(w < PLACEHOLDER_COMPACT_WIDTH ? 'chat.message_compact' : 'chat.message_placeholder')" in APP


def test_pre_authentication_surface_defaults_to_spanish_and_reuses_shared_runtime():
    assert '<html lang="es">' in LOGIN
    assert "from '/static/js/i18n.js'" in LOGIN
    for key in (
        "auth.username", "auth.password", "auth.confirm_password", "auth.remember",
        "auth.show_password", "auth.sign_in", "auth.no_account", "auth.sign_up",
    ):
        assert f'data-i18n="{key}"' in LOGIN or f'data-i18n-aria-label="{key}"' in LOGIN or f'data-i18n-title="{key}"' in LOGIN
    assert "submitBtn.textContent = t('auth.sign_in')" in LOGIN
    assert "totpLabel.textContent = t('auth.two_factor_code')" in LOGIN


def test_timeline_uses_stable_codes_and_no_browser_persistence_or_remote_translation():
    i18n_source = (ROOT / "static/js/i18n.js").read_text(encoding="utf-8")
    assert "message: 'Capture started.'" not in CALLS
    assert "addTimelineEvent('Recording" not in CALLS
    assert "CAPTURE_PHOTO_ADDED" in CALLS
    assert "CAPTURE_SAVED_TO_LIBRARY" in CALLS
    assert "localStorage" not in CALLS
    assert "sessionStorage" not in CALLS
    assert "indexedDB" not in CALLS
    assert "caches.open" not in CALLS
    assert "translate.googleapis" not in CALLS
    assert "navigator.language" not in i18n_source
    assert "output_language" in CALLS
    request_body = re.search(r"JSON\.stringify\(\{\s*transcript,\s*output_language: canonicalOutputLanguage,\s*transcript_language: transcriptLanguage,\s*\}\)", CALLS).group(0)
    assert "photos" not in request_body


def test_capture_dynamic_errors_and_timeline_messages_are_catalog_driven():
    literal_errors = re.findall(
        r"new CallsUiError\([^,]+,\s*(['\"])(?!capture\.|common\.)[^'\"]+[A-Za-z][^'\"]*\1",
        CALLS,
    )
    assert literal_errors == []
    assert "message: 'Capture started.'" not in CALLS
    for key in ("photo_added", "video_added", "attachment_removed"):
        assert f"'capture.timeline.{key}'" in (ROOT / "static/js/locales.js").read_text(encoding="utf-8")


def test_identifiers_and_original_content_are_structurally_excluded():
    locale_source = (ROOT / "static/js/locales.js").read_text(encoding="utf-8")
    for identifier in ("MARE B", "SOLE 26", "ARENA T1", "MEDUWY575021", "WA-10", "PO-2026-0041"):
        assert identifier not in locale_source
    assert "setElementText(transcript, value.transcript_text)" in CALLS
    assert "setElementText(name, media.name)" in CALLS
