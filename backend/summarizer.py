"""Single-call AI summarization pipeline.

One Gemini call per company:
  - All boards' card data (titles + descriptions + week comments) in one block
  - Gemini returns JSON with note_body + optional section summaries

Pre-AI classification (classify_boards_activity):
  - significant: cards have comments this week OR completed cards → send to Gemini
  - moderate:    cards active in window but no comments/completions → send to Gemini
  - none:        no card activity at all → skip Gemini entirely
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .ai_client import chat
from .bigquery_client import BoardData, Card

logger = logging.getLogger(__name__)


@dataclass
class CompanyNote:
    note_body: str
    onboarding_summary: Optional[str]
    production_summary: Optional[str]
    risks_blockers: Optional[str]
    activity_level: str = "moderate"
    error_message: Optional[str] = None


# ── Pre-AI activity classifier ────────────────────────────────────────────────

def classify_boards_activity(boards: list[BoardData]) -> str:
    """
    Classify board activity BEFORE calling Gemini.

    Returns:
        "significant" — at least one card has comments posted this week,
                        OR at least one card was completed this week.
                        Strong signal: a human actively engaged.
        "moderate"    — cards exist in the window (updated/created/moved)
                        but no comments and none completed.
                        Light signal: something happened but no commentary.
        "none"        — no card activity in the reporting window at all.
                        No Gemini call will be made.
    """
    has_comments  = any(card.comments  for b in boards for card in b.cards)
    has_completed = any(card.completed for b in boards for card in b.cards)
    has_any_cards = any(b.cards        for b in boards)

    if has_comments or has_completed:
        return "significant"
    if has_any_cards:
        return "moderate"
    return "none"


# ── Payload card filter ───────────────────────────────────────────────────────

def _is_meaningful(card: Card) -> bool:
    """
    A card is worth including in the Gemini payload if it has real content signal.
    Filters out position changes, label tweaks, etc. that have no text to summarise.
    """
    # Comments posted this week are the strongest signal
    if card.comments:
        return True
    # Card marked done this week
    if card.completed:
        return True
    # Card has a non-trivial description (some context to work with)
    if (card.description or "").strip():
        return True
    return False


# ── Payload builder ───────────────────────────────────────────────────────────

_MAX_CARDS_PER_BOARD = 20
_MAX_DESC_CHARS = 200
_MAX_COMMENT_CHARS = 250
_MAX_COMMENTS_PER_CARD = 5


def _build_payload(boards: list[BoardData], week_start: datetime, week_end: datetime) -> str:
    """
    Build a single compact text block covering all boards.
    Only meaningful cards are included.
    """
    start_str = week_start.strftime("%-d %b")
    end_str   = week_end.strftime("%-d %b %Y")
    lines: list[str] = [f"Reporting window: {start_str} – {end_str}"]

    for board in boards:
        lines.append(f"\n--- {board.phase_type.upper()} BOARD: {board.board_title} ---")

        meaningful = [c for c in board.cards if _is_meaningful(c)]
        if not meaningful:
            lines.append("  (no significant activity this week)")
            continue

        for card in meaningful[:_MAX_CARDS_PER_BOARD]:
            status = "DONE" if card.completed else f"list: {card.list_name}"
            lines.append(f"\n[{status}] {card.title[:80]}")

            desc = (card.description or "").strip()[:_MAX_DESC_CHARS]
            if desc:
                lines.append(f"  desc: {desc}")

            if card.comments:
                lines.append("  updates this week:")
                for cm in card.comments[:_MAX_COMMENTS_PER_CARD]:
                    text = cm.text.strip()[:_MAX_COMMENT_CHARS]
                    lines.append(f"    • {text}")

    return "\n".join(lines)


# ── Single-call prompt ────────────────────────────────────────────────────────

_SYSTEM = """\
You are writing a weekly customer delivery note for HubSpot CRM.
You receive Orbit kanban board activity for one customer (one or more boards).

Rules:
- Comments posted this week are the primary signal of what happened.
- Card titles and descriptions give context — use them but don't recite them verbatim.
- Do not mention Orbit, board names, list names, card IDs, or internal tooling.
- Do not invent facts, delays, completions, or risks.
- Keep it concise — readable in 30 seconds.
- Only include a section if there is real content for it.
- Risks / Blockers only if genuine blockers, delays, or pending decisions exist.
- Each summary section (Onboarding, Production) must have at most 3 bullet points. Pick the 3 most important updates.

note_body must be valid HTML for HubSpot CRM. Use exactly this structure:

<p><strong>{header}</strong></p>

[If onboarding board has activity:]
<p><strong>Onboarding Summary</strong></p>
<ul>
<li>One sentence per key update. (max 3 bullets)</li>
</ul>

[If production board has activity:]
<p><strong>Production Summary</strong></p>
<ul>
<li>One sentence per key update. (max 3 bullets)</li>
</ul>

[Only if genuine risks or blockers:]
<p><strong>⚠️ Risks / Blockers</strong></p>
<ul>
<li>Specific blocker or risk.</li>
</ul>

[If activity was genuinely light, replace all sections with:]
<p>Activity was light this week — no significant updates.</p>

Return JSON only — no markdown, no code fences:
{
  "note_body": "<valid HTML as described above>",
  "onboarding_summary": "<plain text bullets, one per line, or null — max 3 lines>",
  "production_summary": "<plain text bullets, one per line, or null — max 3 lines>",
  "risks_blockers": "<plain text bullets, one per line, or null>"
}"""


_ORBIT_BOARD_BASE = "https://gravitee.info/orbit/board"


def _board_links_html(boards: list[BoardData]) -> str:
    """Build a compact HTML footer with links to each Orbit board."""
    items = "".join(
        f'<li><a href="{_ORBIT_BOARD_BASE}/{b.board_id}">'
        f'{b.board_title} ({b.phase_type.capitalize()})</a></li>'
        for b in boards
    )
    return f'<p><strong>Orbit Boards</strong></p><ul>{items}</ul>'


# ── Orchestrator ──────────────────────────────────────────────────────────────

async def generate_company_note(
    company_name: str,
    boards: list[BoardData],
    week_start: datetime,
    week_end: datetime,
    activity_level: str,
) -> Optional[CompanyNote]:
    """
    Generate an AI summary for a company.

    activity_level is pre-computed by the caller via classify_boards_activity()
    and must be "significant" or "moderate" — callers should never pass "none"
    (those are filtered out before reaching this function).

    Returns a CompanyNote on success, or a fallback CompanyNote on AI failure.
    """
    start_str = week_start.strftime("%-d %b %Y")
    end_str   = week_end.strftime("%-d %b %Y")
    header = f"[Auto-generated | Orbit Notes | {start_str} - {end_str}]"

    payload = _build_payload(boards, week_start, week_end)

    user_msg = f"""\
Customer: {company_name}
Header (insert verbatim as the {{header}} placeholder): {header}

Board activity:
{payload}"""

    board_links = _board_links_html(boards)

    try:
        raw = await chat(_SYSTEM, user_msg, temperature=0.2, expect_json=True)
        data = json.loads(raw)
        base_body = data.get("note_body") or f"<p><strong>{header}</strong></p><p>(No summary generated)</p>"
        return CompanyNote(
            note_body=base_body + board_links,
            onboarding_summary=data.get("onboarding_summary") or None,
            production_summary=data.get("production_summary") or None,
            risks_blockers=data.get("risks_blockers") or None,
            activity_level=activity_level,
        )
    except Exception as exc:
        error_detail = f"{type(exc).__name__}: {exc}"
        logger.exception("Summary generation failed for %s — %s", company_name, error_detail)
        return CompanyNote(
            note_body=(
                f"<p><strong>{header}</strong></p>"
                f"<p>⚠️ Summary generation failed.</p>"
                f"<p><strong>Error:</strong> <code>{error_detail}</code></p>"
                f"{board_links}"
            ),
            onboarding_summary=None,
            production_summary=None,
            risks_blockers=None,
            activity_level=activity_level,
            error_message=error_detail,
        )
