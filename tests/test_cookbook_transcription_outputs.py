import json

from routes.cookbook_routes import _write_transcript_outputs


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
