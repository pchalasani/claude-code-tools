"""Claude Code Tools - Collection of utilities for Claude Code."""

from importlib import import_module
from types import ModuleType

__version__ = "1.24.0"
__all__ = ["action_rpc", "env_safe", "config"]


def __getattr__(name: str) -> ModuleType:
    """Load public modules when they are first accessed."""
    if name in __all__:
        module = import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Include lazy public modules in directory listings."""
    return sorted({*globals(), *__all__})
