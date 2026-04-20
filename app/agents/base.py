from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)




class BaseAgent:
    """
    Abstract base for all AutoCS agents.

    Subclasses implement `run()`.  When an OpenAI API key is present the agent
    calls the LLM; otherwise it falls back to deterministic mock logic so the
    system works end-to-end without any external dependencies.
    """

    name: str = "BaseAgent"

    def __init__(self, config: Settings):
        self.config = config
        self._client = None

        self._is_gemini = bool(
            config.openai_base_url
            and "generativelanguage.googleapis.com" in config.openai_base_url
        )

        if config.use_llm:
            try:
                if not self._is_gemini:
                    import openai  # noqa: PLC0415
                    kwargs: Dict[str, Any] = {"api_key": config.openai_api_key}
                    if config.openai_base_url:
                        kwargs["base_url"] = config.openai_base_url
                    self._client = openai.OpenAI(**kwargs)

                logger.info(
                    "%s: LLM mode (model=%s%s)",
                    self.name,
                    config.openai_model,
                    " [Gemini direct]" if self._is_gemini else "",
                )
            except ImportError:
                logger.warning("%s: openai package not installed, falling back to mock", self.name)
        else:
            logger.info("%s: mock/simulation mode", self.name)

    # ── LLM helper ────────────────────────────────────────────────────────────

    def _call_llm(self, system_prompt: str, user_message: str) -> Dict[str, Any]:
        """Call the LLM and return a parsed JSON dict."""
        if self._is_gemini:
            return self._call_gemini(system_prompt, user_message)
        return self._call_openai(system_prompt, user_message)

    def _call_gemini(self, system_prompt: str, user_message: str) -> Dict[str, Any]:
        """
        Direct Gemini REST call — completely bypasses the openai SDK so
        there are zero auth conflicts with Google's API gateway.
        """
        model = self.config.openai_model  # e.g. "gemini-2.0-flash"
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models"
            f"/{model}:generateContent"
        )
        json_instruction = (
            "\n\nIMPORTANT: Respond with a single valid JSON object only. "
            "No explanation, no markdown fences — raw JSON only."
        )
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt + json_instruction}]},
            "contents": [{"role": "user", "parts": [{"text": user_message}]}],
            "generationConfig": {"temperature": 0.2},
        }
        start = time.monotonic()
        resp = httpx.post(
            url,
            headers={"x-goog-api-key": self.config.openai_api_key},
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.debug("%s Gemini call took %dms", self.name, elapsed_ms)
        content = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        return self._extract_json(content)

    def _call_openai(self, system_prompt: str, user_message: str) -> Dict[str, Any]:
        """OpenAI / OpenRouter call via the openai SDK."""
        if self._client is None:
            raise RuntimeError("LLM client not initialised")

        base_url = self.config.openai_base_url or ""
        use_json_mode = "openai.com" in base_url or base_url == ""
        json_system = (
            system_prompt
            + "\n\nIMPORTANT: Respond with a single valid JSON object only. "
              "No explanation, no markdown fences — raw JSON only."
        )
        start = time.monotonic()
        if use_json_mode:
            try:
                response = self._client.chat.completions.create(
                    model=self.config.openai_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.2,
                )
                content = response.choices[0].message.content
            except Exception:
                use_json_mode = False

        if not use_json_mode:
            response = self._client.chat.completions.create(
                model=self.config.openai_model,
                messages=[
                    {"role": "system", "content": json_system},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.2,
            )
            content = response.choices[0].message.content

        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.debug("%s OpenAI call took %dms", self.name, elapsed_ms)
        return self._extract_json(content)

    @staticmethod
    def _extract_json(text: str) -> Dict[str, Any]:
        """
        Parse JSON from a model response, stripping markdown fences if present.
        """
        text = text.strip()
        # Strip ```json ... ``` or ``` ... ``` fences
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        # Find the first { ... } block in case of extra prose
        start = text.find("{")
        end   = text.rfind("}")
        if start != -1 and end != -1:
            text = text[start:end+1]
        return json.loads(text)

    # ── Timing helper ─────────────────────────────────────────────────────────

    @staticmethod
    def _timer():
        return time.monotonic()

    @staticmethod
    def _elapsed_ms(start: float) -> int:
        return int((time.monotonic() - start) * 1000)
