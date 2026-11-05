"""Provider adapters. Importing this package registers every available one."""

from .base import (
    Capabilities,
    ParsedUsage,
    ProviderAdapter,
    ProviderInfo,
    all_adapters,
    get_adapter,
    register,
)
from . import claude, codex, openweight  # noqa: F401  (importing registers them)

__all__ = [
    "Capabilities",
    "ParsedUsage",
    "ProviderAdapter",
    "ProviderInfo",
    "all_adapters",
    "get_adapter",
    "register",
]
