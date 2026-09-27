"""Shared runtime env helpers: .env load, browser policy, LLM key hints."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable, Optional


logger = logging.getLogger(__name__)

_DOTENV_LOADED = False

LLM_KEY_NAMES = (
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "SHADOW_WEB_LLM_API_KEY",
)


def load_dotenv_files(extra_paths: Optional[Iterable[Path]] = None) -> list[str]:
    """Load KEY=VALUE from an explicit path or the repository root into os.environ.

    Only two sources are trusted: paths passed explicitly (``extra_paths`` /
    ``--env-file``) and the repository root found by walking up from this
    package (a ``pyproject.toml`` sitting next to ``.env``). The current working
    directory is deliberately NOT a candidate: running ``shadow-web exec``
    inside an untrusted checkout must not let that checkout's ``.env`` redirect
    the LLM endpoint or swap in an attacker's API key.

    Safe no-op if files missing. Does not require python-dotenv.
    Returns list of paths that contributed at least one new key.
    """
    global _DOTENV_LOADED
    if _DOTENV_LOADED and extra_paths is None:
        return []
    candidates: list[Path] = []
    if extra_paths:
        candidates.extend(Path(p) for p in extra_paths)
    # Repo root when running from src layout / editable install
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists() and (parent / ".env").exists():
            candidates.append(parent / ".env")
            break

    loaded: list[str] = []
    seen: set[Path] = set()
    for path in candidates:
        try:
            path = path.resolve()
        except OSError:
            continue
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        if _apply_dotenv(path):
            loaded.append(str(path))
    if loaded:
        logger.info("shadow-web: loaded env file(s): %s", ", ".join(loaded))
    if extra_paths is None:
        _DOTENV_LOADED = True
    _warn_if_cwd_dotenv_ignored(seen)
    return loaded


def _warn_if_cwd_dotenv_ignored(loaded: set[Path]) -> None:
    """Make a would-be cwd hijack visible instead of silently obeying or ignoring it."""
    try:
        cwd_env = (Path.cwd() / ".env").resolve()
    except OSError:
        return
    if cwd_env.is_file() and cwd_env not in loaded:
        logger.warning(
            "shadow-web: ignoring %s - a .env in the current working directory is "
            "not loaded; pass it via --env-file to use it explicitly",
            cwd_env,
        )


def _apply_dotenv(path: Path) -> bool:
    contributed = False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ[key] = value
        contributed = True
    return contributed


def llm_api_key_present() -> bool:
    return any(os.environ.get(name) for name in LLM_KEY_NAMES)


def missing_llm_key_message() -> str:
    return (
        "No LLM API key in this process. "
        "Set DEEPSEEK_API_KEY / OPENAI_API_KEY / OPENROUTER_API_KEY, "
        "or pass --api-key (Hermes/tool runners often strip secrets from child env). "
        "shadow-web also loads .env from the project root (next to pyproject.toml); "
        "a .env in the current working directory is not loaded."
    )


def allow_chromium_fallback() -> bool:
    """Silent Camoufox→Chromium fallback is opt-in (prevents anti-bot incidents)."""
    raw = (os.environ.get("SHADOW_WEB_ALLOW_CHROMIUM_FALLBACK") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def preferred_browser_engine(default: str = "camoufox") -> str:
    return (os.environ.get("SHADOW_WEB_BROWSER") or default).strip().lower() or default


def camoufox_unavailable_message(exc: BaseException) -> str:
    return (
        f"Camoufox unavailable ({exc}). "
        "Install/fetch: pip install camoufox && camoufox fetch. "
        "Restart MCP after install. "
        "To allow silent Chromium fallback (weaker anti-bot): "
        "SHADOW_WEB_ALLOW_CHROMIUM_FALLBACK=1"
    )
