from typing import Any

def _parse_eval_res(raw: Any) -> Any:
    """
    Parse javascript execution results.
    cdp-use in browser-use stringifies objects and arrays into JSON strings,
    so we need to deserialize them back to dictionaries/lists.
    """
    if isinstance(raw, str) and raw.strip().startswith(("{", "[")):
        try:
            import json
            return json.loads(raw)
        except Exception:
            return raw
    return raw
