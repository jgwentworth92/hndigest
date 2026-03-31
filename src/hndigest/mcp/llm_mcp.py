"""LLM adapter with pluggable provider support.

Supports Gemini, Claude, OpenAI, and local OpenAI-compatible endpoints.
Provider is selected via config/llm.yaml and API keys from environment
variables. All providers expose the same interface: generate_summary
and validate_summary. Transient HTTP errors (429, 500, 502, 503) are
retried with exponential backoff.
"""

from __future__ import annotations

import asyncio

import json
import logging
import os
from pathlib import Path
from typing import Any

import aiohttp
import yaml

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "llm.yaml"
_DEFAULT_PROMPTS_PATH = Path(__file__).resolve().parents[3] / "config" / "prompts.yaml"

# Environment variable names per provider
class LLMTransientError(RuntimeError):
    """Raised for retryable HTTP errors from LLM providers."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


# Environment variable names per provider
_ENV_KEYS: dict[str, str] = {
    "gemini": "GEMINI_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "local": "LOCAL_LLM_API_KEY",
}


class LLMAdapter:
    """Unified interface to multiple LLM providers.

    Loads configuration from config/llm.yaml, prompt templates from
    config/prompts.yaml, and reads API keys from environment variables.
    Provides generate_summary and validate_summary as the two tool
    operations used by the Summarizer and Validator agents.

    Args:
        config_path: Path to the LLM YAML config file.
        prompts_path: Path to the prompts YAML config file.
        provider_override: Override the provider from config (for testing).
    """

    def __init__(
        self,
        config_path: str | Path | None = None,
        prompts_path: str | Path | None = None,
        provider_override: str | None = None,
    ) -> None:
        path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
        with open(path, "r", encoding="utf-8") as fh:
            self._config: dict[str, Any] = yaml.safe_load(fh)

        prompts_file = Path(prompts_path) if prompts_path else _DEFAULT_PROMPTS_PATH
        with open(prompts_file, "r", encoding="utf-8") as fh:
            self._prompts: dict[str, Any] = yaml.safe_load(fh)

        logger.info("Loaded prompt templates from %s", prompts_file)

        self.provider = provider_override or os.environ.get(
            "LLM_PROVIDER", self._config.get("provider", "gemini")
        )
        self._provider_config: dict[str, Any] = self._config.get(self.provider, {})
        self.model: str = self._provider_config.get("model", "")
        self._base_url: str = self._provider_config.get("base_url", "")
        self._api_key: str = os.environ.get(_ENV_KEYS.get(self.provider, ""), "")
        self._max_tokens: int = self._config.get("max_tokens", 512)
        self._temperature: float = self._config.get("temperature", 0.3)
        self._timeout: int = self._config.get("timeout_seconds", 30)
        self._session: aiohttp.ClientSession | None = None

        # Retry settings for transient API errors
        retry_cfg = self._config.get("retry", {})
        self._max_retries: int = retry_cfg.get("max_retries", 3)
        self._base_delay: float = retry_cfg.get("base_delay_seconds", 1.0)
        self._backoff_factor: float = retry_cfg.get("backoff_factor", 2.0)
        self._retryable_codes: set[int] = set(
            retry_cfg.get("retryable_status_codes", [429, 500, 502, 503])
        )

        if not self._api_key and self.provider != "local":
            logger.warning(
                "No API key found for provider %s (env var: %s)",
                self.provider,
                _ENV_KEYS.get(self.provider, "?"),
            )

        logger.info(
            "LLMAdapter initialized: provider=%s, model=%s",
            self.provider,
            self.model,
        )

    async def _get_session(self) -> aiohttp.ClientSession:
        """Return or create the shared aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _call_llm(self, prompt: str, system_prompt: str = "") -> str:
        """Send a prompt to the configured LLM provider with retry on transient errors.

        Retries transient HTTP errors (429, 500, 502, 503) with exponential
        backoff. Non-transient errors fail immediately.

        Args:
            prompt: The user prompt to send.
            system_prompt: Optional system prompt for context.

        Returns:
            The text response from the LLM.

        Raises:
            RuntimeError: If the API call fails after all retries.
        """
        last_error: Exception | None = None

        for attempt in range(1 + self._max_retries):
            try:
                return await self._call_provider(prompt, system_prompt)
            except LLMTransientError as exc:
                last_error = exc
                if attempt < self._max_retries:
                    delay = self._base_delay * (self._backoff_factor ** attempt)
                    logger.warning(
                        "LLM transient error (HTTP %d), retrying in %.1fs "
                        "(attempt %d/%d): %s",
                        exc.status_code,
                        delay,
                        attempt + 1,
                        self._max_retries,
                        exc,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "LLM transient error (HTTP %d), all %d retries exhausted: %s",
                        exc.status_code,
                        self._max_retries,
                        exc,
                    )

        raise RuntimeError(
            f"LLM call failed after {self._max_retries} retries: {last_error}"
        ) from last_error

    async def _call_provider(self, prompt: str, system_prompt: str) -> str:
        """Dispatch to the configured provider. Raises LLMTransientError for retryable errors."""
        if self.provider == "gemini":
            return await self._call_gemini(prompt, system_prompt)
        elif self.provider == "claude":
            return await self._call_claude(prompt, system_prompt)
        elif self.provider in ("openai", "local"):
            return await self._call_openai_compatible(prompt, system_prompt)
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider}")

    def _raise_for_status(self, provider: str, status: int, body: str) -> None:
        """Raise LLMTransientError for retryable codes, RuntimeError otherwise."""
        msg = f"{provider} API error {status}: {body[:500]}"
        if status in self._retryable_codes:
            raise LLMTransientError(status, msg)
        raise RuntimeError(msg)

    async def _call_gemini(self, prompt: str, system_prompt: str) -> str:
        """Call the Google Gemini API.

        Args:
            prompt: The user prompt.
            system_prompt: System instruction text.

        Returns:
            Generated text from Gemini.
        """
        session = await self._get_session()
        url = (
            f"{self._base_url}/models/{self.model}:generateContent"
            f"?key={self._api_key}"
        )

        body: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": self._max_tokens,
                "temperature": self._temperature,
            },
        }
        if system_prompt:
            body["systemInstruction"] = {
                "parts": [{"text": system_prompt}]
            }

        timeout = aiohttp.ClientTimeout(total=self._timeout)
        async with session.post(url, json=body, timeout=timeout) as resp:
            if resp.status != 200:
                text = await resp.text()
                self._raise_for_status("Gemini", resp.status, text)
            data = await resp.json()

        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"Unexpected Gemini response structure: {exc}") from exc

    async def _call_claude(self, prompt: str, system_prompt: str) -> str:
        """Call the Anthropic Claude API.

        Args:
            prompt: The user prompt.
            system_prompt: System instruction text.

        Returns:
            Generated text from Claude.
        """
        session = await self._get_session()
        url = f"{self._base_url}/messages"

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            body["system"] = system_prompt

        timeout = aiohttp.ClientTimeout(total=self._timeout)
        async with session.post(url, json=body, headers=headers, timeout=timeout) as resp:
            if resp.status != 200:
                text = await resp.text()
                self._raise_for_status("Claude", resp.status, text)
            data = await resp.json()

        try:
            return data["content"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"Unexpected Claude response structure: {exc}") from exc

    async def _call_openai_compatible(self, prompt: str, system_prompt: str) -> str:
        """Call an OpenAI-compatible API (OpenAI, local endpoints).

        Args:
            prompt: The user prompt.
            system_prompt: System instruction text.

        Returns:
            Generated text from the endpoint.
        """
        session = await self._get_session()
        url = f"{self._base_url}/chat/completions"

        headers: dict[str, str] = {"content-type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "messages": messages,
        }

        timeout = aiohttp.ClientTimeout(total=self._timeout)
        async with session.post(url, json=body, headers=headers, timeout=timeout) as resp:
            if resp.status != 200:
                text = await resp.text()
                self._raise_for_status("OpenAI", resp.status, text)
            data = await resp.json()

        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise RuntimeError(
                f"Unexpected OpenAI response structure: {exc}"
            ) from exc

    @staticmethod
    def _parse_json_response(text: str) -> dict[str, Any] | None:
        """Extract and parse a JSON object from an LLM response.

        Handles responses that are pure JSON, wrapped in markdown code
        fences, or have trailing text after the JSON block.

        Args:
            text: Raw LLM response text.

        Returns:
            Parsed dict, or None if no valid JSON could be extracted.
        """
        import re

        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Strip markdown code fences
        fenced = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if fenced:
            try:
                return json.loads(fenced.group(1))
            except json.JSONDecodeError:
                pass

        # Find the outermost balanced braces
        start = text.find("{")
        if start == -1:
            logger.error("No JSON object found in response: %s", text[:200])
            return None

        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break

        logger.error("Failed to parse JSON from response: %s", text[:200])
        return None

    async def generate_summary(self, article_text: str, title: str) -> str:
        """Generate a 2-3 sentence summary of an article.

        Prompt templates are loaded from config/prompts.yaml at init time.

        Args:
            article_text: The extracted article text.
            title: The story title for context.

        Returns:
            A 2-3 sentence summary.
        """
        max_chars = self._prompts.get("max_article_chars", 8000)
        templates = self._prompts["summarizer"]
        system_prompt = templates["system"].strip()
        prompt = templates["user"].format(
            title=title,
            article_text=article_text[:max_chars],
        ).strip()

        result = await self._call_llm(prompt, system_prompt)
        logger.info("Generated summary for '%s' (%d chars)", title[:60], len(result))
        return result.strip()

    async def validate_summary(self, summary: str, source_text: str) -> dict[str, Any]:
        """Validate that a summary is faithful to its source article.

        Prompt templates are loaded from config/prompts.yaml at init time.

        Args:
            summary: The generated summary to validate.
            source_text: The original article text.

        Returns:
            A dict with "result" ("pass" or "fail") and "details" (list
            of per-claim checks with citations).
        """
        max_chars = self._prompts.get("max_article_chars", 8000)
        templates = self._prompts["validator"]
        system_prompt = templates["system"].strip()
        prompt = templates["user"].format(
            summary=summary,
            source_text=source_text[:max_chars],
        ).strip()

        result_text = await self._call_llm(prompt, system_prompt)

        parsed = self._parse_json_response(result_text)
        if parsed is None:
            return {"result": "fail", "details": [{"error": "unparseable response"}]}

        logger.info("Validation result: %s", parsed.get("result", "unknown"))
        return {
            "result": parsed.get("result", "fail"),
            "details": parsed.get("claims", []),
        }
