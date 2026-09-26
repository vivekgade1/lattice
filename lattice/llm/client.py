"""Claude generation client: request shaping, response unpacking, code extraction."""

from __future__ import annotations

import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

_FENCE_RE = re.compile(r"```([^\n]*)\n(.*?)```", re.DOTALL)


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @property
    def total_input_tokens(self) -> int:
        """All prompt tokens billed for the call: raw + cache reads + cache writes."""
        return self.input_tokens + self.cache_read_input_tokens + self.cache_creation_input_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_input_tokens + other.cache_read_input_tokens,
            self.cache_creation_input_tokens + other.cache_creation_input_tokens,
        )


@dataclass(frozen=True)
class Generation:
    text: str
    usage: Usage
    latency_s: float
    stop_reason: str
    model: str = ""  # the model that actually served the request (audited by the gate)
    refusals: int = 0  # refused attempts that were retried before this answer


class GenerationRefused(Exception):
    """Raised when the model declines to answer (`stop_reason == "refusal"`).

    Carries the refused call's usage, latency and served model so callers can
    still account for the tokens it consumed.
    """

    def __init__(
        self,
        message: str,
        *,
        usage: Usage | None = None,
        latency_s: float = 0.0,
        model: str = "",
    ) -> None:
        super().__init__(message)
        self.usage = usage if usage is not None else Usage(0, 0)
        self.latency_s = latency_s
        self.model = model


class Generator(Protocol):
    def generate(self, system: str, messages: Sequence[Mapping[str, str]]) -> Generation: ...


class ClaudeGenerator:
    """Generator backed by the Claude messages endpoint.

    No server-side model fallback: the benchmark must compare arms on the same model,
    and a fallback silently rerouted Lattice-arm requests to another model in live
    testing. A refused call is retried up to `refusal_retries` times (identically for
    every arm); if every attempt is refused it surfaces as GenerationRefused."""

    def __init__(
        self,
        model: str = "claude-opus-5",
        max_tokens: int = 16000,
        effort: str = "high",
        client: Any | None = None,
        refusal_retries: int = 2,
    ) -> None:
        self.model = model
        self.refusal_retries = refusal_retries
        self.max_tokens = max_tokens
        self.effort = effort
        self._client = client

    @property
    def client(self) -> Any:
        """The underlying SDK client, constructed lazily if none was injected."""
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def generate(self, system: str, messages: Sequence[Mapping[str, str]]) -> Generation:
        spent = Usage(0, 0)
        latency = 0.0
        for attempt in range(self.refusal_retries + 1):
            try:
                result = self._generate_once(system, messages)
            except GenerationRefused as refused:
                spent, latency = spent + refused.usage, latency + refused.latency_s
                if attempt == self.refusal_retries:
                    refused.usage, refused.latency_s = spent, latency
                    raise
                continue
            return Generation(
                text=result.text,
                usage=spent + result.usage,
                latency_s=latency + result.latency_s,
                stop_reason=result.stop_reason,
                model=result.model,
                refusals=attempt,
            )
        raise AssertionError("unreachable")

    def _generate_once(self, system: str, messages: Sequence[Mapping[str, str]]) -> Generation:
        start = time.perf_counter()
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            output_config={"effort": self.effort},
            system=[
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=list(messages),
        )
        latency_s = time.perf_counter() - start
        served_model = getattr(response, "model", "") or ""
        if not isinstance(served_model, str):
            served_model = str(served_model)

        usage = Usage(
            input_tokens=getattr(response.usage, "input_tokens", 0) or 0,
            output_tokens=getattr(response.usage, "output_tokens", 0) or 0,
            cache_read_input_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=(
                getattr(response.usage, "cache_creation_input_tokens", 0) or 0
            ),
        )

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None)
            explanation = getattr(details, "explanation", None) or ""
            raise GenerationRefused(
                f"model refused to generate (category={category!r}): {explanation[:300]}",
                usage=usage,
                latency_s=latency_s,
                model=served_model,
            )

        text = "".join(block.text for block in response.content if block.type == "text")
        return Generation(
            text=text,
            usage=usage,
            latency_s=latency_s,
            stop_reason=response.stop_reason,
            model=served_model,
        )


class ScriptedGenerator:
    """Test double: replays canned texts in order and records every call."""

    def __init__(self, texts: Sequence[str]) -> None:
        self._texts = list(texts)
        self._index = 0
        self.calls: list[tuple[str, list[dict]]] = []

    def generate(self, system: str, messages: Sequence[Mapping[str, str]]) -> Generation:
        self.calls.append((system, list(messages)))
        if self._index >= len(self._texts):
            raise IndexError("ScriptedGenerator: no more scripted texts")
        text = self._texts[self._index]
        self._index += 1
        return Generation(text=text, usage=Usage(0, 0), latency_s=0.0, stop_reason="end_turn")


def extract_code(text: str) -> str | None:
    """First ```python`/```py fenced block; else the first bare fence; else None."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = [(info.strip().lower(), body) for info, body in _FENCE_RE.findall(normalized)]

    for info, body in blocks:
        if info in ("python", "py"):
            return body

    for info, body in blocks:
        if info == "":
            return body

    return None
