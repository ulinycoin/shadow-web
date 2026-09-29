"""Per-session MCP browser state: registry, session keys, and per-session locks.

Hermetic: nothing here launches a browser or touches the network. Browser
resources are only ever represented by plain sentinel objects, so the close
helpers have nothing real to await.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

from shadow_web.mcp import server as mcp_server

DEFAULT_KEY = mcp_server._DEFAULT_SESSION_KEY

# The browser-touching MCP tools that must serialise on their own session lock.
BROWSER_TOOLS = (
    "navigate",
    "snapshot",
    "click",
    "fill",
    "web_search",
    "shadow_query",
    "query_page",
    "schema_session",
    "schema_session_json",
    "schema_session_csv",
    "content_outline",
    "content_blocks",
    "form_fill_plan",
    "form_fill_execute",
)


@pytest.fixture(autouse=True)
def _isolate_mcp_globals():
    """Snapshot the module registries so no session state leaks between tests."""
    sessions = dict(mcp_server._SESSIONS)
    locks = dict(mcp_server._SESSION_LOCKS)
    identities = dict(mcp_server._SESSION_IDENTITIES)
    app = mcp_server._mcp_app
    legacy = mcp_server._session
    yield
    mcp_server._SESSIONS.clear()
    mcp_server._SESSIONS.update(sessions)
    # The default key must stay bound to the module-level ``_session`` alias.
    mcp_server._SESSIONS[DEFAULT_KEY] = legacy
    mcp_server._SESSION_LOCKS.clear()
    mcp_server._SESSION_LOCKS.update(locks)
    mcp_server._SESSION_IDENTITIES.clear()
    mcp_server._SESSION_IDENTITIES.update(identities)
    mcp_server._mcp_app = app


@pytest.fixture
def session_key(monkeypatch):
    """Point _current_session_key() at a switchable fake key."""
    holder = {"key": "session-a"}
    monkeypatch.setattr(mcp_server, "_current_session_key", lambda: holder["key"])
    return holder


class _FakeContext:
    def __init__(self, session):
        self._session = session

    @property
    def session(self):
        if isinstance(self._session, Exception):
            raise self._session
        return self._session


class _FakeApp:
    def __init__(self, context=None, error=None):
        self._context = context
        self._error = error

    def get_context(self):
        if self._error is not None:
            raise self._error
        return self._context


# ---------------------------------------------------------------------------
# _sess(): one stable state dict per session key
# ---------------------------------------------------------------------------


def test_sess_returns_one_stable_dict_per_key(session_key):
    session_key["key"] = "session-a"
    first = mcp_server._sess()
    first["marker"] = 1
    assert mcp_server._sess() is first

    session_key["key"] = "session-b"
    other = mcp_server._sess()
    assert other is not first
    assert other == {}
    assert mcp_server._sess() is other

    session_key["key"] = "session-a"
    assert mcp_server._sess() is first
    assert first["marker"] == 1


def test_default_key_uses_the_module_level_session_alias(monkeypatch):
    monkeypatch.setattr(mcp_server, "_mcp_app", None)
    legacy = {}
    monkeypatch.setattr(mcp_server, "_session", legacy)

    assert mcp_server._current_session_key() == DEFAULT_KEY
    assert mcp_server._sess() is legacy


# ---------------------------------------------------------------------------
# Isolation: state written by one session must not exist for another
# ---------------------------------------------------------------------------


def test_state_under_session_a_is_invisible_to_session_b(session_key):
    session_key["key"] = "session-a"
    page = object()
    mcp_server._sess()["shadow_page"] = page
    mcp_server._sess()["clean_html"] = "<h1>A</h1>"
    assert mcp_server._get_shadow_page() is page

    session_key["key"] = "session-b"
    with pytest.raises(RuntimeError, match="No browser session"):
        mcp_server._get_shadow_page()

    # ...and B cannot see A's captured HTML either.
    session_key["key"] = "session-b"
    with pytest.raises(RuntimeError, match="navigate"):
        mcp_server._get_content_index_blocks()


def test_closing_session_b_leaves_session_a_intact(session_key):
    session_key["key"] = "session-a"
    a_state = mcp_server._sess()
    a_page = object()
    a_state["shadow_page"] = a_page
    a_state["clean_html"] = "<h1>A</h1>"

    session_key["key"] = "session-b"
    b_state = mcp_server._sess()
    b_state["shadow_page"] = object()
    b_state["clean_html"] = "<h1>B</h1>"

    asyncio.run(mcp_server._close_browser_resources())

    # B was reset (no browser objects involved, so nothing was awaited)...
    assert "shadow_page" not in b_state
    assert "clean_html" not in b_state
    assert mcp_server._sess() is b_state

    # ...while A's page survived untouched.
    assert a_state["shadow_page"] is a_page
    assert a_state["clean_html"] == "<h1>A</h1>"
    session_key["key"] = "session-a"
    assert mcp_server._get_shadow_page() is a_page


def test_close_all_sessions_clears_every_registered_session(session_key):
    session_key["key"] = "session-a"
    a_state = mcp_server._sess()
    a_state["clean_html"] = "<h1>A</h1>"
    session_key["key"] = "session-b"
    b_state = mcp_server._sess()
    b_state["clean_html"] = "<h1>B</h1>"
    assert {k for k in mcp_server._SESSIONS if k != DEFAULT_KEY} == {
        "session-a",
        "session-b",
    }

    asyncio.run(mcp_server._close_all_sessions())

    assert "clean_html" not in a_state
    assert "clean_html" not in b_state
    assert {k for k in mcp_server._SESSIONS if k != DEFAULT_KEY} == set()


def test_close_all_sessions_forgets_identities_and_locks(session_key):
    session_obj = object()
    session_key["key"] = "session-a"
    mcp_server._SESSION_IDENTITIES[id(session_obj)] = (session_obj, "session-a")
    mcp_server._SESSION_LOCKS["session-a"] = asyncio.Lock()
    mcp_server._sess()["clean_html"] = "<h1>A</h1>"

    asyncio.run(mcp_server._close_all_sessions())

    assert id(session_obj) not in mcp_server._SESSION_IDENTITIES
    assert "session-a" not in mcp_server._SESSION_LOCKS


def test_session_cap_evicts_the_oldest_idle_session(session_key, monkeypatch):
    monkeypatch.setattr(mcp_server, "_MCP_MAX_SESSIONS", 2)
    states = {}
    for name in ("session-a", "session-b", "session-c"):
        session_key["key"] = name
        states[name] = mcp_server._sess()
        states[name]["clean_html"] = f"<h1>{name}</h1>"
        mcp_server._SESSION_LOCKS[name] = asyncio.Lock()

    session_key["key"] = "session-d"
    mcp_server._sess()["clean_html"] = "<h1>session-d</h1>"

    asyncio.run(mcp_server._enforce_session_cap())

    remaining = {k for k in mcp_server._SESSIONS if k != DEFAULT_KEY}
    assert "session-a" not in remaining
    assert "session-c" in remaining and "session-d" in remaining
    assert "clean_html" not in states["session-a"]


def test_session_cap_never_evicts_a_busy_session(session_key, monkeypatch):
    monkeypatch.setattr(mcp_server, "_MCP_MAX_SESSIONS", 1)
    for name in ("session-a", "session-b"):
        session_key["key"] = name
        mcp_server._sess()

    busy = asyncio.Lock()
    asyncio.run(busy.acquire())
    mcp_server._SESSION_LOCKS["session-a"] = busy
    mcp_server._SESSION_LOCKS["session-b"] = asyncio.Lock()

    asyncio.run(mcp_server._enforce_session_cap())

    # session-a is mid-call, so the idle session-b goes instead.
    assert "session-a" in mcp_server._SESSIONS
    assert "session-b" not in mcp_server._SESSIONS


def test_create_mcp_server_registers_a_shutdown_lifespan(session_key):
    """_close_all_sessions used to be dead code: no browser was ever reclaimed."""
    mcp = mcp_server.create_mcp_server()
    assert mcp.settings.lifespan is not None

    session_key["key"] = "session-a"
    state = mcp_server._sess()
    state["clean_html"] = "<h1>A</h1>"

    async def _run():
        async with mcp.settings.lifespan(mcp):
            assert "clean_html" in state
        assert "clean_html" not in state

    asyncio.run(_run())
    assert "session-a" not in mcp_server._SESSIONS


# ---------------------------------------------------------------------------
# Session key derivation and its fallback path
# ---------------------------------------------------------------------------


def test_key_is_derived_from_the_context_session(monkeypatch):
    first_session = object()
    monkeypatch.setattr(
        mcp_server, "_mcp_app", _FakeApp(_FakeContext(first_session))
    )
    key = mcp_server._current_session_key()
    assert key != DEFAULT_KEY
    assert mcp_server._current_session_key() == key

    second_session = object()
    monkeypatch.setattr(
        mcp_server, "_mcp_app", _FakeApp(_FakeContext(second_session))
    )
    assert mcp_server._current_session_key() != key


def test_session_key_is_not_the_session_id(monkeypatch):
    """``id()`` is recycled after GC, so it must not be the registry key."""
    session = object()
    monkeypatch.setattr(mcp_server, "_mcp_app", _FakeApp(_FakeContext(session)))

    key = mcp_server._current_session_key()

    assert key != str(id(session))
    assert mcp_server._SESSION_IDENTITIES[id(session)][1] == key


def test_registry_pins_the_session_object(monkeypatch):
    """Pinning the object keeps its id() out of circulation while registered."""
    import gc
    import weakref

    class _Session:
        """Weak-referenceable stand-in for mcp's ServerSession."""

    session = _Session()
    monkeypatch.setattr(mcp_server, "_mcp_app", _FakeApp(_FakeContext(session)))
    mcp_server._current_session_key()

    ref = weakref.ref(session)
    del session
    gc.collect()

    assert ref() is not None, "a registered session must not be collectable"


def test_key_falls_back_to_default_without_an_app(monkeypatch):
    monkeypatch.setattr(mcp_server, "_mcp_app", None)
    assert mcp_server._current_session_key() == DEFAULT_KEY


def test_key_falls_back_to_default_when_context_has_no_session(monkeypatch):
    # FastMCP.get_context() succeeds outside a request but ctx.session raises
    # AttributeError; a request-less ctx must not break the fallback.
    monkeypatch.setattr(
        mcp_server,
        "_mcp_app",
        _FakeApp(_FakeContext(AttributeError("no request context"))),
    )
    assert mcp_server._current_session_key() == DEFAULT_KEY

    monkeypatch.setattr(mcp_server, "_mcp_app", _FakeApp(_FakeContext(None)))
    assert mcp_server._current_session_key() == DEFAULT_KEY

    monkeypatch.setattr(
        mcp_server, "_mcp_app", _FakeApp(error=LookupError("no request context"))
    )
    assert mcp_server._current_session_key() == DEFAULT_KEY


# ---------------------------------------------------------------------------
# Per-session locks
# ---------------------------------------------------------------------------


def test_session_lock_is_distinct_per_key_and_stable(session_key):
    session_key["key"] = "session-a"
    lock_a = mcp_server._get_session_lock()
    assert isinstance(lock_a, asyncio.Lock)
    assert mcp_server._get_session_lock() is lock_a

    session_key["key"] = "session-b"
    lock_b = mcp_server._get_session_lock()
    assert lock_b is not lock_a
    assert isinstance(lock_b, asyncio.Lock)

    session_key["key"] = "session-a"
    assert mcp_server._get_session_lock() is lock_a


# ---------------------------------------------------------------------------
# Server wiring: 27 tools, browser tools serialised on their session lock
# ---------------------------------------------------------------------------


def test_create_mcp_server_exposes_exactly_27_tools():
    mcp = mcp_server.create_mcp_server()
    tools = asyncio.run(mcp.list_tools())
    names = {tool.name for tool in tools}
    assert len(tools) == 27
    assert set(BROWSER_TOOLS) <= names


def test_browser_tools_are_async_and_hold_the_session_lock():
    mcp = mcp_server.create_mcp_server()
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    assert set(BROWSER_TOOLS) <= set(tools)

    for name in BROWSER_TOOLS:
        fn = tools[name].fn
        assert inspect.iscoroutinefunction(fn), f"{name} must be async to hold the lock"
        source = inspect.getsource(fn)
        assert "async with _get_session_lock():" in source, name


def test_import_is_safe_without_the_mcp_package():
    # The FastMCP import must stay lazy inside create_mcp_server(): the session
    # registry and key derivation have to work with the mcp extra missing.
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent(
        """
        import importlib.abc
        import sys

        class _BlockMCP(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "mcp" or fullname.startswith("mcp."):
                    raise ImportError("mcp is not installed for this test")
                return None

        sys.meta_path.insert(0, _BlockMCP())

        from shadow_web.mcp import server

        assert server._DEFAULT_SESSION_KEY
        assert server._current_session_key() == server._DEFAULT_SESSION_KEY
        assert isinstance(server._sess(), dict)
        assert server._get_session_lock() is server._get_session_lock()
        print("ok")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"
