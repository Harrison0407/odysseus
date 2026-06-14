import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.cookbook_routes as cookbook_routes
from routes.cookbook_routes import (
    _clear_whisper_cache_dir,
    _looks_like_whisper_checksum_error,
    setup_cookbook_routes,
    _write_transcript_outputs,
)


def test_write_transcript_outputs_creates_txt_srt_vtt(tmp_path):
    json_path = tmp_path / "sample.json"
    json_path.write_text(
        json.dumps(
            {
                "segments": [
                    {"start": 0.0, "end": 1.25, "text": "Hola mundo", "speaker": "SPEAKER_00"},
                    {"start": 1.5, "end": 3.0, "text": "Second line"},
                ]
            }
        ),
        encoding="utf-8",
    )

    outputs = _write_transcript_outputs(json_path, tmp_path / "sample")

    txt = (tmp_path / "sample.txt").read_text(encoding="utf-8")
    srt = (tmp_path / "sample.srt").read_text(encoding="utf-8")
    vtt = (tmp_path / "sample.vtt").read_text(encoding="utf-8")

    assert outputs["segments"] == 2
    assert "SPEAKER_00: Hola mundo" in txt
    assert "00:00:00,000 --> 00:00:01,250" in srt
    assert vtt.startswith("WEBVTT")
    assert "00:00:01.500 --> 00:00:03.000" in vtt


def test_detects_whisper_checksum_mismatch():
    output = "Model has been downloaded but the SHA256 checksum does not match."

    assert _looks_like_whisper_checksum_error(output)


def test_clear_whisper_cache_only_removes_whisper_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    whisper_cache = tmp_path / ".cache" / "whisper"
    hf_cache = tmp_path / ".cache" / "huggingface" / "hub"
    whisper_cache.mkdir(parents=True)
    hf_cache.mkdir(parents=True)
    (whisper_cache / "large-v3.pt").write_text("corrupt", encoding="utf-8")
    (hf_cache / "model.bin").write_text("keep", encoding="utf-8")

    result = _clear_whisper_cache_dir()

    assert result["ok"] is True
    assert result["cleared"] is True
    assert result["path"] == str(whisper_cache)
    assert not whisper_cache.exists()
    assert (hf_cache / "model.bin").read_text(encoding="utf-8") == "keep"


def test_cookbook_state_preserves_encrypted_hf_token(tmp_path, monkeypatch):
    state_path = tmp_path / "cookbook_state.json"
    monkeypatch.setattr(cookbook_routes, "COOKBOOK_STATE_FILE", str(state_path))
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)

    app = FastAPI()
    app.include_router(setup_cookbook_routes())
    client = TestClient(app)

    encrypted = "enc:gAAAAABtest-token-with-fernet-characters=="
    response = client.post("/api/cookbook/state", json={"env": {"hfToken": encrypted}, "tasks": []})

    assert response.status_code == 200
    assert response.json()["ok"] is True
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["env"]["hfToken"] == encrypted


def test_cookbook_state_preserves_transcription_output_on_empty_client_sync(tmp_path, monkeypatch):
    state_path = tmp_path / "cookbook_state.json"
    monkeypatch.setattr(cookbook_routes, "COOKBOOK_STATE_FILE", str(state_path))
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)

    existing = {
        "tasks": [
            {
                "sessionId": "transcribe-abc12345",
                "type": "transcription",
                "status": "error",
                "progress": "failed",
                "output": "[odysseus] Whisper binary: /opt/homebrew/bin/whisper\nstderr body\n",
                "payload": {"_cmd": "/opt/homebrew/bin/whisper sample.m4a"},
            }
        ]
    }
    state_path.write_text(json.dumps(existing), encoding="utf-8")

    app = FastAPI()
    app.include_router(setup_cookbook_routes())
    client = TestClient(app)

    response = client.post(
        "/api/cookbook/state",
        json={
            "tasks": [
                {
                    "sessionId": "transcribe-abc12345",
                    "type": "transcription",
                    "status": "running",
                    "output": "",
                    "payload": {},
                }
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    task = saved["tasks"][0]
    assert task["status"] == "error"
    assert "stderr body" in task["output"]
    assert task["payload"]["_cmd"] == "/opt/homebrew/bin/whisper sample.m4a"


def test_cookbook_state_preserves_active_download_over_stale_crash(tmp_path, monkeypatch):
    state_path = tmp_path / "cookbook_state.json"
    monkeypatch.setattr(cookbook_routes, "COOKBOOK_STATE_FILE", str(state_path))
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)

    existing = {
        "tasks": [
            {
                "sessionId": "cookbook-abc12345",
                "type": "download",
                "status": "running",
                "progress": "downloading 62%",
                "output": "[odysseus] Active HuggingFace download process detected: PID 30164",
                "activePid": "30164",
                "downloadProgress": {"percent": 62, "downloaded_bytes": 14200000000},
            }
        ]
    }
    state_path.write_text(json.dumps(existing), encoding="utf-8")

    app = FastAPI()
    app.include_router(setup_cookbook_routes())
    client = TestClient(app)

    response = client.post(
        "/api/cookbook/state",
        json={
            "tasks": [
                {
                    "sessionId": "cookbook-abc12345",
                    "type": "download",
                    "status": "crashed",
                    "output": "Terminated: 15\nDownload attempt 1 failed",
                }
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    task = saved["tasks"][0]
    assert task["status"] == "running"
    assert task["_retrying"] is False
    assert task["activePid"] == "30164"
    assert task["downloadProgress"]["percent"] == 62
