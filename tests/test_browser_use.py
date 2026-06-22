import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from shadow_web.utils import _parse_eval_res
from shadow_web.browser_use import AsyncShadowPage, HAS_BROWSER_USE

def test_parse_eval_res():
    # Verify valid JSON parsing
    assert _parse_eval_res('{"key": "value"}') == {"key": "value"}
    assert _parse_eval_res('[1, 2, 3]') == [1, 2, 3]
    # Invalid JSON string should return the original string
    assert _parse_eval_res('{"key": "value"') == '{"key": "value"'
    # Plain text should remain untouched
    assert _parse_eval_res('plain text') == 'plain text'
    # Non-string variables should remain untouched
    assert _parse_eval_res(123) == 123
    assert _parse_eval_res(None) is None

@pytest.mark.anyio
async def test_shadow_tools_not_installed():
    # If HAS_BROWSER_USE is false, importing/calling ShadowTools should raise ImportError
    from shadow_web.browser_use import ShadowTools
    
    # We patch HAS_BROWSER_USE availability to test the stub path
    with patch("shadow_web.browser_use.HAS_BROWSER_USE", False):
        # We define a dummy stub inside a local context to test stub class behaviour
        class DummyStub:
            def __init__(self, *args, **kwargs):
                raise ImportError(
                    "ShadowTools requires browser-use to be installed. "
                    "Install it using: pip install 'shadow-web[browser-use]'"
                )
        
        with pytest.raises(ImportError) as excinfo:
            DummyStub()
        assert "requires browser-use to be installed" in str(excinfo.value)

@pytest.mark.anyio
async def test_shadow_tools_registration():
    if not HAS_BROWSER_USE:
        pytest.skip("browser-use is not installed in the environment")

    from shadow_web.browser_use import ShadowTools

    tools = ShadowTools()
    assert "get_xml_action_map" in tools.registry.registry.actions
    assert "click_shadow_element" in tools.registry.registry.actions
    assert "fill_shadow_element" in tools.registry.registry.actions

    # Verify standard actions are excluded
    assert "click_element" not in tools.registry.registry.actions
    assert "input_text" not in tools.registry.registry.actions

@pytest.mark.anyio
async def test_shadow_tools_actions_mocked():
    if not HAS_BROWSER_USE:
        pytest.skip("browser-use is not installed in the environment")

    from shadow_web.browser_use import ShadowTools

    # Mock page details
    mock_page = MagicMock()
    mock_page.title = AsyncMock(return_value="YCombinator")
    mock_page.url = "https://news.ycombinator.com"
    
    # Mock CDP session to prevent errors during deep capture (auto-mode covers accessibility)
    mock_context = MagicMock()
    mock_cdp = AsyncMock()
    mock_cdp.send = AsyncMock(return_value={"nodes": []})
    mock_context.new_cdp_session = AsyncMock(return_value=mock_cdp)
    mock_page.context = mock_context
    
    # evaluate returns JSON string representation of document structure
    mock_page.evaluate = AsyncMock(return_value='{"html": "<body><button data-sw-bind=\\"sw-1\\">Search</button></body>", "bindings": {"sw-1": {"path": [], "tag": "button"}}, "stats": {}}')

    mock_session = MagicMock()
    mock_session.must_get_current_page = AsyncMock(return_value=mock_page)

    tools = ShadowTools()
    action_fn = tools.registry.registry.actions["get_xml_action_map"].function
    
    # 1. Default is terse (token-efficient)
    result = await action_fn(browser_session=mock_session)
    assert result.error is None
    assert "Current Page Action Map (format: 'terse')" in result.extracted_content

    # 2. Explicit XML when needed
    result = await action_fn(browser_session=mock_session, format="xml")
    assert result.error is None
    assert "Current Page Grouped Action Map" in result.extracted_content
    assert "Search" in result.extracted_content

    # 3. Verify Query filtering
    result = await action_fn(browser_session=mock_session, query="button", format="terse")
    assert result.error is None
    assert "Filtered Page Action Map" in result.extracted_content
