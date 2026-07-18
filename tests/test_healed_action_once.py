from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shadow_web.browser_use import AsyncShadowPage
from shadow_web.wrapper import ShadowPage


def _seed_action(shadow, *, source: str = "dom") -> None:
    shadow.action_map = [
        {
            "id": "1",
            "type": "button",
            "label": "Submit",
            "bind_id": "bind-1",
        }
    ]
    shadow.bindings = {
        "bind-1": {
            "source": source,
            "path": [{"t": "body"}, {"t": "child", "i": 0}],
            "tag": "button",
        }
    }


def test_sync_binding_heal_executes_action_once():
    shadow = ShadowPage(MagicMock())
    _seed_action(shadow)
    shadow._invalidate_heal_for_sid = MagicMock()
    shadow._attempt_heal = MagicMock(return_value="button.submit")
    shadow._execute_selector_action = MagicMock()
    shadow._wait_after_click = MagicMock()
    shadow.refresh = MagicMock()

    with patch(
        "shadow_web.wrapper.interact_by_binding",
        side_effect=RuntimeError("stale binding"),
    ):
        shadow._perform_action("1", "click")

    shadow._execute_selector_action.assert_called_once_with(
        "button.submit",
        "click",
        None,
        5000,
    )
    shadow._wait_after_click.assert_called_once_with(5000)
    shadow.refresh.assert_called_once_with()


@pytest.mark.anyio
async def test_async_binding_heal_executes_action_once():
    shadow = AsyncShadowPage(MagicMock())
    _seed_action(shadow, source="a11y")
    shadow._invalidate_heal_for_sid = MagicMock()
    shadow._attempt_heal = AsyncMock(return_value="button.submit")
    shadow._execute_selector_action = AsyncMock()
    shadow._wait_after_click = AsyncMock()
    shadow.refresh = AsyncMock()

    with patch(
        "shadow_web.browser_use.ainteract_by_a11y_binding",
        new=AsyncMock(side_effect=RuntimeError("stale binding")),
    ):
        await shadow._perform_action("1", "click")

    shadow._execute_selector_action.assert_awaited_once_with(
        "button.submit",
        "click",
        None,
        5000,
    )
    shadow._wait_after_click.assert_awaited_once_with(5000)
    shadow.refresh.assert_awaited_once_with()
