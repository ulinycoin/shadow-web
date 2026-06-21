"""One-off script to regenerate large benchmark fixtures."""
from pathlib import Path
import json

root = Path(__file__).parent / "fixtures"

rows = []
for i in range(120):
    rows.append(
        f'<div class="flex items-center p-4 hover:bg-gray-800 border-b border-gray-700">'
        f'<svg class="w-4 h-4 mr-2" viewBox="0 0 16 16"><path d="M0 0h16v16H0z"/></svg>'
        f'<a class="text-sm font-mono text-blue-400" href="/file/src/module_{i}.ts">src/module_{i}.ts</a>'
        f'<span class="ml-auto text-xs text-gray-500">Updated {i} days ago</span></div>'
    )

css = "".join(f".p{i}{{padding:{i % 8}px;color:#{i:06x}}}" for i in range(400))
scripts = "".join(f"var _state_{i}={list(range(20))};" for i in range(50))

github = f"""<!DOCTYPE html>
<html><head><title>GitHub repo</title>
<style>{css}</style>
<script>{scripts}</script>
</head><body>
<header class="Header flex p-4"><nav><a href="/">Home</a><a href="/issues">Issues</a></nav>
<input type="search" placeholder="Search" aria-label="Search"/><button aria-label="Create">New</button></header>
<main><h1>org / shadow-web</h1>
<button aria-label="Star">Star</button><button aria-label="Fork">Fork</button>
<a href="/pulls">Pull requests</a><a href="/actions">Actions</a>
<div class="file-tree">{''.join(rows)}</div></main>
<footer><button>Contact</button><a href="/about">About</a></footer></body></html>"""
(root / "github_like.html").write_text(github, encoding="utf-8")

paras = []
for i in range(80):
    paras.append(
        f"<p class=\"mw-parser-output\">Paragraph {i}: Web scraping extracts structured data from HTML. "
        f"Utility noise class-u{i} margin-{i} padding-{i}.</p>"
    )
wiki_css = "".join(f".u{i}{{margin:{i % 5}px;padding:{i % 3}px}}" for i in range(400))
wiki = f"""<!DOCTYPE html><html><head><title>Wikipedia</title>
<style>{wiki_css}</style>
<script>mw.loader.state({json.dumps({f'module{i}': 'ready' for i in range(30)})});</script>
</head><body>
<nav aria-label="Site"><a href="/wiki/Main_Page">Main</a>
<input type="search" placeholder="Search Wikipedia" aria-label="Search"/><button>Search</button></nav>
<main><h1>Web scraping</h1>{''.join(paras)}
<form><input type="text" name="username" placeholder="Username"/>
<input type="password" name="password" placeholder="Password"/>
<button>Create account</button></form></main></body></html>"""
(root / "wikipedia_like.html").write_text(wiki, encoding="utf-8")
print("ok", (root / "github_like.html").stat().st_size, (root / "wikipedia_like.html").stat().st_size)
