import os
import sys
import time
from typing import Dict, Any, List

# Ensure src directory is in Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from playwright.sync_api import sync_playwright
from shadow_web.wrapper import ShadowPage

# Try to import tiktoken for accurate GPT-4 / DeepSeek token count (cl100k_base)
try:
    import tiktoken
    _encoding = tiktoken.get_encoding("cl100k_base")
    HAS_TIKTOKEN = True
    def count_tokens(text: str) -> int:
        return len(_encoding.encode(text))
except ImportError:
    HAS_TIKTOKEN = False
    def count_tokens(text: str) -> int:
        # Fallback heuristic: approx 4 characters per token for English
        return len(text) // 4

TEST_SITES = [
    {
        "name": "Hacker News",
        "url": "https://news.ycombinator.com",
        "desc": "Simple table-based link aggregator"
    },
    {
        "name": "Wikipedia (Web Scraping)",
        "url": "https://en.wikipedia.org/wiki/Web_scraping",
        "desc": "Text-heavy informational article"
    },
    {
        "name": "GitHub Trending",
        "url": "https://github.com/trending",
        "desc": "Modern complex SPA-like dashboard"
    }
]

def run_benchmarks():
    print("=" * 80)
    print(" SHADOW WEB COMPRESSION BENCHMARKS")
    print(f" Tiktoken available: {HAS_TIKTOKEN} (using cl100k_base model pricing)" if HAS_TIKTOKEN 
          else " Tiktoken NOT available: using rough estimation (~4 chars per token)")
    print("=" * 80)

    results = []

    with sync_playwright() as p:
        print("[Playwright] Launching headless Chromium...")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        for site in TEST_SITES:
            print(f"\n[Benchmark] Loading {site['name']} ({site['url']})...")
            try:
                # Load page
                start_time = time.time()
                page.goto(site["url"], wait_until="domcontentloaded")
                # Wait for any lazy JS renderings
                page.wait_for_timeout(2000)
                load_duration = time.time() - start_time

                raw_html = page.content()
                raw_chars = len(raw_html)
                raw_tokens = count_tokens(raw_html)

                # Initialize ShadowPage and capture
                shadow = ShadowPage(page)
                shadow_start = time.time()
                clean_html, xml_map = shadow.refresh()
                shadow_duration = time.time() - shadow_start

                clean_chars = len(clean_html)
                clean_tokens = count_tokens(clean_html)

                xml_chars = len(xml_map)
                xml_tokens = count_tokens(xml_map)

                action_count = len(shadow.action_map)

                # Token reduction factor
                reduction_ratio_html = raw_tokens / max(1, clean_tokens)
                reduction_ratio_xml = raw_tokens / max(1, xml_tokens)

                results.append({
                    "name": site["name"],
                    "url": site["url"],
                    "raw_chars": raw_chars,
                    "raw_tokens": raw_tokens,
                    "clean_chars": clean_chars,
                    "clean_tokens": clean_tokens,
                    "xml_chars": xml_chars,
                    "xml_tokens": xml_tokens,
                    "action_count": action_count,
                    "reduction_html": reduction_ratio_html,
                    "reduction_xml": reduction_ratio_xml,
                    "shadow_duration_ms": shadow_duration * 1000,
                    "stats": shadow.capture_stats
                })

                print(f" -> Raw Size: {raw_tokens:,} tokens ({raw_chars:,} chars)")
                print(f" -> Shadow DOM Clean HTML: {clean_tokens:,} tokens ({clean_chars:,} chars) | {reduction_ratio_html:.1f}x reduction")
                print(f" -> Grouped XML Action Map: {xml_tokens:,} tokens ({xml_chars:,} chars) | {reduction_ratio_xml:.1f}x reduction")
                print(f" -> Processing time: {shadow_duration*1000:.1f} ms | Found {action_count} interactive elements.")

            except Exception as e:
                print(f" [ERROR] Failed to benchmark {site['name']}: {e}")

        browser.close()

    # Print Final Summary Table
    print("\n" + "=" * 100)
    print(f"{'Target Site':<25} | {'Raw HTML (tkn)':<15} | {'Clean HTML (tkn)':<18} | {'XML Map (tkn)':<15} | {'XML Savings':<12}")
    print("-" * 100)
    for r in results:
        savings_pct = (1 - (r["xml_tokens"] / r["raw_tokens"])) * 100
        print(f"{r['name']:<25} | {r['raw_tokens']:14,} | {r['clean_tokens']:14,} ({r['reduction_html']:.1f}x) | {r['xml_tokens']:12,} | -{savings_pct:.1f}% ({r['reduction_xml']:.1f}x)")
    print("=" * 100)

if __name__ == "__main__":
    run_benchmarks()
