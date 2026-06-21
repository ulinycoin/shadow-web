"""
In-browser DOM flattening for Shadow Web.

Builds a read-only HTML snapshot (open Shadow DOM + same-origin iframes) for the
compressor. Never writes flattened markup back into the live document — that
would break React/Vue/Svelte listeners and component state.

Each interactive element receives a stable ``data-sw-bind`` id and a path map
used to resolve the live node for Playwright actions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Union

# Playwright Page / Frame — typed loosely to avoid hard dependency in unit tests.
PageLike = Any

ActionKind = Literal["click", "fill"]


@dataclass
class FlattenResult:
    """Output of a single flatten pass."""

    html: str
    bindings: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    stats: Dict[str, int] = field(default_factory=dict)


# Runs inside the browser. Produces HTML string + binding paths only — no DOM mutation.
_FLATTEN_SCRIPT = """
() => {
  const bindings = {};
  let bindCounter = 0;

  const stats = {
    interactive: 0,
    shadow_hosts: 0,
    same_origin_iframes: 0,
    cross_origin_iframes: 0,
  };

  function escapeAttr(value) {
    if (value == null) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function isInteractive(el) {
    if (!el || el.nodeType !== 1) return false;
    const tag = el.tagName.toLowerCase();
    if (["a", "button", "input", "select", "textarea"].includes(tag)) return true;
    if (el.getAttribute("role") === "button") return true;
    if (el.hasAttribute("onclick")) return true;
    return false;
  }

  const KEEP_ATTRS = [
    "href", "src", "alt", "type", "value", "placeholder",
    "name", "for", "action", "method", "aria-label",
  ];

  function buildAttrs(el, bindId) {
    let out = "";
    if (bindId) out += ` data-sw-bind="${bindId}"`;
    for (const name of KEEP_ATTRS) {
      if (el.hasAttribute(name)) {
        out += ` ${name}="${escapeAttr(el.getAttribute(name))}"`;
      }
    }
    return out;
  }

  function elementChildren(parent) {
    return Array.from(parent.children).filter((n) => n.nodeType === 1);
  }

  function registerBind(el, path) {
    bindCounter += 1;
    const bindId = "sw-" + bindCounter;
    bindings[bindId] = {
      path: path.slice(),
      tag: el.tagName.toLowerCase(),
    };
    stats.interactive += 1;
    return bindId;
  }

  function serializeElement(el, path) {
    const tag = el.tagName.toLowerCase();
    const SKIP_TAGS = new Set([
      "script", "style", "noscript", "meta", "link", "head",
      "canvas", "audio", "video",
    ]);
    if (SKIP_TAGS.has(tag)) return "";

    if (tag === "iframe") {
      const bindId = isInteractive(el) ? registerBind(el, path) : null;
      try {
        const doc = el.contentDocument;
        if (doc && doc.body) {
          stats.same_origin_iframes += 1;
          const innerPath = path.concat([{ t: "iframe" }]);
          const inner = serializeChildren(doc.body, innerPath);
          const attrs = buildAttrs(el, bindId);
          return `<div data-sw-iframe="same-origin"${attrs}>${inner}</div>`;
        }
      } catch (err) {
        /* cross-origin — fall through */
      }
      stats.cross_origin_iframes += 1;
      const attrs = buildAttrs(el, bindId);
      const src = el.getAttribute("src") || "";
      return (
        `<div data-sw-iframe="cross-origin" data-sw-src="${escapeAttr(src)}"${attrs}>` +
        `[iframe: ${escapeAttr(src)}]</div>`
      );
    }

    const bindId = isInteractive(el) ? registerBind(el, path) : null;
    const attrs = buildAttrs(el, bindId);

    let inner = "";
    if (el.shadowRoot) {
      stats.shadow_hosts += 1;
      const shadowPath = path.concat([{ t: "shadow" }]);
      inner = serializeChildren(el.shadowRoot, shadowPath);
    } else {
      inner = serializeChildren(el, path);
    }

    return `<${tag}${attrs}>${inner}</${tag}>`;
  }

  function serializeChildren(parent, path) {
    let html = "";
    const nodes = Array.from(parent.childNodes);
    let childElementIndex = 0;
    for (let i = 0; i < nodes.length; i++) {
      const node = nodes[i];
      if (node.nodeType === 1) {
        html += serializeElement(node, path.concat([{ t: "child", i: childElementIndex }]));
        childElementIndex++;
      } else if (node.nodeType === 3) {
        html += escapeAttr(node.textContent);
      }
    }
    return html;
  }

  if (!document.body) {
    return { html: "<body></body>", bindings, stats };
  }

  const html = "<body>" + serializeChildren(document.body, [{ t: "body" }]) + "</body>";
  return { html, bindings, stats };
}
"""

_RESOLVE_SCRIPT = """
({ path }) => {
  function elementChildren(parent) {
    return Array.from(parent.children).filter((n) => n.nodeType === 1);
  }

  let node = document.documentElement;
  for (const step of path) {
    if (!node) return null;
    if (step.t === "body") {
      node = document.body;
    } else if (step.t === "child") {
      node = elementChildren(node)[step.i];
    } else if (step.t === "shadow") {
      if (!node.shadowRoot) return null;
      node = node.shadowRoot;
    } else if (step.t === "iframe") {
      if (node.tagName.toLowerCase() !== "iframe") return null;
      node = node.contentDocument && node.contentDocument.body;
    }
  }
  return node;
}
"""

_INTERACT_SCRIPT = """
({ path, action, value }) => {
  function elementChildren(parent) {
    return Array.from(parent.children).filter((n) => n.nodeType === 1);
  }

  let node = document.documentElement;
  for (const step of path) {
    if (!node) return { ok: false, error: "path_resolution_failed" };
    if (step.t === "body") {
      node = document.body;
    } else if (step.t === "child") {
      node = elementChildren(node)[step.i];
    } else if (step.t === "shadow") {
      if (!node.shadowRoot) return { ok: false, error: "shadow_root_missing" };
      node = node.shadowRoot;
    } else if (step.t === "iframe") {
      if (node.tagName.toLowerCase() !== "iframe") return { ok: false, error: "iframe_expected" };
      node = node.contentDocument && node.contentDocument.body;
    }
  }

  if (!node || node.nodeType !== 1) {
    return { ok: false, error: "element_not_found" };
  }

  if (action === "click") {
    node.click();
    return { ok: true };
  }
  if (action === "fill") {
    if (typeof node.value === "undefined") {
      return { ok: false, error: "not_fillable" };
    }
    node.focus();
    node.value = value ?? "";
    node.dispatchEvent(new Event("input", { bubbles: true }));
    node.dispatchEvent(new Event("change", { bubbles: true }));
    return { ok: true };
  }
  return { ok: false, error: "unknown_action" };
}
"""


def capture_flattened_dom(page: PageLike) -> FlattenResult:
    """
    Flatten open Shadow DOM and same-origin iframes into an HTML string.

    The live document is never modified. Use :func:`interact_by_binding` to act
    on elements in the real DOM via stored paths.
    """
    raw = page.evaluate(_FLATTEN_SCRIPT)
    return FlattenResult(
        html=raw.get("html") or "<body></body>",
        bindings=raw.get("bindings") or {},
        stats=raw.get("stats") or {},
    )


def resolve_binding(page: PageLike, binding: Dict[str, Any]) -> bool:
    """Return True if the binding path still resolves to a live element."""
    path = binding.get("path")
    if not path:
        return False
    node = page.evaluate(_RESOLVE_SCRIPT, {"path": path})
    return node is not None


def interact_by_binding(
    page: PageLike,
    binding: Dict[str, Any],
    action: ActionKind,
    value: Optional[str] = None,
) -> None:
    """Click or fill the live element described by a flatten or a11y binding."""
    if binding.get("source") == "a11y":
        from .a11y_capture import interact_by_a11y_binding

        interact_by_a11y_binding(page, binding, action, value=value)
        return

    path = binding.get("path")
    if not path:
        raise ValueError("Binding has no path")

    result = page.evaluate(
        _INTERACT_SCRIPT,
        {"path": path, "action": action, "value": value},
    )
    if not result or not result.get("ok"):
        error = (result or {}).get("error", "unknown")
        tag = binding.get("tag", "?")
        raise RuntimeError(
            f"Failed to {action} element <{tag}> via binding path: {error}"
        )


def binding_path_json(binding: Dict[str, Any]) -> str:
    """Serialize a binding path for logging or heal context."""
    return json.dumps(binding.get("path", []), separators=(",", ":"))
