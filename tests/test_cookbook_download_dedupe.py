from routes.cookbook_routes import _download_key_from_task, _download_key_from_values


def test_download_key_includes_include_and_local_dir():
    base = _download_key_from_values(
        "unsloth/example-GGUF",
        "*Q4_K_M*",
        "gpu-box",
        "/models/hf/",
    )
    different_include = _download_key_from_values(
        "unsloth/example-GGUF",
        "*Q5_K_M*",
        "gpu-box",
        "/models/hf/",
    )
    different_dir = _download_key_from_values(
        "unsloth/example-GGUF",
        "*Q4_K_M*",
        "gpu-box",
        "/other/hf",
    )

    assert base != different_include
    assert base != different_dir


def test_download_key_from_task_matches_request_values():
    task = {
        "type": "download",
        "remoteHost": "gpu-box",
        "payload": {
            "repo_id": "unsloth/example-GGUF",
            "include": "*Q4_K_M*",
            "local_dir": "/models/hf/",
        },
    }

    assert _download_key_from_task(task) == _download_key_from_values(
        "unsloth/example-GGUF",
        "*Q4_K_M*",
        "gpu-box",
        "/models/hf/",
    )
