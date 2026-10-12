"""Builds the configured transcript post-processor (``post_process``).

Both providers share one interface — ``start()``, ``stop()``,
``wait_ready(timeout)``, ``ready``, ``clean(text, since)``,
``last_outcome`` and ``name`` — so the app and CLI never branch on the
provider beyond this factory.
"""

from __future__ import annotations

from collections.abc import Callable

from .config import Config
from .postprocess import DEFAULT_TIMEOUTS


def effective_timeout(cfg: Config) -> float:
    """``post_process_timeout``, or the provider's default when it is 0."""
    if cfg.post_process_timeout:
        return float(cfg.post_process_timeout)
    return DEFAULT_TIMEOUTS[cfg.post_process_provider]


def create_post_processor(
    cfg: Config,
    status: Callable[[str], None],
    keepalive_minutes: float | None = None,
):  # noqa: ANN201 - PostProcessor | CerebrasProcessor
    """Return an unstarted processor for ``cfg.post_process_provider``.

    Args:
        cfg: The validated config.
        status: Status-line sink.
        keepalive_minutes: Override for the S1-mini keepalive (the CLI
            passes 0); defaults to ``cfg.keepalive_minutes``.
    """
    timeout = effective_timeout(cfg)
    if cfg.post_process_provider == "cerebras":
        from .postprocess_cerebras import CerebrasProcessor

        return CerebrasProcessor(
            model=cfg.post_process_cerebras_model,
            fmt=cfg.post_process_format,
            timeout=timeout,
            status=status,
            log_text=cfg.post_process_log_text,
        )
    from .postprocess_server import PostProcessor

    return PostProcessor(
        style=cfg.post_process_style,
        structure=cfg.post_process_structure,
        context=cfg.post_process_context,
        timeout=timeout,
        model=cfg.post_process_model,
        server=cfg.post_process_server,
        keepalive_minutes=(
            cfg.keepalive_minutes
            if keepalive_minutes is None
            else keepalive_minutes
        ),
        status=status,
        log_text=cfg.post_process_log_text,
    )
