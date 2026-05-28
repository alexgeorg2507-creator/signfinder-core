"""signfinder.llm — мульти-провайдерный LLM-слой (v1.10)."""
from signfinder.llm.anthropic_client import AnthropicClient, DEFAULT_MODEL
from signfinder.llm.base import LLMClient, LLMError
from signfinder.llm.config import (
    configured_providers,
    get_active_provider,
    get_api_key,
    load_config,
    mask_key,
    save_config,
    SUPPORTED_PROVIDERS,
)
from signfinder.llm.factory import available_providers, create_client

__all__ = [
    "LLMClient",
    "LLMError",
    "AnthropicClient",
    "DEFAULT_MODEL",
    "create_client",
    "available_providers",
    "load_config",
    "save_config",
    "get_active_provider",
    "get_api_key",
    "configured_providers",
    "mask_key",
    "SUPPORTED_PROVIDERS",
]
