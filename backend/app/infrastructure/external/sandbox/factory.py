"""Sandbox provider factory."""

from __future__ import annotations

import logging
from typing import Type

from app.core.config import get_settings
import os
from app.domain.external.sandbox import Sandbox
from app.infrastructure.external.sandbox.docker_sandbox import DockerSandbox

logger = logging.getLogger(__name__)


def get_sandbox_class() -> Type[Sandbox]:
    """Return the sandbox implementation requested via configuration."""

    settings = get_settings()
    provider = (settings.sandbox_provider or "docker").lower()
    logger.info("Sandbox provider selected: %s", provider)

    if provider in {"docker", "local"}:
        # Auto-fallback: if Docker is selected but docker.sock is missing and E2B
        # credentials are available, switch to E2B to avoid hard failure in
        # cloud environments.
        if not os.path.exists("/var/run/docker.sock"):
            try:
                if settings.e2b_template_id and settings.e2b_api_key:
                    from app.infrastructure.external.sandbox.e2b_sandbox import (
                        E2BSandbox,
                    )
                    logger.info(
                        "Docker socket not found; falling back to E2B sandbox"
                    )
                    return E2BSandbox
            except Exception:
                # If import fails, continue with Docker and let it error as before
                logger.debug("E2B fallback import failed; keeping Docker provider")
        return DockerSandbox

    if provider == "e2b":
        try:
            from app.infrastructure.external.sandbox.e2b_sandbox import E2BSandbox
        except ImportError as exc:  # pragma: no cover - configuration error path
            raise RuntimeError(
                "E2B sandbox provider requested but e2b-code-interpreter is not installed"
            ) from exc
        logger.info("Using E2B sandbox implementation")
        return E2BSandbox

    logger.error("Unsupported sandbox provider configured: %s", settings.sandbox_provider)
    raise RuntimeError(f"Unsupported sandbox provider: {settings.sandbox_provider}")
