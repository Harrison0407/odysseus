import json
from pathlib import Path

from routes.cookbook_routes import (
    _clear_whisper_cache_dir,
    _looks_like_whisper_checksum_error,
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
