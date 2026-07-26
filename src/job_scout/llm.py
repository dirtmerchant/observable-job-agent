"""Chat-model factory and the per-run LLM call budget.

The call budget is a simple circuit breaker: every node reads the running
``llm_calls`` counter from state, checks it against ``MAX_LLM_CALLS_PER_RUN``
before calling the model, and returns the incremented total. The graph runs
sequentially, so returning the cumulative total (not a delta) keeps the counter
correct.
"""

from __future__ import annotations

import os
from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from job_scout.config import get_settings


class LLMBudgetExceededError(RuntimeError):
    """Raised when a run would exceed ``MAX_LLM_CALLS_PER_RUN``."""


def _export_anthropic_key() -> None:
    """Copy the Anthropic key from settings into the environment for LangChain."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    key = get_settings().anthropic_api_key.get_secret_value()
    if key:
        os.environ["ANTHROPIC_API_KEY"] = key


def _export_openai_key() -> None:
    """Copy the OpenAI key from settings into the environment for LangChain.

    ``pydantic-settings`` reads ``.env`` into the ``Settings`` object but does not
    export to ``os.environ``, which is where the OpenAI client looks for its key.
    """
    if os.environ.get("OPENAI_API_KEY"):
        return
    key = get_settings().openai_api_key.get_secret_value()
    if key:
        os.environ["OPENAI_API_KEY"] = key


def _export_ollama_host() -> None:
    """Copy ``OLLAMA_HOST`` from settings into the environment for ``langchain-ollama``.

    The Ollama client reads the base URL from ``OLLAMA_HOST``. Like the OpenAI
    key, ``pydantic-settings`` keeps it in the ``Settings`` object but doesn't
    export it to ``os.environ``.
    """
    if os.environ.get("OLLAMA_HOST"):
        return
    host = get_settings().ollama_host
    if host:
        os.environ["OLLAMA_HOST"] = host


@lru_cache(maxsize=8)
def get_chat_model(model: str, temperature: float = 0.0) -> BaseChatModel:
    """Return a cached chat model for a LangChain provider string (e.g. ``openai:gpt-4o-mini``)."""
    if model.startswith("anthropic:"):
        _export_anthropic_key()
    elif model.startswith("openai:"):
        _export_openai_key()
    elif model.startswith("ollama:"):
        _export_ollama_host()
    return init_chat_model(model, temperature=temperature)


def ensure_budget(current_calls: int, planned: int, max_calls: int) -> None:
    """Raise ``LLMBudgetExceededError`` if ``planned`` more calls would exceed ``max_calls``."""
    if current_calls + planned > max_calls:
        raise LLMBudgetExceededError(
            f"Run would make {current_calls + planned} LLM calls, exceeding MAX_LLM_CALLS_PER_RUN={max_calls}."
        )
