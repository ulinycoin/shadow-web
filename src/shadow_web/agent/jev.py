"""Jev (TypeSafe System One) as a gated step selector for the exec agent.

Jev answers typed questions over a supplied state and never generates text, so a
"what next" decision becomes a Choice over typed candidates plus a Noul presence
check, both with calibrated probabilities. Code owns the gate: the selector's
decision is used only when it is positive *and* confident enough; otherwise the
step falls through to the LLM. Failures never raise into the loop.

Enabled by environment:

    SHADOW_WEB_AGENT_JEV=1             # opt-in
    TYPESAFE_API_KEY=...               # or SHADOW_WEB_JEV_API_KEY
    SHADOW_WEB_JEV_THRESHOLD=0.6       # optional
    SHADOW_WEB_JEV_MODEL=jev-latest    # optional
    SHADOW_WEB_JEV_MAX_STEPS=2         # optional, selector steps per run
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
DEFAULT_THRESHOLD = 0.6
DEFAULT_MAX_OPTIONS = 25
DEFAULT_TIMEOUT = 30

# The `presence` Noul ("is a useful candidate on this page at all?") is a coarse
# signal: measured on real tasks it reads lower than Jev's own choice probability
# (0.33 presence vs 0.79 probability on the same request), so it is used only as a
# floor that catches "this page has nothing for the task" — never as a hard gate.
MIN_PRESENCE = 0.2

# "@12 a Pricing [Header]" — the shape produced by tools._action_terse().
_ACTION_LINE = re.compile(
    r"^@(?P<sid>\S+)\s+(?P<kind>\S+)\s*(?P<label>.*?)(?:\s*\[(?P<group>[^\]]*)\])?$"
)

# Tools Jev may pick. `done` is excluded on purpose: finishing needs generated
# text, which a System One model cannot produce.
TOOL_CANDIDATES: Dict[str, str] = {
    "schema_page": "Extract tables / forms / lists from the page as JSON",
    "content_outline": "Read the page text as a terse block outline (articles, catalogs, SPA text)",
    "snapshot": "Refresh the observation / action map of the current page",
}
TRUTHY = {"1", "true", "yes", "on"}


@dataclass
class JevDecision:
    """One gated decision: what to do next, and how sure Jev is."""

    name: str                      # tool name, or "click"
    args: Dict[str, Any] = field(default_factory=dict)
    probability: float = 0.0       # probability of the chosen option
    confidence: float = 0.0        # distribution concentration of the choice
    presence: float = 0.0          # Noul: is a usable step present at all
    candidates: int = 0
    usage: Dict[str, int] = field(default_factory=dict)
    label: str = ""

    def usable(self, threshold: float) -> bool:
        """Gate: act only when the pick is both likely and concentrated.

        `presence` is a floor (see MIN_PRESENCE), not a gate — it is far weaker
        than the choice distribution and would reject correct picks.
        """
        return (
            self.presence >= MIN_PRESENCE
            and self.confidence >= threshold
            and self.probability >= threshold
        )

    def describe(self) -> str:
        return (
            f"{self.name}({json.dumps(self.args, ensure_ascii=False)}) "
            f"p={self.probability:.2f} conf={self.confidence:.2f} presence={self.presence:.2f}"
        )


def parse_action_lines(observation: str) -> List[Dict[str, str]]:
    """Pull `@sid kind label` rows out of a terse observation."""
    out: List[Dict[str, str]] = []
    for raw in (observation or "").splitlines():
        line = raw.strip()
        if not line.startswith("@"):
            continue
        m = _ACTION_LINE.match(line)
        if not m:
            continue
        sid = (m.group("sid") or "").strip()
        if not sid or sid == "?":
            continue
        label = (m.group("label") or "").strip()
        label = re.sub(r"\s*\[[^\]]*\]\s*$", "", label)  # drop the group suffix
        out.append(
            {
                "sid": sid,
                "kind": (m.group("kind") or "").strip(),
                "label": label[:70],
                "group": (m.group("group") or "").strip(),
            }
        )
    return out


class JevSelector:
    """Ask Jev which single step advances the task; gate on calibrated confidence."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        endpoint: str = DEFAULT_ENDPOINT,
        model: str = DEFAULT_MODEL,
        threshold: float = DEFAULT_THRESHOLD,
        max_options: int = DEFAULT_MAX_OPTIONS,
        timeout: int = DEFAULT_TIMEOUT,
        transport: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ):
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = model
        self.threshold = threshold
        self.max_options = max_options
        self.timeout = timeout
        self._transport = transport
        # Why the last select() returned None — surfaced in run telemetry.
        self.last_skip_reason: Optional[str] = None

    # -- construction -----------------------------------------------------

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> Optional["JevSelector"]:
        env = dict(os.environ if env is None else env)
        if (env.get("SHADOW_WEB_AGENT_JEV") or "").strip().lower() not in TRUTHY:
            return None
        key = (env.get("SHADOW_WEB_JEV_API_KEY") or env.get("TYPESAFE_API_KEY") or "").strip()
        if not key:
            return None
        try:
            threshold = float(env.get("SHADOW_WEB_JEV_THRESHOLD", DEFAULT_THRESHOLD))
        except (TypeError, ValueError):
            threshold = DEFAULT_THRESHOLD
        return cls(
            api_key=key,
            endpoint=env.get("SHADOW_WEB_JEV_ENDPOINT", DEFAULT_ENDPOINT),
            model=env.get("SHADOW_WEB_JEV_MODEL", DEFAULT_MODEL),
            threshold=threshold,
        )

    # -- HTTP -------------------------------------------------------------

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self._transport is not None:
            return self._transport(payload)
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    # -- decision ---------------------------------------------------------

    def candidates_for(self, observation: str) -> Dict[str, str]:
        """Typed option map: tool steps + clickable sids from the observation."""
        options: Dict[str, str] = {f"tool:{name}": desc for name, desc in TOOL_CANDIDATES.items()}
        for action in parse_action_lines(observation)[: self.max_options]:
            key = f"click:{action['sid']}"
            label = action["label"] or action["kind"]
            options[key] = f"Click element {action['sid']} ({action['kind']}): {label}"
        return options

    def select(
        self,
        *,
        goal: str,
        observation: str,
        url: str = "",
    ) -> Optional[JevDecision]:
        """Return a decision when Jev is usable, else None (caller uses the LLM)."""
        options = self.candidates_for(observation)
        # A selector step only makes sense over a real action map.
        if not parse_action_lines(observation):
            self.last_skip_reason = "no_action_map"
            return None
        try:
            payload = {
                "model": self.model,
                "state": {
                    "task": goal,
                    "page": {"url": url},
                    "action_map": observation,
                },
                "questions": {
                    "step_exists": {
                        "type": "noul",
                        "instructions": (
                            "Treat every `candidate` as a possible NEXT step for the agent, not as a "
                            "complete solution: a candidate is relevant when acting on it makes "
                            "progress toward `task` (reading, extracting, opening a page). "
                            "Is at least one candidate a useful next step on this page?"
                        ),
                        "criteria": {
                            "true": "At least one candidate makes progress toward the task",
                            "false": "No candidate makes any progress toward the task",
                        },
                    },
                    "next_step": {
                        "type": "choice",
                        "instructions": "Which single candidate is the best immediate next step for `task`?",
                        "criteria": options,
                    },
                },
            }
            data = self._post(payload)
        except Exception:  # noqa: BLE001 — the selector must never break a run
            self.last_skip_reason = "transport_error"
            return None
        try:
            answers = data["answers"]
            chosen = str(answers["next_step"]["choice"])
            usage = {k: int(v) for k, v in (data.get("usage") or {}).items()}
            decision = JevDecision(
                name="click" if chosen.startswith("click:") else chosen.split(":", 1)[-1],
                args={"sid": chosen.split(":", 1)[1]} if chosen.startswith("click:") else {},
                probability=float(answers["next_step"]["probabilities"].get(chosen, 0.0)),
                confidence=float(answers["next_step"]["confidence"]),
                presence=float(answers["step_exists"]["noul"]),
                candidates=len(options),
                usage=usage,
                label=options.get(chosen, ""),
            )
        except (KeyError, TypeError, ValueError):
            self.last_skip_reason = "malformed_response"
            return None
        if decision.name not in TOOL_CANDIDATES and decision.name != "click":
            self.last_skip_reason = "unknown_choice"
            return None
        self.last_skip_reason = None
        return decision


def observation_from(result: Any) -> Optional[str]:
    """Observation text from a ToolResult, if it carries an action map."""
    data = getattr(result, "data", None)
    if not isinstance(data, dict):
        return None
    obs = data.get("observation")
    if isinstance(obs, str) and obs.strip():
        return obs
    return None


# ---------------------------------------------------------------------------
# Price plane: "which of these amounts is this product's price?"
# ---------------------------------------------------------------------------

PRICE_THRESHOLD = 0.7
# Noul floor, always applied. The Choice must return *something* even when no
# candidate is an offer price, so `is_offer` is the only signal that can say
# "this amount is not this product's price".
#
# Why 0.7 and not 0.6: on the live tentree PDP (real URL, ['$45','$20']) two
# consecutive calls gave is_offer 0.56 and 0.61, confidence 0.59 and 0.64 while
# the picked amount stayed $20. A 0.6 floor sits inside that jitter, so the row
# would flip between "$20 accepted" and "stay low" on every daily scan — a false
# price alert is worse than an honest low. 0.70 is two jitter widths above the
# hottest live sample (0.61). At the boundary the model reports "don't know",
# which is the correct answer for a row no human has checked yet.
OFFER_FLOOR = 0.7


@dataclass
class PriceJudgement:
    """Jev's read on a regex price plane, with the evidence kept for audits."""

    raw: str
    amount: float
    confidence: float
    is_offer: float
    probability: float = 0.0
    candidates: int = 0
    usage: Dict[str, int] = field(default_factory=dict)

    def accepted(
        self,
        threshold: float = PRICE_THRESHOLD,
        offer_floor: float = OFFER_FLOOR,
    ) -> bool:
        """Double gate.

        `is_offer` must clear its floor always. Choice confidence is only
        meaningful when there was a real choice: with a single candidate the
        model must return it with probability 1, so confidence carries no
        information there and is not required.
        """
        if self.is_offer < offer_floor:
            return False
        if self.candidates >= 2 and self.confidence < threshold:
            return False
        return True

    def gate(self, threshold: float = PRICE_THRESHOLD, offer_floor: float = OFFER_FLOOR) -> dict:
        """Thresholds actually applied to this judgement — kept for audits."""
        return {
            "is_offer_floor": offer_floor,
            "confidence_threshold": threshold if self.candidates >= 2 else None,
            "candidates": self.candidates,
        }

    def describe(self) -> str:
        return (
            f"{self.raw} → {self.amount} (conf={self.confidence:.2f} "
            f"is_offer={self.is_offer:.2f} p={self.probability:.2f} "
            f"n={self.candidates})"
        )


class JevPriceJudge:
    """One typed Jev call over a regex candidate list.

    Deliberately narrow: it only answers *which amount is the price of this
    product*, and it is consulted only when no JSON-LD / og / schema plane
    supplied a price. Currency, variant and needs_review logic stay untouched —
    this model does not repair those.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        endpoint: str = DEFAULT_ENDPOINT,
        model: str = DEFAULT_MODEL,
        timeout: int = DEFAULT_TIMEOUT,
        transport: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ):
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = model
        self.timeout = timeout
        self._transport = transport
        self.last_skip_reason: Optional[str] = None

    @classmethod
    def from_env(
        cls,
        env: Optional[Dict[str, str]] = None,
        *,
        flag: str = "SHADOW_WEB_JEV_PRICE",
    ) -> Optional["JevPriceJudge"]:
        env = dict(os.environ if env is None else env)
        if (env.get(flag) or "").strip().lower() not in TRUTHY:
            return None
        key = (env.get("SHADOW_WEB_JEV_API_KEY") or env.get("TYPESAFE_API_KEY") or "").strip()
        if not key:
            return None
        return cls(
            api_key=key,
            endpoint=env.get("SHADOW_WEB_JEV_ENDPOINT", DEFAULT_ENDPOINT),
            model=env.get("SHADOW_WEB_JEV_MODEL", DEFAULT_MODEL),
        )

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self._transport is not None:
            return self._transport(payload)
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    def judge(
        self,
        *,
        title: str,
        url: str,
        amounts: Sequence[str],
    ) -> Optional[PriceJudgement]:
        """None means "no usable answer" — the caller keeps its low-confidence row."""
        options: Dict[str, str] = {}
        for raw in amounts:
            key = str(raw).strip()
            if key and key not in options:
                options[key] = f"Amount exactly as it appears on the page: {key}"
        if not options:
            self.last_skip_reason = "no_candidates"
            return None
        try:
            payload = {
                "model": self.model,
                "state": {
                    "product": {"title": title, "url": url},
                    "candidate_amounts": list(options),
                },
                "questions": {
                    "is_offer": {
                        "type": "noul",
                        "instructions": (
                            "The candidate amounts were scraped from the product page above. "
                            "Is at least one of them the price of THIS product's offer?"
                        ),
                        "criteria": {
                            "true": "A candidate is this product's offer price",
                            "false": "No candidate is an offer price for this product",
                        },
                    },
                    "price_amount": {
                        "type": "choice",
                        "instructions": (
                            "Which candidate amount is the price of this product's offer? "
                            "Prefer the main buy-box price over shipping, instalments, "
                            "compare-at or unrelated amounts."
                        ),
                        "criteria": options,
                    },
                },
            }
            data = self._post(payload)
        except Exception:  # noqa: BLE001 — never break the price pipeline
            self.last_skip_reason = "transport_error"
            return None
        try:
            answers = data["answers"]
            chosen = str(answers["price_amount"]["choice"])
            amount = _coerce_amount(chosen)
            if amount is None:
                self.last_skip_reason = "unparseable_choice"
                return None
            judgement = PriceJudgement(
                raw=chosen,
                amount=amount,
                confidence=float(answers["price_amount"]["confidence"]),
                is_offer=float(answers["is_offer"]["noul"]),
                probability=float(
                    (answers["price_amount"].get("probabilities") or {}).get(chosen, 0.0)
                ),
                candidates=len(options),
                usage={k: int(v) for k, v in (data.get("usage") or {}).items()},
            )
        except (KeyError, TypeError, ValueError):
            self.last_skip_reason = "malformed_response"
            return None
        self.last_skip_reason = None
        return judgement


def _coerce_amount(raw: str) -> Optional[float]:
    """Parse a scraped amount string without importing the probe's parser."""
    cleaned = re.sub(r"[^\d.,]", "", raw or "")
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "") if cleaned.rfind(".") > cleaned.rfind(",") else cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


__all__ = [
    "JevDecision",
    "JevSelector",
    "JevPriceJudge",
    "PriceJudgement",
    "PRICE_THRESHOLD",
    "OFFER_FLOOR",
    "TOOL_CANDIDATES",
    "observation_from",
    "parse_action_lines",
]
