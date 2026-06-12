# Shadow Web — Project Memory (Hot Cache)

## Core Concept
Shadow Web is an agent-facing web interaction suite that shifts heavy DOM rendering and browser execution to the client (open-source SDK) while providing lightweight cloud APIs for optimization (Compression API) and reliability (Self-Healing Selectors API).

## Directory Structure
```
shadow-web/
├── .env                        # DEEPSEEK_API_KEY + config (gitignored)
├── .gitignore                  # ignores .env, __pycache__, *.pyc
├── CLAUDE.md                   # Hot cache (this file)
├── README.md                   # Project documentation & specs
├── requirements.txt            # Python dependencies
├── pyproject.toml              # Editable install config
├── src/
│   └── shadow_web/
│       ├── __init__.py
│       ├── compressor.py       # lxml DOM stripping & Action Map Builder
│       └── wrapper.py          # Playwright SDK wrapper (sid actions)
├── server/
│   ├── __init__.py
│   └── main.py                 # FastAPI server for /v1/compress and /v1/heal
└── tests/
    ├── __init__.py
    ├── test_compressor.py      # Unit tests (4/4 pass)
    └── test_integration.py     # Integration test — real DeepSeek heal
```

## Setup & Commands
- **Install dependencies:** `pip install -r requirements.txt`
- **Run tests:** `python3 -m unittest discover -s tests`
- **Run FastAPI server:** `uvicorn server.main:app --reload --port 8000`

## Development Standards
- Keep core dependencies minimal (`lxml`, `playwright`, `fastapi`, `uvicorn`).
- No bulky JS frameworks on the server side; Python-first for data processing.
- Clean text values from elements before building LLM payloads.
- Ensure all custom action identifiers (`data-sid`) are short, sequential strings.

## Setup Notes
- Install: `python3 -m pip install -e .` (editable mode, adds `src/` to PYTHONPATH)
- Run server: `python3 -m uvicorn server.main:app --host 127.0.0.1 --port 8000`
- Run tests: `python3 -m unittest discover -s tests -v`

## Active Priorities (Milestone 1)
- [x] Initial directory creation on Desktop
- [x] Port `compressor.py` core from scratch folder
- [x] Implement `wrapper.py` for Playwright interaction using `data-sid`
- [x] Write unittest suite in `tests/` and verify they pass
- [x] Implement FastAPI server with `/v1/heal` DeepSeek integrations and verify via integration test

## Next Steps (Milestone 2)
- [x] Configure live OpenAI/DeepSeek keys in local environment and run healing with actual LLM
- [ ] Implement automated deployment scripts for Oracle Cloud Infrastructure (OCI)
- [x] Package SDK into a installable python wheel (dist/setup.py)

