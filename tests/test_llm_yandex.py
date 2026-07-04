from __future__ import annotations

from skg.extract.llm import LLMClient, _yandex_model_uri


class FakeResponse:
    status_code = 200

    def __init__(self, payload: dict):
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


class FakeHTTP:
    def __init__(self):
        self.calls = []

    def post(self, url: str, headers: dict, json: dict):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return FakeResponse(
            {"result": {"alternatives": [{"message": {"text": "{\"ok\": true}"}}]}}
        )


def test_yandex_model_uri_builds_from_folder_id():
    assert (
        _yandex_model_uri("yandexgpt-lite/latest", "folder-1")
        == "gpt://folder-1/yandexgpt-lite/latest"
    )
    assert _yandex_model_uri("gpt://folder-1/custom/latest", None) == "gpt://folder-1/custom/latest"


def test_yandex_complete_json_uses_foundation_models_payload():
    client = LLMClient(
        provider="yandex",
        model="yandexgpt-lite/latest",
        api_key="secret",
        folder_id="folder-1",
    )
    fake_http = FakeHTTP()
    client._http = fake_http

    result = client.complete_json("system prompt", "user prompt")

    call = fake_http.calls[0]
    assert result == {"ok": True}
    assert call["url"].endswith("/foundationModels/v1/completion")
    assert call["headers"]["Authorization"] == "Api-Key secret"
    assert call["json"]["modelUri"] == "gpt://folder-1/yandexgpt-lite/latest"
    assert call["json"]["messages"][0] == {"role": "system", "text": "system prompt"}
    assert call["json"]["messages"][1] == {"role": "user", "text": "user prompt"}
