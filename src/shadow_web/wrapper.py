import time
import requests
from typing import List, Dict, Any, Tuple, Optional
from .compressor import process_html, generate_xml_map

class ShadowPage:
    def __init__(self, page, heal_api_url: Optional[str] = None, api_key: Optional[str] = None):
        """
        Wraps a Playwright Page instance to enable data-sid interactions and self-healing selectors.
        
        Args:
            page: Playwright Page object.
            heal_api_url: Endpoint for the self-healing service (e.g. 'http://localhost:8000/v1/heal').
            api_key: Shadow Web API key for authentication.
        """
        self.page = page
        self.heal_api_url = heal_api_url
        self.api_key = api_key
        
        # State
        self.clean_html: str = ""
        self.action_map: List[Dict[str, Any]] = []
        self.xml_map: str = ""
        self.last_url: str = ""
        
        # Internal cache of healed selectors to prevent re-querying: {sid: new_css_selector}
        self.healed_selectors: Dict[str, str] = {}

    def refresh(self) -> Tuple[str, str]:
        """Intercepts the current DOM, updates clean HTML and builds Action Map."""
        self.last_url = self.page.url
        html_text = self.page.content()
        title = self.page.title()
        
        self.clean_html, self.action_map = process_html(html_text)
        self.xml_map = generate_xml_map(self.last_url, title, self.action_map)
        
        return self.clean_html, self.xml_map

    def get_action_by_sid(self, sid: str) -> Optional[Dict[str, Any]]:
        """Finds action metadata from the current Action Map."""
        for action in self.action_map:
            if action.get("id") == sid:
                return action
        return None

    def _get_element_context_html(self, sid: str) -> str:
        """
        Extracts surrounding HTML context (parent node structure) 
        for a missing data-sid to send to the Self-Healing API.
        """
        # Inject JavaScript to find the parent context of where the element used to be
        # Or find elements with similar text/labels.
        # For simple cases, we pass the current page content or a smaller snippet.
        try:
            # Get the raw HTML around elements that match the action's label
            action = self.get_action_by_sid(sid)
            if not action:
                return self.page.content()[:5000] # Fallback to first 5k chars
                
            label = action.get("label", "")
            tag = action.get("type", "button").split('[')[0] # Get base tag (e.g. input)
            
            # Find elements with matching tags and text/labels to extract context HTML
            context = self.page.evaluate(f"""() => {{
                const label = "{label}";
                const tag = "{tag}";
                // Find element by label text or attributes
                let candidates = Array.from(document.querySelectorAll(tag));
                let match = candidates.find(el => el.textContent.includes(label) || 
                                                  el.placeholder?.includes(label) || 
                                                  el.value?.includes(label));
                if (match) {{
                    // Return the HTML of the parent node
                    return match.parentElement ? match.parentElement.outerHTML : match.outerHTML;
                }}
                return document.body.innerHTML.substring(0, 5000);
            }}""")
            return context
        except Exception:
            return self.page.content()[:3000]

    def _attempt_self_healing(self, sid: str, original_selector: str, error_msg: str) -> str:
        """Queries the Self-Healing API to find a recovered CSS selector."""
        if not self.heal_api_url:
            raise Exception(f"Element {original_selector} not found and no self-healing API URL configured. Error: {error_msg}")
            
        action = self.get_action_by_sid(sid)
        if not action:
            raise Exception(f"Shadow ID {sid} not found in current Action Map. Cannot heal.")
            
        print(f"[Shadow Web] Target element {original_selector} not found. Attempting self-healing for ID {sid}...")
        
        # Get HTML context snippet
        context_html = self._get_element_context_html(sid)
        
        payload = {
            "broken_selector": original_selector,
            "context_html": context_html,
            "action_label": action.get("label", ""),
            "action_type": action.get("type", "")
        }
        
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            
        try:
            response = requests.post(self.heal_api_url, json=payload, headers=headers, timeout=10)
            if response.status_code == 200:
                new_selector = response.json().get("selector")
                if new_selector:
                    print(f"[Shadow Web] Self-healing success! Recovered selector for ID {sid}: '{new_selector}'")
                    self.healed_selectors[sid] = new_selector
                    return new_selector
            raise Exception(f"API returned status {response.status_code}: {response.text}")
        except Exception as e:
            raise Exception(f"Self-healing failed for ID {sid}. Error querying API: {e}. Original error: {error_msg}")

    def click(self, sid: str, timeout_ms: int = 5000):
        """
        Clicks an element by its shadow ID (data-sid), with automatic self-healing.
        """
        if not self.clean_html:
            self.refresh()
            
        # Use previously healed selector if available
        selector = self.healed_selectors.get(sid, f'*[data-sid="{sid}"]')
        
        try:
            # Attempt normal click
            self.page.click(selector, timeout=timeout_ms)
        except Exception as e:
            if sid in self.healed_selectors:
                # If the healed selector failed, clear it and try healing again
                del self.healed_selectors[sid]
                selector = f'*[data-sid="{sid}"]'
                
            # Trigger self-healing
            healed_selector = self._attempt_self_healing(sid, selector, str(e))
            # Retry with healed selector
            self.page.click(healed_selector, timeout=timeout_ms)
            
        # Refresh DOM state after click interaction
        self.page.wait_for_load_state("networkidle")
        self.refresh()

    def fill(self, sid: str, value: str, timeout_ms: int = 5000):
        """
        Fills an input element by its shadow ID (data-sid), with automatic self-healing.
        """
        if not self.clean_html:
            self.refresh()
            
        selector = self.healed_selectors.get(sid, f'*[data-sid="{sid}"]')
        
        try:
            self.page.fill(selector, value, timeout=timeout_ms)
        except Exception as e:
            if sid in self.healed_selectors:
                del self.healed_selectors[sid]
                selector = f'*[data-sid="{sid}"]'
                
            healed_selector = self._attempt_self_healing(sid, selector, str(e))
            self.page.fill(healed_selector, value, timeout=timeout_ms)
            
        self.refresh()
