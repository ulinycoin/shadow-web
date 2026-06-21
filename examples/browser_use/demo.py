import os
import sys
import asyncio
from typing import Optional

# Ensure project src is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../src')))

try:
    from browser_use import Agent, Tools, ActionResult, BrowserSession
    from langchain_openai import ChatOpenAI
    HAS_BROWSER_USE = True
except ImportError:
    HAS_BROWSER_USE = False
    print("[Warning] browser-use or langchain_openai is not installed.")
    print("This file serves as a reference implementation. Read README.md for setup instructions.")

from shadow_web.browser_use import AsyncShadowPage

# Global state to keep our shadow page instance bound to the browser session.
# In production, you would manage this inside a custom context or per-agent session.
_shadow_page: Optional[AsyncShadowPage] = None

# Initialize custom tools for browser-use
tools = Tools()

def get_shadow_page(page) -> AsyncShadowPage:
    global _shadow_page
    if _shadow_page is None or _shadow_page.page != page:
        heal_url = os.environ.get("SHADOW_WEB_HEAL_URL", "http://localhost:8000/v1/heal")
        _shadow_page = AsyncShadowPage(
            page,
            heal_api_url=heal_url,
            capture_mode="auto",
            verify_heal=True,
        )
    return _shadow_page

@tools.action(
    description=(
        "Captures the current page state as a compressed XML Action Map. "
        "Use this tool first to understand what interactive elements (buttons, inputs) "
        "exist on the page and get their unique 'id' (data-sid)."
    )
)
async def get_xml_action_map(browser_session: BrowserSession) -> ActionResult:
    page = await browser_session.must_get_current_page()
    shadow = get_shadow_page(page)
    
    # Capture & build compressed map
    clean_html, xml_map = await shadow.refresh()
    
    # Return XML map to the LLM agent
    return ActionResult(
        extracted_content=f"Current Page Grouped Action Map:\n\n{xml_map}",
        include_in_memory=True
    )

@tools.action(
    description=(
        "Clicks an interactive element by its unique Shadow ID (data-sid) from the Action Map. "
        "Automatically heals if the selector has changed due to design updates."
    )
)
async def click_shadow_element(sid: str, browser_session: BrowserSession) -> ActionResult:
    page = await browser_session.must_get_current_page()
    shadow = get_shadow_page(page)
    
    try:
        await shadow.click(sid)
        return ActionResult(
            extracted_content=f"Successfully clicked element with ID {sid}. Current URL: {shadow.last_url}"
        )
    except Exception as e:
        return ActionResult(
            error=f"Failed to click element {sid}: {str(e)}"
        )

@tools.action(
    description=(
        "Fills an input/textarea element by its unique Shadow ID (data-sid) with text value. "
        "Automatically heals broken selectors."
    )
)
async def fill_shadow_element(sid: str, value: str, browser_session: BrowserSession) -> ActionResult:
    page = await browser_session.must_get_current_page()
    shadow = get_shadow_page(page)
    
    try:
        await shadow.fill(sid, value)
        return ActionResult(
            extracted_content=f"Successfully filled element {sid} with value."
        )
    except Exception as e:
        return ActionResult(
            error=f"Failed to fill element {sid}: {str(e)}"
        )

async def main():
    if not HAS_BROWSER_USE:
        print("\nTo run this example, please install requirements:")
        print("pip install browser-use langchain-openai")
        return

    # Initialize langchain LLM (ensure OPENAI_API_KEY is configured in your environment)
    llm = ChatOpenAI(model="gpt-4o")

    # Set up the task
    # Note: We instruct the agent to use our custom shadow tools instead of default click/fill actions
    # to gain the benefit of DOM compression and self-healing.
    task = (
        "Go to news.ycombinator.com, read the action map, and search for 'Agent' "
        "using our custom shadow tools. Do not use the default browser actions."
    )

    agent = Agent(
        task=task,
        llm=llm,
        tools=tools
    )

    print("[Shadow Web Demo] Running browser-use Agent...")
    await agent.run()

if __name__ == "__main__":
    asyncio.run(main())
