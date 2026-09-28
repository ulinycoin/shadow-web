#!/usr/bin/env python3
"""Back-office portal pull — example RPA flow on a login-gated portal with no API.

SCENARIO
--------
A forwarder / broker / ops team must pull the same record grid out of a vendor
portal every morning. The vendor has no API, no export, and the only interface
is a JavaScript UI. Today a human opens the portal, logs in, filters, and
copy-pastes rows into a spreadsheet.

This script does that loop headlessly and writes a change digest. It is
demonstrated on the public OrangeHRM demo portal, which is a faithful stand-in
for a real back-office: login-gated, Vue-rendered, and — importantly — it does
NOT use an HTML <table>. It renders an ARIA grid (role=table / role=row /
role=cell), which is what modern portals actually ship.

WHAT THIS PROVES (and what it deliberately does not)
----------------------------------------------------
1. Session reuse. A browser profile is NOT enough: the portal issues a session
   cookie (expires=-1), and Chromium never writes those to disk. The flow below
   saves/restores Playwright `storage_state` instead, and skips the login form
   when the restored session is still valid.
2. Stable identity. Interactions are driven from the shadow-web Action Map
   (`data-sid`), not from CSS paths, so a portal redeploy does not silently
   break the flow — that is what the self-healing layer is for.
3. Structured extraction from both planes, via `schema_snap.parse_tables`:
   classic `<table>` elements AND the div-based ARIA grids (role=table/row/cell)
   that modern admin UIs ship. Each result is tagged with its `source` plane.
4. Acting on the portal through the Action Map (`data-sid`), including a click
   that navigates — the engine now waits for the new document instead of racing
   it.
5. Change detection. The artifact a client pays for is the delta, not the dump.
   Running this twice back-to-back correctly reports ZERO changes; a meaningful
   digest needs a daily cadence. (Shadow-web already refuses self-diffs in the
   price-watch path for exactly this reason — see REVIEW.md B10.)

USAGE
-----
    python scripts/backoffice_portal_demo.py                 # full flow
    python scripts/backoffice_portal_demo.py --headed        # watch it work
    BACKOFFICE_USER=me BACKOFFICE_PASS=secret \
        python scripts/backoffice_portal_demo.py --login-url https://portal.example/login \
            --list-url https://portal.example/orders --state ~/.shadow-web/portal.json

Credentials come from the environment; the defaults below are the portal's own
public demo credentials, printed on its login page.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from playwright.sync_api import sync_playwright  # noqa: E402

from shadow_web.schema_snap import parse_tables  # noqa: E402
from shadow_web.wrapper import ShadowPage  # noqa: E402

# Public demo portal. Swap both URLs for the real vendor portal.
DEMO_LOGIN = "https://opensource-demo.orangehrmlive.com/web/index.php/auth/login"
DEMO_LIST = "https://opensource-demo.orangehrmlive.com/web/index.php/pim/viewEmployeeList"

DEFAULT_OUT = ROOT / "reports" / "backoffice"

# ARIA grid note: modern portals (Vue/React admin UIs) render
# role=table/row/cell instead of <table>. `schema_snap.parse_tables` now reads
# both planes and tags each result with `source`, so no local extractor is
# needed here any more.


def log(msg: str) -> None:
    print(msg, flush=True)


def open_context(browser, state_path: Path):
    """Restore a saved session if we have one.

    Note: launch_persistent_context(user_data_dir=...) does NOT survive a portal
    that issues session cookies — verified against this demo portal. storage_state
    does, because Playwright serialises session cookies explicitly.
    """
    kwargs = {"locale": "en-US", "viewport": {"width": 1400, "height": 950}}
    if state_path.exists():
        kwargs["storage_state"] = str(state_path)
        cookies = json.loads(state_path.read_text()).get("cookies", [])
        log(f"  session: restoring storage_state ({len(cookies)} cookie(s)) from {state_path}")
    else:
        log("  session: no saved state, login will be required")
    return browser.new_context(**kwargs)


def ensure_logged_in(page, login_url: str, user: str, password: str) -> bool:
    """Navigate to the portal root; log in only if the session is dead.

    Returns True when a login form had to be submitted.
    """
    page.goto(login_url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(2500)
    if "/auth/login" not in page.url:  # reversed by the restored session
        log(f"  login: skipped (session alive) -> {page.url}")
        return False

    log("  login: submitting credentials")
    page.wait_for_selector("input[name=username]", timeout=45_000)
    page.fill("input[name=username]", user)
    page.fill("input[name=password]", password)
    page.click("button[type=submit]")
    page.wait_for_url("**/dashboard/**", timeout=60_000)
    log(f"  login: ok -> {page.url}")
    return True


def extract_records(page) -> tuple[list[str], list[list[str]], str]:
    """Read the portal grid through shadow-web's SchemaSnap.

    Both planes now come from the engine: classic <table> elements and the
    div-based ARIA grids that modern admin UIs ship (role=table/row/cell).
    """
    tables = parse_tables(page.content(), max_rows=200)
    if not tables or not tables[0].get("rows"):
        return [], [], "empty"
    table = tables[0]
    return table["columns"], table["rows"], f"schema_snap.parse_tables ({table.get('source')})"


def click_by_label(page, label: str) -> str | None:
    """Act on the portal through the Action Map, not through CSS selectors.

    The Action Map gives every interactive element a `data-sid` plus a human
    label. Driving the flow by (label -> sid) is what keeps the automation
    alive across portal redeploys: when the sid goes stale, shadow-web heals
    the selector instead of the script raising "element not found".
    """
    shadow = ShadowPage(page, capture_mode="auto", prefer_webmcp=False)
    shadow.refresh()
    needle = label.lower()
    target = next(
        (
            a for a in shadow.action_map
            if needle in str(a.get("label", "")).lower()
            or needle in str(a.get("placeholder", "")).lower()
            or needle in str(a.get("href", "")).lower()
        ),
        None,
    )
    if not target or not target.get("id"):
        return None
    sid = str(target["id"])
    shadow.click(sid)
    page.wait_for_load_state("load", timeout=60_000)
    page.wait_for_timeout(2000)
    return f"{target.get('type')} label={target.get('label')!r} sid={sid}"


def action_map_summary(page) -> dict:
    """shadow-web Action Map: stable per-element identity for the portal UI."""
    shadow = ShadowPage(page, capture_mode="auto", prefer_webmcp=False)
    shadow.refresh()
    interactive = [
        a for a in shadow.action_map
        if str(a.get("type", "")).split("[")[0] in {"input", "button", "select", "textarea", "a"}
    ]
    return {
        "page_class": shadow.page_class,
        "page_class_reason": shadow.page_class_reason,
        "elements": len(shadow.action_map),
        "interactive": len(interactive),
        "sample": [
            {
                "sid": a.get("id"),  # Action Map key is "id"; data-sid carries it in the DOM
                "type": a.get("type"),
                "label": (a.get("label") or a.get("placeholder") or a.get("href") or "")[:60],
            }
            for a in interactive[:6]
        ],
    }


def _key_index(rows: list[list[str]]) -> int:
    """Pick the record identity column.

    Grids usually lead with a checkbox/no-op column that renders empty, so a
    naive r[0] key silently collapses every row into one. Choose the first
    column that is mostly filled and mostly unique.
    """
    if not rows:
        return 0
    width = max(len(r) for r in rows)
    best, best_score = 0, -1.0
    for i in range(width):
        vals = [r[i] for r in rows if i < len(r)]
        filled = [v for v in vals if v and v.strip()]
        if not filled:
            continue
        score = (len(filled) / len(vals)) * (len(set(filled)) / len(filled))
        if score > best_score:
            best, best_score = i, score
    return best


def diff_records(prev: dict | None, header: list[str], rows: list[list[str]]) -> dict:
    """Delta between two pulls, keyed on the record identity column."""
    key_idx = _key_index(rows)
    key_name = header[key_idx] if key_idx < len(header) else f"col{key_idx}"
    current = {r[key_idx]: r for r in rows if len(r) > key_idx and r[key_idx].strip()}
    if not prev:
        return {"baseline": True, "key_column": key_name, "added": len(current), "removed": 0,
                "changed": [], "added_keys": sorted(current)[:20], "removed_keys": []}

    before = {r[0]: r[1:] for r in prev.get("rows", []) if r}
    before = {k: v for k, v in before.items() if k and k.strip()}
    added = sorted(set(current) - set(before))
    removed = sorted(set(before) - set(current))
    changed = []
    for key in sorted(set(current) & set(before)):
        now = current[key][: key_idx] + current[key][key_idx + 1:]
        old = before[key]
        pairs = [
            {"column": header[i + 1] if i + 1 < len(header) else f"col{i + 1}", "from": old[i], "to": now[i]}
            for i in range(min(len(old), len(now)))
            if old[i] != now[i]
        ]
        if pairs:
            changed.append({"key": key, "fields": pairs})
    return {
        "baseline": False,
        "key_column": key_name,
        "added": len(added),
        "removed": len(removed),
        "changed": changed,
        "added_keys": added[:20],
        "removed_keys": removed[:20],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Pull a record grid from a login-gated portal.")
    ap.add_argument("--login-url", default=DEMO_LOGIN)
    ap.add_argument("--list-url", default=DEMO_LIST)
    ap.add_argument("--user", default=os.environ.get("BACKOFFICE_USER", "Admin"))
    ap.add_argument("--pass", dest="password", default=os.environ.get("BACKOFFICE_PASS", "admin123"))
    ap.add_argument("--state", default=str(Path.home() / ".shadow-web" / "backoffice-state.json"))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--headed", action="store_true", help="show the browser window")
    ap.add_argument("--dry-run", action="store_true", help="login + extract, write nothing")
    ap.add_argument("--prev", default="", help="compare against this snapshot instead of the newest one")
    ap.add_argument("--then", default="", metavar="LABEL",
                    help="after the pull, click the Action Map element whose label matches LABEL "
                         "and pull again (e.g. --then Leave)")
    args = ap.parse_args()

    state_path = Path(args.state).expanduser()
    out_dir = Path(args.out_dir)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snap_path = out_dir / f"records-{today}.json"
    prev_snap = None
    if args.prev:
        prev_path = Path(args.prev).expanduser()
        prev_snap = json.loads(prev_path.read_text(encoding="utf-8"))
        log(f"previous pull: {prev_path.name} ({len(prev_snap.get('rows', []))} rows)")
    else:
        previous = sorted(p for p in out_dir.glob("records-*.json") if p != snap_path)
        if previous:
            prev_snap = json.loads(previous[-1].read_text(encoding="utf-8"))
            log(f"previous pull: {previous[-1].name} ({len(prev_snap.get('rows', []))} rows)")

    log("=" * 72)
    log(f" BACK-OFFICE PULL — {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    log("=" * 72)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        context = open_context(browser, state_path)
        page = context.new_page()
        try:
            ensure_logged_in(page, args.login_url, args.user, args.password)

            log(f"  portal: navigating to the record view")
            page.goto(args.list_url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(5000)
            log(f"  portal: {page.url}")

            info = action_map_summary(page)
            log(f"  action map: {info['elements']} elements, {info['interactive']} interactive"
                f" | page class: {info['page_class']}")
            for el in info["sample"]:
                log(f"      sid={el['sid']} type={el['type']} label={el['label']!r}")

            header, rows, plane = extract_records(page)
            log(f"  extract: {len(rows)} records via {plane}")
            if header:
                log(f"  columns: {' | '.join(header[:7])}")

            delta = diff_records(prev_snap, header, rows)
            if delta["baseline"]:
                log(f"  diff: baseline pull — {delta['added']} records keyed on '{delta['key_column']}'"
                    f" (no previous pull to compare)")
            else:
                log(f"  diff: +{delta['added']} / -{delta['removed']} / ~{len(delta['changed'])} changed"
                    f" (key '{delta['key_column']}')")
                for ch in delta["changed"][:5]:
                    for f in ch["fields"][:3]:
                        log(f"      {ch['key']}: {f['column']}: {f['from']!r} -> {f['to']!r}")

            # Persist the session so the next run can skip the login step.
            second_pull = None
            if args.then:
                log(f"  act: clicking the Action Map element matching {args.then!r}")
                hit = click_by_label(page, args.then)
                if not hit:
                    log(f"  act: nothing matched {args.then!r} — skipped")
                else:
                    log(f"  act: {hit}")
                    log(f"  act: landed on {page.url}")
                    header2, rows2, plane2 = extract_records(page)
                    log(f"  extract: {len(rows2)} records via {plane2} (after the action)")
                    if header2:
                        log(f"  columns: {' | '.join(header2[:7])}")
                    second_pull = {"url": page.url, "plane": plane2, "columns": header2,
                                   "rows": rows2, "via": hit}

            context.storage_state(path=str(state_path))
            log(f"  session: saved to {state_path}")

            if args.dry_run:
                log("  dry-run: nothing written")
            else:
                snap_path.write_text(json.dumps({
                    "pulled_at": datetime.now(timezone.utc).isoformat(),
                    "portal": args.list_url,
                    "plane": plane,
                    "page_class": info["page_class"],
                    "action_map": {"elements": info["elements"], "interactive": info["interactive"]},
                    "columns": header,
                    "rows": rows,
                    "delta": delta,
                    "after_action": second_pull,
                }, ensure_ascii=False, indent=2), encoding="utf-8")
                log(f"  wrote: {snap_path}")
        finally:
            context.close()
            browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# ---------------------------------------------------------------------------
# ENGINE FINDINGS from building this example (F1 and F2 are now fixed)
#
# F1. click() raced with portal navigation. `_perform_action` ends with
#     `_wait_after_click()` + `refresh()`, but the wait targeted
#     `domcontentloaded` on the OLD document, which is already loaded, so the
#     `evaluate()` inside `refresh()` could run while the new document was
#     committing and raise "Execution context was destroyed". The click itself
#     succeeded; only the observation failed. Reproduced 1/1 on this portal.
#     FIXED: `_wait_after_click` is now navigation-aware (it compares against the
#     pre-click URL and waits for the new document to load), and `refresh()` —
#     sync and async — retries once when a navigation destroys the context,
#     while never retrying real failures. See tests/test_navigation_race.py.
#     Location: src/shadow_web/wrapper.py, src/shadow_web/browser_use.py.
#
# F2. schema_snap only understood <table>. This portal (like most Vue/React
#     admin UIs) renders an ARIA grid — role=table/row/cell — so `parse_tables()`
#     returned nothing and every caller had to bring its own extractor.
#     FIXED: `parse_tables` now reads both planes and tags each result with
#     `source` ("table" | "aria_grid"); nested grids are not double-counted.
#     Cell text also drops hidden subtrees, so a header's display:none sort menu
#     no longer splices "AscendingDescending" into the column name, and
#     zero-padded identifiers like "0406" stay strings instead of becoming 406.
#     See tests/test_schema_snap.py.
#
# F3. A browser profile does not keep a portal login. `--profile`
#     (launch_persistent_context / user_data_dir) writes cookies to disk, but
#     portals commonly issue session cookies (expires=-1) and Chromium does not
#     persist those. Verified: run 2 on the same profile was logged out again.
#     Playwright `storage_state` does restore them, which is what this script
#     uses. The exec agent's `--profile` and the MCP server's per-session
#     context therefore do NOT give you a reusable portal login today.
#     NOT FIXED — this is the session-vault gap, see item 1 below.
#
# WHAT A PAID DEPLOYMENT STILL NEEDS (this example is mechanics, not product)
#
# 1. Credential vault. Credentials here come from the environment in cleartext.
#    A client portal needs per-tenant secrets at rest, rotation, and an audit
#    trail of who triggered each pull.
# 2. Scheduling + history store. Nothing in shadow-web schedules runs; cron
#    calls this script today. Phase B in ROADMAP.md.
# 3. Write actions. This example is read-only. Clicking "approve", "book" or
#    "submit" on a client's behalf needs an explicit consent model: form_fill
#    already defaults to auto_submit=False and one submit per URL, which is the
#    right primitive to build on.
# 4. Provenance. Before any of this is used as evidence ("the portal showed X
#    on date Y"), the winning extraction plane has to be stored alongside the
#    value — an open item in ROADMAP.md Phase A.
# ---------------------------------------------------------------------------
