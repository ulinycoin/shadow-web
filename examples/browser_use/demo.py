import os
import sys
import asyncio

# Ensure project src is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../src')))

try:
    from browser_use import Agent
    from langchain_openai import ChatOpenAI
    from shadow_web import ShadowTools, HAS_BROWSER_USE
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False
    print("[Warning] browser-use or langchain_openai is not installed.")
    print("This file serves as a reference implementation. Read README.md for setup instructions.")

async def main():
    if not HAS_DEPS:
        print("\nTo run this example, please install requirements:")
        print("pip install browser-use langchain-openai")
        return

    # Check for API Key. If not set, run as an offline integration smoke test.
    if not os.environ.get("OPENAI_API_KEY"):
        print("[Shadow Web Demo] OPENAI_API_KEY is not set.")
        print("[Shadow Web Demo] Running offline integration check...")
        
        # Test ShadowTools initialization
        heal_url = os.environ.get("SHADOW_WEB_HEAL_URL", "http://localhost:8000/v1/heal")
        tools = ShadowTools(heal_api_url=heal_url)
        print("✅ ShadowTools initialized successfully!")
        print("\nRegistered actions:")
        for action in tools.registry.registry.actions.values():
            print(f"  - {action.name}: {action.description[:90]}...")
            
        print("\n[Notice] Setup is complete and correct. Set OPENAI_API_KEY to run the full browser-use LLM agent demo.")
        return

    # Initialize langchain LLM
    llm = ChatOpenAI(model="gpt-4o")

    # Initialize custom shadow tools in one line!
    # This automatically disables browser-use default actions (click_element, input_text)
    # and registers optimized get_xml_action_map, click_shadow_element, and fill_shadow_element.
    heal_url = os.environ.get("SHADOW_WEB_HEAL_URL", "http://localhost:8000/v1/heal")
    tools = ShadowTools(heal_api_url=heal_url)

    # Set up the task.
    # Note: We instruct the agent to use our custom shadow tools instead of default actions.
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
