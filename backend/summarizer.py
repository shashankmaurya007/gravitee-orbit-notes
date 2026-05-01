"""Single-call AI summarization pipeline.

One Gemini call per company:
  - All boards' card data (titles + descriptions + week comments) in one block
  - Gemini returns JSON with note_body + optional section summaries
  - Companies with no meaningful activity are skipped entirely (no API call)
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


# ── Meaningful-activity filter ────────────────────────────────────────────────

def _is_meaningful(card: Card) -> bool:
    """
    A card is worth including if it has real content signal.
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


def _has_meaningful_content(boards: list[BoardData]) -> bool:
    """Return True if any board has at least one meaningful card."""
    return any(_is_meaningful(c) for b in boards for c in b.cards)


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

note_body must be valid HTML for HubSpot CRM. Use exactly this structure:

<p><strong>{header}</strong></p>

[If onboarding board has activity:]
<p><strong>Onboarding Summary</strong></p>
<ul>
<li>One sentence per key update.</li>
</ul>

[If production board has activity:]
<p><strong>Production Summary</strong></p>
<ul>
<li>One sentence per key update.</li>
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
  "onboarding_summary": "<plain text bullets, one per line, or null>",
  "production_summary": "<plain text bullets, one per line, or null>",
  "risks_blockers": "<plain text bullets, one per line, or null>"
}"""


# ── Orchestrator ──────────────────────────────────────────────────────────────

async def generate_company_note(
    company_name: str,
    boards: list[BoardData],
    week_start: datetime,
    week_end: datetime,
) -> Optional[CompanyNote]:
    """
    Returns None if the company has no meaningful activity this week
    (no Gemini call made).  Returns a CompanyNote on success.
    """
    if not _has_meaningful_content(boards):
        logger.info(
            "%s: no meaningful card activity this week — skipping AI call",
            company_name,
        )
        return None

    start_str = week_start.strftime("%-d %b %Y")
    end_str   = week_end.strftime("%-d %b %Y")
    header = f"[Auto-generated | Orbit Notes | {start_str} - {end_str}]"

    payload = _build_payload(boards, week_start, week_end)

    user_msg = f"""\
Customer: {company_name}
Header (insert verbatim as the {{header}} placeholder): {header}

Board activity:
{payload}"""

    try:
        raw = await chat(_SYSTEM, user_msg, temperature=0.2, expect_json=True)
        data = json.loads(raw)
        return CompanyNote(
            note_body=data.get("note_body") or f"<p><strong>{header}</strong></p><p>(No summary generated)</p>",
            onboarding_summary=data.get("onboarding_summary") or None,
            production_summary=data.get("production_summary") or None,
            risks_blockers=data.get("risks_blockers") or None,
        )
    except Exception:
        logger.exception("Summary generation failed for %s", company_name)
        return CompanyNote(
            note_body=f"<p><strong>{header}</strong></p><p>Summary generation failed — please retry.</p>",
            onboarding_summary=None,
            production_summary=None,
            risks_blockers=None,
        )
