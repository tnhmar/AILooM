"""Packaging and CI quality-gate tests — M6-T5 (8 tests)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

# Project root is two levels above this file: tests/packaging/ -> tests/ -> root
_ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# 1. create_app() exists and returns a FastAPI instance
# ---------------------------------------------------------------------------


def test_create_app_returns_fastapi_instance() -> None:
    from fastapi import FastAPI

    from memory_layer.api.app import create_app

    app = create_app()
    assert isinstance(app, FastAPI)


# ---------------------------------------------------------------------------
# 2. All declared package modules are importable without side effects
# ---------------------------------------------------------------------------


def test_package_modules_importable() -> None:
    modules = [
        "memory_layer",
        "memory_layer.config.settings",
        "memory_layer.config.loader",
        "memory_layer.observability.logging",
        "memory_layer.observability.tracing",
        "memory_layer.observability.metrics",
        "memory_layer.api.health",
    ]
    import importlib

    for mod in modules:
        importlib.import_module(mod)  # must not raise


# ---------------------------------------------------------------------------
# 3. src/memory_layer/__init__.py exposes __version__
# ---------------------------------------------------------------------------


def test_package_exposes_version() -> None:
    import memory_layer

    assert hasattr(memory_layer, "__version__")
    assert isinstance(memory_layer.__version__, str)
    assert len(memory_layer.__version__) > 0


# ---------------------------------------------------------------------------
# 4. entrypoint.sh is executable
# ---------------------------------------------------------------------------


def test_entrypoint_sh_is_executable() -> None:
    entrypoint = _ROOT / "scripts" / "entrypoint.sh"
    assert entrypoint.exists(), f"entrypoint.sh not found at {entrypoint}"
    mode = entrypoint.stat().st_mode
    assert mode & stat.S_IXUSR, "entrypoint.sh is not user-executable"


# ---------------------------------------------------------------------------
# 5. Dockerfile contains USER nobody
# ---------------------------------------------------------------------------


def test_dockerfile_contains_user_nobody() -> None:
    dockerfile = _ROOT / "Dockerfile"
    assert dockerfile.exists(), "Dockerfile not found"
    content = dockerfile.read_text()
    assert "USER nobody" in content, "Dockerfile must contain 'USER nobody'"


# ---------------------------------------------------------------------------
# 6. docker-compose.yml defines a memory-layer-api service
# ---------------------------------------------------------------------------


def test_docker_compose_defines_api_service() -> None:
    compose_file = _ROOT / "docker-compose.yml"
    assert compose_file.exists(), "docker-compose.yml not found"
    content = compose_file.read_text()
    assert "memory-layer-api" in content, (
        "docker-compose.yml must define a 'memory-layer-api' service"
    )


# ---------------------------------------------------------------------------
# 7. CI workflow defines test job with --cov-fail-under=90
# ---------------------------------------------------------------------------


def test_ci_workflow_has_coverage_gate() -> None:
    ci_file = _ROOT / ".github" / "workflows" / "ci.yml"
    assert ci_file.exists(), ".github/workflows/ci.yml not found"
    content = ci_file.read_text()
    assert "--cov-fail-under=90" in content, (
        "CI workflow must contain '--cov-fail-under=90'"
    )


# ---------------------------------------------------------------------------
# 8. CI workflow defines benchmark job gated to refs/heads/main
# ---------------------------------------------------------------------------


def test_ci_workflow_benchmark_gated_to_main() -> None:
    ci_file = _ROOT / ".github" / "workflows" / "ci.yml"
    assert ci_file.exists(), ".github/workflows/ci.yml not found"
    content = ci_file.read_text()
    assert "refs/heads/main" in content, (
        "CI workflow must gate the benchmark job to 'refs/heads/main'"
    )
    assert "benchmark" in content, (
        "CI workflow must define a benchmark job"
    )
