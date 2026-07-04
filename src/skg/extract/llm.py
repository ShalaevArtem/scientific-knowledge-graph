"""Подключаемый LLM-клиент: Anthropic, Yandex AI Studio или OpenAI-совместимый API.

Конфигурация через .env:
  LLM_PROVIDER = anthropic | openai | yandex   (openai = и любой совместимый: openrouter…;
                 Yandex доступен и как openai-совместимый эндпоинт, и нативно)
  LLM_MODEL    = claude-haiku-4-5-20251001 | gpt-4o-mini | yandexgpt-lite/latest | …
  LLM_API_KEY  = ключ
  LLM_FOLDER_ID = folder id для Yandex AI Studio (нативный провайдер)
  LLM_BASE_URL = переопределение базового URL (для совместимых провайдеров)

Клиент один метод: complete_json(system, user) → dict. Ответ принуждается к JSON
и валидируется вызывающей стороной (pydantic) — невалидный JSON повторяется один раз.
"""

from __future__ import annotations

import json
import os

import httpx
from dotenv import load_dotenv

load_dotenv()


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(
        self,
        provider: str,
        model: str,
        api_key: str,
        base_url: str | None = None,
        folder_id: str | None = None,
    ):
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.folder_id = folder_id
        if provider == "anthropic":
            self.base_url = base_url or "https://api.anthropic.com"
        elif provider == "openai":
            self.base_url = base_url or "https://api.openai.com/v1"
        elif provider == "yandex":
            self.base_url = base_url or "https://llm.api.cloud.yandex.net"
            if not self.folder_id and not self.model.startswith("gpt://"):
                raise LLMError("LLM_FOLDER_ID обязателен для Yandex AI Studio")
        else:
            raise LLMError(f"неизвестный провайдер: {provider}")
        self._http = httpx.Client(timeout=120.0)

    def complete_text(self, system: str, user: str, max_tokens: int = 2048) -> str:
        return self._complete_retry(system, user, max_tokens, json_mode=False)

    def _complete_retry(self, system: str, user: str, max_tokens: int, json_mode: bool) -> str:
        """Ретраи на лимиты/сбои API (429/5xx/сеть) с экспоненциальной паузой."""
        import time

        last: Exception | None = None
        for attempt in range(4):
            try:
                return self._complete(system, user, max_tokens, json_mode)
            except (httpx.TransportError, LLMError) as exc:
                msg = str(exc)
                retriable = isinstance(exc, httpx.TransportError) or any(
                    f" {code}" in msg[:20] for code in ("429", "500", "502", "503", "504")
                )
                if not retriable:
                    raise
                last = exc
                time.sleep(2**attempt)
        raise LLMError(f"после 4 попыток: {last}")

    def complete_json(self, system: str, user: str, max_tokens: int = 4096) -> dict:
        raw = self._complete_retry(system, user, max_tokens, json_mode=True)
        try:
            return _parse_json(raw)
        except ValueError:
            # одна повторная попытка с явным требованием чистого JSON
            raw = self._complete_retry(
                system, user + "\n\nОтветь СТРОГО валидным JSON без пояснений и markdown.",
                max_tokens, json_mode=True,
            )
            try:
                return _parse_json(raw)
            except ValueError as exc:
                raise LLMError(f"модель не вернула валидный JSON: {raw[:300]}") from exc

    def _complete(self, system: str, user: str, max_tokens: int, json_mode: bool) -> str:
        if self.provider == "anthropic":
            resp = self._http.post(
                f"{self.base_url}/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": self.model,
                    "max_tokens": max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                },
            )
            _raise_for_status(resp)
            return "".join(
                block["text"] for block in resp.json()["content"] if block["type"] == "text"
            )
        if self.provider == "yandex":
            resp = self._http.post(
                f"{self.base_url}/foundationModels/v1/completion",
                headers={"Authorization": f"Api-Key {self.api_key}"},
                json={
                    "modelUri": _yandex_model_uri(self.model, self.folder_id),
                    "completionOptions": {
                        "stream": False,
                        "temperature": 0.1 if json_mode else 0.2,
                        "maxTokens": str(max_tokens),
                    },
                    "messages": [
                        {"role": "system", "text": system},
                        {"role": "user", "text": user},
                    ],
                },
            )
            _raise_for_status(resp)
            return resp.json()["result"]["alternatives"][0]["message"]["text"]
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        resp = self._http.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
        )
        _raise_for_status(resp)
        return resp.json()["choices"][0]["message"]["content"]


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        raise LLMError(f"LLM API {resp.status_code}: {resp.text[:500]}")


def _parse_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text.removeprefix("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("нет JSON-объекта в ответе")
    return json.loads(text[start : end + 1])


def _yandex_model_uri(model: str, folder_id: str | None) -> str:
    if model.startswith("gpt://"):
        return model
    return f"gpt://{folder_id}/{model}"


def get_llm(role: str = "extract") -> LLMClient:
    """role="extract" — дешёвая модель для массового извлечения;
    role="synth" — модель для синтеза ответов (LLM_MODEL_SYNTH, по умолчанию та же)."""
    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key:
        raise LLMError(
            "LLM_API_KEY не задан в .env — извлечение и синтез ответов недоступны. "
            "Задайте LLM_PROVIDER / LLM_MODEL / LLM_API_KEY (и LLM_BASE_URL для совместимых API)."
        )
    model = os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")
    if role == "synth":
        model = os.environ.get("LLM_MODEL_SYNTH", model)
    return LLMClient(
        provider=os.environ.get("LLM_PROVIDER", "anthropic"),
        model=model,
        api_key=api_key,
        base_url=os.environ.get("LLM_BASE_URL") or None,
        folder_id=os.environ.get("LLM_FOLDER_ID") or None,
    )
