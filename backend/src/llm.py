"""The single model gateway: every LLM and embedding call in RAIA goes through this module.

Which provider serves the calls, and which model serves each tier, is config (config/llm.yaml);
no other module imports a provider SDK. Responses are cached on disk by a hash of the whole request,
so re-running a pipeline stage never pays twice for the same call.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections import defaultdict
from functools import cache
from pathlib import Path
from typing import Literal, TypeVar

import anthropic
import openai
import yaml
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from pydantic import BaseModel

from src.settings import get_settings

Tier = Literal["cheap", "mid", "expensive"]
Effort = Literal["low", "medium", "high"]
T = TypeVar("T", bound=BaseModel)

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """The model returned no usable answer: a refusal, or output cut off at max_tokens."""


class _Refusal(LLMError):
    pass


class _Unavailable(LLMError):
    """The provider won't serve this model to us (no access, unknown model, or still rate-limited after retries)."""

    def __init__(self, message: str, permanent: bool):
        super().__init__(message)
        self.permanent = permanent


class ProviderConfig(BaseModel):
    sdk: Literal["anthropic", "openai"]
    base_url: str | None = None
    api_key_env: str
    tiers: dict[Tier, list[str]]
    max_concurrency: int = 6


class EmbeddingConfig(BaseModel):
    base_url: str | None = None
    api_key_env: str
    model: str


class LLMConfig(BaseModel):
    provider: str
    providers: dict[str, ProviderConfig]
    embeddings: EmbeddingConfig

    @property
    def active(self) -> ProviderConfig:
        return self.providers[self.provider]


@cache
def config() -> LLMConfig:
    return LLMConfig.model_validate(yaml.safe_load((get_settings().config_dir / "llm.yaml").read_text()))


def model_for(tier: Tier) -> str:
    """The model that currently serves `tier`: its first model that hasn't been found unavailable."""
    models = config().active.tiers[tier]
    return next((m for m in models if m not in _unavailable), models[-1])


# Models the provider refused to serve at all ("no access", unknown model); skipped for the rest of the process.
_unavailable: set[str] = set()


# Token usage per model since the process started, for cost reporting at the end of a run. Answers
# served from the disk cache are counted per tier: the cache does not record which model wrote them.
usage: dict[str, dict[str, int]] = defaultdict(lambda: {"calls": 0, "input": 0, "output": 0})
cache_hits: dict[str, int] = defaultdict(int)


async def structured(
    tier: Tier, system: str, prompt: str, output: type[T], *, max_tokens: int = 16000, effort: Effort | None = None
) -> T:
    """Ask the tier's model for a response that validates against the Pydantic model `output`."""
    result = await _call(tier, system, prompt, output, max_tokens, effort)
    return output.model_validate(result)


async def text(tier: Tier, system: str, prompt: str, *, max_tokens: int = 8000, effort: Effort | None = None) -> str:
    """Ask the tier's model for plain text."""
    return await _call(tier, system, prompt, None, max_tokens, effort)


async def embed(texts: list[str]) -> list[list[float]]:
    """Embed texts, caching each vector on disk."""
    cfg = config().embeddings
    keys = [_cache_file({"embed": cfg.model, "text": t}) for t in texts]
    missing = [i for i, key in enumerate(keys) if not key.exists()]
    client = _openai_client(cfg.api_key_env, cfg.base_url)
    for start in range(0, len(missing), 256):
        batch = missing[start : start + 256]
        response = await client.embeddings.create(model=cfg.model, input=[texts[i] for i in batch])
        for i, item in zip(batch, response.data):
            _write_cache(keys[i], item.embedding)
    return [json.loads(key.read_text()) for key in keys]


async def _call(tier: Tier, system: str, prompt: str, output: type[BaseModel] | None, max_tokens: int, effort: Effort | None):
    cfg = config().active
    models = cfg.tiers[tier]
    schema = output.model_json_schema() if output else None
    key = _cache_file({"sdk": cfg.sdk, "models": models, "system": system, "prompt": prompt, "schema": schema,
                       "max_tokens": max_tokens, "effort": effort})
    if key.exists():
        cache_hits[tier] += 1
        return json.loads(key.read_text())

    errors = []
    async with _semaphore(config().provider):
        for model in [m for m in models if m not in _unavailable]:
            try:
                result = await _generate(cfg, model, system, prompt, output, max_tokens, effort)
                break
            except _Unavailable as e:
                if e.permanent:
                    _unavailable.add(model)
                errors.append(f"{model}: {e}")
            except _Refusal as e:
                errors.append(f"{model}: {e}")
            log.warning("%s could not answer (%s tier: %s); trying the next model", model, tier, errors[-1][:160])
        else:
            raise LLMError(f"no {tier}-tier model could answer: {errors}")
    _write_cache(key, result)
    return result


async def _generate(cfg: ProviderConfig, model: str, system: str, prompt: str, output, max_tokens: int, effort):
    """One request to the provider; returns plain text, or the parsed output as JSON-compatible data."""
    try:
        return await _request(cfg, model, system, prompt, output, max_tokens, effort)
    except (anthropic.NotFoundError, anthropic.PermissionDeniedError, openai.NotFoundError, openai.PermissionDeniedError) as e:
        raise _Unavailable(str(e)[:300], permanent=True) from e
    except (anthropic.RateLimitError, openai.RateLimitError) as e:
        raise _Unavailable(str(e)[:300], permanent="No access to this model" in str(e)) from e


async def _request(cfg: ProviderConfig, model: str, system: str, prompt: str, output, max_tokens: int, effort):
    if cfg.sdk == "anthropic":
        client = _anthropic_client(cfg.api_key_env, cfg.base_url)
        kwargs = {"output_config": {"effort": effort}} if effort and not model.startswith("claude-haiku") else {}
        messages = [{"role": "user", "content": prompt}]
        if output:
            response = await client.messages.parse(
                model=model, max_tokens=max_tokens, system=system, messages=messages, output_format=output, **kwargs
            )
        else:
            response = await client.messages.create(model=model, max_tokens=max_tokens, system=system, messages=messages, **kwargs)
        _record(model, response.usage.input_tokens, response.usage.output_tokens)
        if response.stop_reason == "refusal":
            raise _Refusal(f"{model} refused: {response.stop_details}")
        if response.stop_reason == "max_tokens":
            raise LLMError(f"{model} hit max_tokens={max_tokens}")
        if output:
            return response.parsed_output.model_dump(mode="json")
        return "".join(block.text for block in response.content if block.type == "text")

    client = _openai_client(cfg.api_key_env, cfg.base_url)
    kwargs = {"reasoning_effort": effort} if effort else {}
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    if output:
        response = await client.chat.completions.parse(
            model=model, messages=messages, response_format=output, max_completion_tokens=max_tokens, **kwargs
        )
    else:
        response = await client.chat.completions.create(model=model, messages=messages, max_completion_tokens=max_tokens, **kwargs)
    _record(model, response.usage.prompt_tokens, response.usage.completion_tokens)
    choice = response.choices[0]
    if choice.message.refusal:
        raise _Refusal(f"{model} refused: {choice.message.refusal}")
    if choice.finish_reason == "length":
        raise LLMError(f"{model} hit max_tokens={max_tokens}")
    return choice.message.parsed.model_dump(mode="json") if output else choice.message.content


def _record(model: str, input_tokens: int, output_tokens: int) -> None:
    usage[model]["calls"] += 1
    usage[model]["input"] += input_tokens
    usage[model]["output"] += output_tokens


# Clients and semaphores bind to the event loop that first uses them, so keep one per loop.
_per_loop: dict[tuple, object] = {}


def _loop_local(key: tuple, factory):
    key = (*key, id(asyncio.get_running_loop()))
    if key not in _per_loop:
        _per_loop[key] = factory()
    return _per_loop[key]


def _api_key(env_name: str) -> str:
    if not os.environ.get(env_name):
        raise LLMError(f"{env_name} is not set (in the environment or backend/.env)")
    return os.environ[env_name]


def _anthropic_client(api_key_env: str, base_url: str | None) -> AsyncAnthropic:
    return _loop_local(
        ("anthropic", api_key_env, base_url),
        lambda: AsyncAnthropic(api_key=_api_key(api_key_env), base_url=base_url, max_retries=4),
    )


def _openai_client(api_key_env: str, base_url: str | None) -> AsyncOpenAI:
    return _loop_local(
        ("openai", api_key_env, base_url),
        lambda: AsyncOpenAI(api_key=_api_key(api_key_env), base_url=base_url, max_retries=4),
    )


def _semaphore(provider: str) -> asyncio.Semaphore:
    return _loop_local(("semaphore", provider), lambda: asyncio.Semaphore(config().providers[provider].max_concurrency))


def _cache_file(request: dict) -> Path:
    digest = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return get_settings().cache_dir / "llm" / digest[:2] / f"{digest}.json"


def _write_cache(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False))
    tmp.replace(path)


async def _check() -> None:
    class ElectionDate(BaseModel):
        event: str
        date_iso: str

    prompt = "Extract the event and date: 'INEC said the presidential election will hold on Saturday, 16 January 2027.'"
    for tier in ("cheap", "mid", "expensive"):
        answer = await structured(tier, "Extract facts exactly as stated.", prompt, ElectionDate, max_tokens=4000)
        print(f"{tier:9} {model_for(tier):18} -> {answer}")
    await structured("cheap", "Extract facts exactly as stated.", prompt, ElectionDate, max_tokens=4000)
    vectors = await embed(["INEC fixes January 16 for the 2027 presidential poll"])
    print(f"embedding: {len(vectors[0])} dims from {config().embeddings.model}")
    print("usage:", dict(usage))


if __name__ == "__main__":
    asyncio.run(_check())
