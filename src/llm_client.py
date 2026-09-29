from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx
from openai import AzureOpenAI

logger = logging.getLogger(__name__)

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class OpenAIConfig:
    api_version: str
    azure_openai_base_url: str
    deployment: str
    ca_bundle_path: str
    timeout_seconds: int = 60
    temperature: float = 0.0

    # Retry/backoff (defaults seguros)
    max_retries: int = 5                 # número de tentativas adicionais (além da 1ª)
    retry_base_seconds: float = 0.8      # base do backoff exponencial
    retry_max_seconds: float = 8.0       # teto do backoff


class LLMClient:
    def __init__(self, cfg: OpenAIConfig):
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY environment variable not set")

        http_client = httpx.Client(
            verify=cfg.ca_bundle_path,
            timeout=cfg.timeout_seconds,
        )

        self._client = AzureOpenAI(
            api_key=api_key,
            api_version=cfg.api_version,
            base_url=cfg.azure_openai_base_url,
            http_client=http_client,
        )

        self._model = cfg.deployment
        self._temperature = cfg.temperature

        self._max_retries = int(cfg.max_retries)
        self._retry_base_seconds = float(cfg.retry_base_seconds)
        self._retry_max_seconds = float(cfg.retry_max_seconds)

    def _sleep_backoff(self, attempt: int) -> None:
        """
        attempt: 1..N (1 = primeira repetição)
        backoff exponencial com jitter.
        """
        exp = self._retry_base_seconds * (2 ** (attempt - 1))
        jitter = random.uniform(0.0, 0.25 * exp)
        delay = min(self._retry_max_seconds, exp + jitter)
        time.sleep(delay)

    def _is_retryable_exception(self, exc: Exception) -> bool:
        # httpx: timeouts / conexão
        if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError)):
            return True

        # O SDK da OpenAI/Azure pode lançar erros próprios; como não sabemos exatamente
        # a classe no seu ambiente, fazemos um fallback por mensagem.
        msg = str(exc).lower()

        retryable_markers = [
            "429",
            "rate limit",
            "too many requests",
            "timeout",
            "timed out",
            "408",
            "500",
            "502",
            "503",
            "504",
            "service unavailable",
            "bad gateway",
            "gateway timeout",
            "temporarily unavailable",
            "connection reset",
        ]
        return any(m in msg for m in retryable_markers)

    def chat_json(self, system: str, user: str) -> dict[str, Any]:
        """
        Faz uma chamada ao LLM e garante retorno como objeto JSON (dict).
        Aplica retry/backoff em falhas transitórias.
        """
        last_exc: Exception | None = None

        # total_attempts = 1 + max_retries
        for attempt_idx in range(0, self._max_retries + 1):
            try:
                resp = self._client.chat.completions.create(
                    model=self._model,
                    temperature=self._temperature,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )

                text = (resp.choices[0].message.content or "").strip()

                # 1) JSON direto
                try:
                    obj = json.loads(text)
                    if isinstance(obj, dict):
                        return obj
                except Exception:
                    pass

                # 2) JSON “embrulhado” em texto
                m = _JSON_OBJECT_RE.search(text)
                if m:
                    try:
                        obj = json.loads(m.group(0))
                        if isinstance(obj, dict):
                            return obj
                    except Exception:
                        pass

                raise ValueError(f"LLM did not return valid JSON object. Raw output:\n{text}")

            except Exception as e:
                last_exc = e

                # Se não é erro transitório, falha imediatamente
                if not self._is_retryable_exception(e):
                    raise

                # Se acabou retries, propaga
                if attempt_idx >= self._max_retries:
                    raise

                # Retry
                retry_num = attempt_idx + 1
                logger.warning(
                    "Falha transitória no LLM (tentativa %d/%d). Erro: %s",
                    retry_num,
                    self._max_retries,
                    str(e),
                )
                self._sleep_backoff(retry_num)

        # Não deve chegar aqui
        if last_exc:
            raise last_exc
        raise RuntimeError("Unexpected error: retry loop ended without exception")
