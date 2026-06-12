import os
import sys
import time
import subprocess
import unittest
import requests
from playwright.sync_api import sync_playwright

# Ensure src is in PYTHONPATH
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from shadow_web.wrapper import ShadowPage

class TestSelfHealingIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 1. Start FastAPI server in background
        print("[Test] Starting FastAPI server on port 8000...")
        cls.server_proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server.main:app", "--port", "8000"],
            cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), '..')),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        # Give server time to spin up
        time.sleep(2.5)
        
        # Check if server is running
        try:
            res = requests.get("http://127.0.0.1:8000/v1/compress", timeout=2)
            # We expect a 400 or 422 because it's a GET, but it proves the server is alive
            print(f"[Test] Server check status: {res.status_code} (Alive)")
        except Exception as e:
            cls.server_proc.kill()
            raise RuntimeError(f"Failed to connect to local FastAPI server: {e}")

    @classmethod
    def tearDownClass(cls):
        print("[Test] Terminating FastAPI server...")
        cls.server_proc.terminate()
        cls.server_proc.wait()

    def test_end_to_end_self_healing(self):
        # Create a temporary HTML file simulating a broken selector transition
        # Page 1: Initial state where we parsed the Action Map.
        # Button has data-sid="1" and class "btn-old".
        html_page_1 = """
        <html>
        <body>
            <div id="container">
                <button class="btn-old" data-sid="1">Submit Order</button>
            </div>
        </body>
        </html>
        """
        
        # Page 2: The website updated.
        # The data-sid attribute is GONE, and the class name has changed.
        # Only the text "Submit Order" and the tag type "button" remain the same.
        html_page_2 = """
        <html>
        <body>
            <div id="container">
                <button class="new-action-trigger-btn">Submit Order</button>
            </div>
        </body>
        </html>
        """
        
        temp_html_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'temp_test.html'))
        
        # Write Page 1 first
        with open(temp_html_path, "w", encoding="utf-8") as f:
            f.write(html_page_1)
            
        file_url = f"file://{temp_html_path}"
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # Navigate to Page 1
            page.goto(file_url)
            
            # Wrap page in ShadowPage
            # We point to our local FastAPI server
            shadow_page = ShadowPage(
                page, 
                heal_api_url="http://127.0.0.1:8000/v1/heal",
                api_key="test_key"
            )
            
            # Initial parse - builds Action Map containing data-sid="1"
            shadow_page.refresh()
            self.assertEqual(len(shadow_page.action_map), 1)
            self.assertEqual(shadow_page.action_map[0]["label"], "Submit Order")
            
            # Now simulate page update dynamically (write Page 2 to the file and reload page)
            with open(temp_html_path, "w", encoding="utf-8") as f:
                f.write(html_page_2)
            page.reload()
            
            # Inject a click handler in Page 2 to verify if the button is actually clicked
            page.evaluate("""() => {
                document.querySelector('button').addEventListener('click', () => {
                    document.body.innerHTML = '<h1>SUCCESSFULLY CLICKED</h1>';
                });
            }""")
            
            # Verify data-sid="1" is gone from the DOM to ensure we trigger healing
            self.assertNotIn('data-sid="1"', page.content())
            
            # Call click("1")
            # This should fail to find button[data-sid="1"]
            # It should trigger self-healing, send HTML to server, receive button.new-action-trigger-btn
            # And successfully click it.
            print("[Test] Executing self-healing click...")
            shadow_page.click("1")
            
            # Verify the click handler fired and changed body content
            self.assertIn("SUCCESSFULLY CLICKED", page.content())
            print("[Test] Integration success! Page content updated via healed selector.")
            
            browser.close()
            
        # Cleanup temp file
        if os.path.exists(temp_html_path):
            os.remove(temp_html_path)

if __name__ == "__main__":
    unittest.main()
