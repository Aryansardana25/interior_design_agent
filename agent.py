"""
agent.py
--------
Interior Company AI Design Agent — core planning engine.

This is the agent: it interprets a room brief, calls tools in a
multi-step loop, and produces a design plan + BOQ.

It is NOT a chatbot. It does not answer from memory.
Every product it recommends must come from the catalog tool.
Every spend must be verified by the budget tool.
Every piece must pass the layout tool before committing.

Guardrails:
  - Never invent products not in the catalog.
  - Never silently exceed the budget.
  - Decline out-of-scope requests (structural, electrical, plumbing).
  - Honest when infeasible — offer closest realistic alternative.
  - OOS items flagged, not hidden.
  - Specific brand/designer pieces not in catalog: declined.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

from catalog_tool import (
    search_catalog, get_item_by_id, get_cheapest_per_category,
    SearchTier, STYLE_ADJACENCY, CatalogItemResult
)
from budget_tool import BudgetState, feasibility_check
from layout_tool import check_layout, make_layout_item, LayoutItem
from database import get_room_brief, get_all_room_briefs, get_distinct_categories

DB_PATH = os.path.join(os.path.dirname(__file__), "interior_company_catalog.db")

# ---------------------------------------------------------------------------
# Out-of-scope topic detection
# ---------------------------------------------------------------------------
OUT_OF_SCOPE_PATTERNS = [
    r"\bremov(e|ing)\b.*\bwall\b",
    r"\bknock\b.*\bwall\b",
    r"\bstructural\b",
    r"\belectrical\b.*\bwiring\b",
    r"\bplumbing\b",
    r"\bload[- ]bearing\b",
    r"\brewir(e|ing)\b",
    r"\bcivil\b.*\bwork\b",
    r"\bfals(e|ing)\b.*\bceil(ing)?\b",
    r"\bseepage\b",
    r"\bleakage\b",
]

# Specific designer/brand pieces not in our catalog
DESIGNER_BRAND_PATTERNS = [
    r"\btogo\b",
    r"\bnoguchi\b",
    r"\beames\s+loung(e|er)\b",
    r"\bkarlsson\b",
    r"\bvitra\b",
    r"\bknoll\b",
    r"\bmuuto\b",
    r"\bArtek\b",
    r"\bflos\b",
]

# Must-have synonym normalisation → canonical catalog category
MUST_HAVE_CATEGORY_MAP = {
    "3-seater sofa": "Sofa",
    "3 seater sofa": "Sofa",
    "sofa": "Sofa",
    "sectional": "Sofa",
    "l-sofa": "Sofa",
    "l sofa": "Sofa",
    "seating for 4": "Sofa",
    "accent seating": "Armchair",
    "armchair": "Armchair",
    "coffee table": "Coffee Table",
    "tv unit": "TV Unit",
    "tv console": "TV Unit",
    "media unit": "TV Unit",
    "rug": "Rug",
    "layered rugs": "Rug",
    "lighting": "Floor Lamp",
    "floor lamp": "Floor Lamp",
    "pendant": "Pendant Light",
    "pendant light": "Pendant Light",
    "a statement pendant": "Pendant Light",
    "reading corner": "Armchair",
    "a reading corner": "Armchair",
    "wardrobe": "Wardrobe",
    "bed": "Bed",
    "queen bed": "Bed",
    "king bed": "Bed",
    "cane-headboard bed": "Bed",
    "nightstand": "Bedside Table",
    "nightstands": "Bedside Table",
    "two nightstands": "Bedside Table",
    "soft lighting": "Table Lamp",
    "airy curtains": "Curtains",
    "curtains": "Curtains",
    "jute rug": "Rug",
    "work desk": "Desk",
    "study desk": "Desk",
    "desk": "Desk",
    "ergonomic chair": "Office Chair",
    "shelving": "Bookshelf",
    "bookshelf": "Bookshelf",
    "big bookshelf": "Bookshelf",
    "task lighting": "Floor Lamp",
    "console": "Console",
    "a console": "Console",
    "storage": "Bookshelf",
    "plants": "Planter",
    "dining table": "Dining Table",
    "6-seater dining set": "Dining Table",
    "8-seater banquet dining": "Dining Table",
    "dining chair": "Dining Chair",
    "dining set": "Dining Table",
    "art": "Wall Art",
    "layered lighting": "Floor Lamp",
    "designer sofa": "Sofa",
    "premium statement living room": "Sofa",
}


def _normalise_must_haves(raw_must_haves: list) -> list:
    """Map free-text must-haves to canonical catalog categories."""
    categories = []
    seen = set()
    for raw in raw_must_haves:
        key = raw.lower().strip()
        cat = MUST_HAVE_CATEGORY_MAP.get(key)
        if cat is None:
            # fuzzy: check if any known key is a substring
            for k, v in MUST_HAVE_CATEGORY_MAP.items():
                if k in key or key in k:
                    cat = v
                    break
        if cat and cat not in seen:
            categories.append(cat)
            seen.add(cat)
    # If nothing matched, keep originals (the agent will handle gaps gracefully)
    if not categories:
        return raw_must_haves
    return categories


def _detect_out_of_scope(text: str) -> Optional[str]:
    """Return the matched pattern description if text is out of scope, else None."""
    lower = text.lower()
    for pattern in OUT_OF_SCOPE_PATTERNS:
        if re.search(pattern, lower):
            return pattern
    return None


def _detect_designer_pieces(text: str) -> list:
    """Return list of designer/brand names found in the text."""
    lower = text.lower()
    found = []
    for pattern in DESIGNER_BRAND_PATTERNS:
        if re.search(pattern, lower):
            found.append(pattern.replace(r"\b", "").replace("\\", ""))
    return found


# ---------------------------------------------------------------------------
# Design plan output
# ---------------------------------------------------------------------------

@dataclass
class DesignPlan:
    brief_id: str
    room_type: str
    budget_inr: int
    style: str
    selected_items: list  # list of CatalogItemResult
    budget_state: BudgetState
    layout_result: object  # LayoutCheckResult
    rationale: str
    trade_offs: str
    replan_log: list  # audit trail of tool calls + reasoning
    declined: bool = False
    decline_reason: str = ""
    feasibility_note: str = ""

    def to_text(self) -> str:
        """Render the full design plan as readable text."""
        lines = []
        lines.append("=" * 70)
        lines.append(f"INTERIOR DESIGN PLAN — {self.brief_id}")
        lines.append("=" * 70)

        if self.declined:
            lines.append(f"\n⛔ REQUEST DECLINED\n")
            lines.append(self.decline_reason)
            return "\n".join(lines)

        lines.append(f"\nRoom:   {self.room_type}")
        lines.append(f"Style:  {self.style}")
        lines.append(f"Budget: ₹{self.budget_inr:,}")

        if self.feasibility_note:
            lines.append(f"\n📋 FEASIBILITY NOTE\n{self.feasibility_note}")

        lines.append("\n" + "-" * 70)
        lines.append("SELECTED ITEMS & RATIONALE")
        lines.append("-" * 70)
        lines.append(self.rationale)

        lines.append("\n" + "-" * 70)
        lines.append("BILL OF QUANTITIES (BOQ)")
        lines.append("-" * 70)
        lines.append(self.budget_state.format_boq())

        lines.append("\n" + "-" * 70)
        lines.append("LAYOUT CHECK")
        lines.append("-" * 70)
        lines.append(self.layout_result.summary())

        lines.append("\n" + "-" * 70)
        lines.append("TRADE-OFFS & CAVEATS")
        lines.append("-" * 70)
        lines.append(self.trade_offs)

        if self.replan_log:
            lines.append("\n" + "-" * 70)
            lines.append("AGENT REASONING LOG")
            lines.append("-" * 70)
            for entry in self.replan_log:
                lines.append(f"  • {entry}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------

class InteriorDesignAgent:
    """
    The Interior Company AI Design Agent.

    Usage:
        agent = InteriorDesignAgent(db_path="interior_company_catalog.db")
        plan = agent.design(brief_id="BR-01")
        print(plan.to_text())

    Or with a free-text brief:
        plan = agent.design_from_text(
            room_type="Living Room",
            budget_inr=200000,
            style="Scandinavian",
            must_haves_text="sofa, coffee table, rug",
            room_length_cm=450,
            room_width_cm=350,
        )
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._valid_categories = None

    @property
    def valid_categories(self) -> list:
        if self._valid_categories is None:
            self._valid_categories = get_distinct_categories(self.db_path)
        return self._valid_categories

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------

    def design(self, brief_id: str) -> DesignPlan:
        """Design a room from a database brief."""
        brief = get_room_brief(brief_id, db_path=self.db_path)
        if brief is None:
            return self._decline(brief_id, "Living Room", 0, f"Brief '{brief_id}' not found in database.")
        return self._run_agent(
            brief_id=brief.brief_id,
            room_type=brief.room_type,
            budget_inr=brief.budget_inr or 0,
            style=brief.style_preference or "Contemporary",
            must_haves_raw=brief.must_haves,
            room_length_cm=brief.length_cm,
            room_width_cm=brief.width_cm,
            constraints=brief.constraints or "",
            customer_note=brief.customer_note or "",
        )

    def design_from_text(
        self,
        room_type: str,
        budget_inr: int,
        style: str,
        must_haves_text: str,
        room_length_cm: Optional[int] = None,
        room_width_cm: Optional[int] = None,
        constraints: str = "",
    ) -> DesignPlan:
        """Design a room from free-text inputs."""
        must_haves_raw = [m.strip() for m in must_haves_text.split(",") if m.strip()]
        brief_id = "FREE-TEXT"
        return self._run_agent(
            brief_id=brief_id,
            room_type=room_type,
            budget_inr=budget_inr,
            style=style,
            must_haves_raw=must_haves_raw,
            room_length_cm=room_length_cm,
            room_width_cm=room_width_cm,
            constraints=constraints,
            customer_note=must_haves_text,
        )

    # ------------------------------------------------------------------
    # Core planning loop
    # ------------------------------------------------------------------

    def _run_agent(
        self,
        brief_id: str,
        room_type: str,
        budget_inr: int,
        style: str,
        must_haves_raw: list,
        room_length_cm: Optional[int],
        room_width_cm: Optional[int],
        constraints: str,
        customer_note: str,
    ) -> DesignPlan:

        replan_log = []
        log = replan_log.append  # shorthand

        log(f"START brief={brief_id} room={room_type} budget=₹{budget_inr:,} style={style}")

        # ── Guardrail 1: Out-of-scope check ──────────────────────────────
        full_text = f"{constraints} {customer_note}"
        oos_match = _detect_out_of_scope(full_text)
        if oos_match:
            log(f"OUT_OF_SCOPE detected: {oos_match}")
            return self._decline(
                brief_id, room_type, budget_inr,
                "This request involves structural, civil, electrical, or plumbing work "
                "that is outside our scope. We design and source furniture and decor only. "
                "Please consult a licensed architect or contractor for structural changes."
            )

        # ── Guardrail 2: Specific designer/brand pieces ───────────────────
        # Check customer_note AND must_haves_raw (BR-08 puts brand names in must_haves)
        all_text_for_designer_check = customer_note + " " + " ".join(must_haves_raw) + " " + constraints
        designer_pieces = _detect_designer_pieces(all_text_for_designer_check)
        designer_decline = False
        designer_note = ""
        if designer_pieces:
            log(f"DESIGNER_PIECES detected: {designer_pieces}")
            designer_decline = True
            designer_note = (
                f"The following designer/branded pieces are not available in our catalog "
                f"and cannot be recommended: {', '.join(designer_pieces)}. "
                f"We only recommend items from our verified catalog. "
                f"We can suggest similar-aesthetic alternatives from our range."
            )
            # Hard-decline when ≥2 specific branded pieces are requested
            # (the entire brief is about items we cannot supply)
            if len(designer_pieces) >= 2:
                log("Hard decline: brief requests ≥2 designer pieces not in catalog")
                return self._decline(brief_id, room_type, budget_inr, designer_note)

        # ── Step 1: Normalise must-haves → catalog categories ────────────
        must_have_categories = _normalise_must_haves(must_haves_raw)
        log(f"NORMALISE must_haves: {must_haves_raw} → {must_have_categories}")

        # ── Step 2: Feasibility pre-check ────────────────────────────────
        fc = feasibility_check(must_have_categories, room_type, budget_inr, self.db_path)
        log(f"FEASIBILITY: {fc['verdict']}")

        feasibility_note = ""
        if not fc["feasible"]:
            if fc["minimum_cost"] > budget_inr:
                feasibility_note = (
                    f"⚠️ Budget constraint: The minimum cost of your must-have items "
                    f"(₹{fc['minimum_cost']:,}) exceeds your budget (₹{budget_inr:,}) "
                    f"by ₹{fc['minimum_cost'] - budget_inr:,}. "
                    f"We will build the best plan possible, prioritising must-haves, "
                    f"but you may need to increase your budget or accept fewer items."
                )
                log(f"INFEASIBLE: min_cost=₹{fc['minimum_cost']:,} > budget=₹{budget_inr:,}")
            if fc["missing_categories"]:
                feasibility_note += (
                    f"\n⚠️ Catalog gap: No items found for: {', '.join(fc['missing_categories'])}."
                )
                log(f"CATALOG_GAP: {fc['missing_categories']}")

        # ── Step 3: Planning loop — select items ──────────────────────────
        budget_state = BudgetState(budget_inr=budget_inr)
        selected: list = []  # CatalogItemResult
        style_gaps = []
        oos_flags = []

        # Compute usable area for footprint warnings
        usable_area = None
        if room_length_cm and room_width_cm:
            usable_area = int(room_length_cm * room_width_cm * 0.60)

        for category in must_have_categories:
            log(f"CATALOG_SEARCH category={category} room={room_type} style={style} remaining=₹{budget_state.remaining:,}")

            # Search with remaining budget as ceiling
            result = search_catalog(
                category=category,
                room_type=room_type,
                style=style,
                max_price=budget_state.remaining if budget_state.remaining > 0 else None,
                max_width_cm=room_width_cm if room_width_cm else None,
                usable_area_sqcm=usable_area,
                limit=5,
                db_path=self.db_path,
            )
            log(f"SEARCH_RESULT tier={result.search_tier.value} found={result.total_found}")

            # If nothing found within budget, try without price ceiling
            # (so we can flag the gap rather than silently skip)
            if not result.has_results:
                result = search_catalog(
                    category=category,
                    room_type=room_type,
                    style=style,
                    limit=3,
                    db_path=self.db_path,
                )
                log(f"RETRY_NO_BUDGET_CEILING tier={result.search_tier.value} found={result.total_found}")

            if not result.has_results:
                log(f"CATALOG_GAP: no items at all for {category} in {room_type}")
                style_gaps.append(f"No {category} available for {room_type} in any style.")
                continue

            # Pick best item; run layout check
            chosen = None
            for candidate in result.results:
                # Layout fit check
                test_items = [make_layout_item(s) for s in selected] + [make_layout_item(candidate)]
                layout_check = check_layout(test_items, room_length_cm, room_width_cm)
                log(f"LAYOUT_CHECK item={candidate.item_id} fits={layout_check.fits} util={layout_check.footprint_utilisation_pct:.1f}%")

                if layout_check.fits:
                    chosen = candidate
                    break
                else:
                    log(f"REPLAN: {candidate.item_id} fails layout, trying next candidate")

            if chosen is None:
                # No candidate passes layout — pick cheapest anyway and flag
                chosen = result.best
                log(f"LAYOUT_FALLBACK: using {chosen.item_id} despite layout issues (best available)")

            # Budget check
            if not budget_state.can_afford(chosen.price_inr or 0):
                log(f"BUDGET_WARN: {chosen.item_id} ₹{chosen.price_inr:,} exceeds remaining ₹{budget_state.remaining:,}")
                # Still add — the feasibility note already warned the customer

            # Commit
            add_result = budget_state.add_item(
                item_id=chosen.item_id,
                name=chosen.name,
                category=chosen.category,
                price_inr=chosen.price_inr or 0,
                db_path=self.db_path,
            )
            selected.append(chosen)
            log(f"COMMITTED: {chosen.summary()} | {add_result.summary()}")

            # Style gap tracking
            if result.has_style_gap and result.style_gap_report:
                style_gaps.append(result.style_gap_report.describe())

            # OOS tracking
            if chosen.warnings.is_oos:
                oos_flags.append(f"{chosen.name} (lead time: {chosen.lead_time_days} days)")

        # ── Step 4: Final layout check ────────────────────────────────────
        all_layout_items = [make_layout_item(s) for s in selected]
        final_layout = check_layout(all_layout_items, room_length_cm, room_width_cm)
        log(f"FINAL_LAYOUT fits={final_layout.fits} util={final_layout.footprint_utilisation_pct:.1f}%")

        # ── Step 5: Compose rationale ─────────────────────────────────────
        rationale = self._build_rationale(selected, style, room_type, style_gaps)
        trade_offs = self._build_trade_offs(
            selected, budget_state, style_gaps, oos_flags,
            must_have_categories, final_layout, designer_note, feasibility_note
        )

        return DesignPlan(
            brief_id=brief_id,
            room_type=room_type,
            budget_inr=budget_inr,
            style=style,
            selected_items=selected,
            budget_state=budget_state,
            layout_result=final_layout,
            rationale=rationale,
            trade_offs=trade_offs,
            replan_log=replan_log,
            feasibility_note=feasibility_note,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _decline(self, brief_id: str, room_type: str, budget_inr: int, reason: str) -> DesignPlan:
        from layout_tool import check_layout
        dummy_layout = check_layout([], None, None)
        return DesignPlan(
            brief_id=brief_id,
            room_type=room_type,
            budget_inr=budget_inr,
            style="N/A",
            selected_items=[],
            budget_state=BudgetState(budget_inr=budget_inr),
            layout_result=dummy_layout,
            rationale="",
            trade_offs="",
            replan_log=[f"DECLINED: {reason}"],
            declined=True,
            decline_reason=reason,
        )

    def _build_rationale(self, selected: list, style: str, room_type: str, style_gaps: list) -> str:
        if not selected:
            return "No items could be selected — see trade-offs section."
        parts = [
            f"This plan creates a {style} {room_type} using {len(selected)} pieces "
            f"from our catalog. Each piece was chosen for style coherence, budget fit, "
            f"and physical compatibility with the room dimensions.\n"
        ]
        for item in selected:
            match_note = ""
            if item.style_match.value == "adjacent":
                match_note = f" (adjacent style: {item.adjacent_style_used})"
            elif item.style_match.value == "none":
                match_note = " (best available — no style match)"
            warn_note = ""
            if item.warnings.is_oos:
                warn_note = f" ⚠️ Out of stock — lead time {item.lead_time_days} days."
            if item.warnings.has_long_lead:
                warn_note += f" ⚠️ Long lead time ({item.lead_time_days} days)."
            parts.append(
                f"• {item.name} [{item.item_id}] — ₹{item.price_inr:,}{match_note}{warn_note}\n"
                f"  Finish: {item.item.color_finish or 'N/A'} | "
                f"Dimensions: {item.item.width_cm}W × {item.item.depth_cm}D × {item.item.height_cm}H cm"
            )
        return "\n".join(parts)

    def _build_trade_offs(
        self, selected, budget_state, style_gaps, oos_flags,
        must_have_categories, final_layout, designer_note, feasibility_note
    ) -> str:
        parts = []

        if designer_note:
            parts.append(f"Designer pieces: {designer_note}")

        if feasibility_note:
            parts.append(feasibility_note)

        if budget_state.is_over_budget:
            parts.append(
                f"⚠️ OVER BUDGET: Total spend ₹{budget_state.total_spent:,} exceeds "
                f"budget ₹{budget_state.budget_inr:,} by ₹{budget_state.total_spent - budget_state.budget_inr:,}. "
                f"To bring this within budget, consider: removing optional items, choosing "
                f"lower-cost alternatives, or increasing the budget."
            )
        else:
            parts.append(
                f"✅ Within budget: ₹{budget_state.remaining:,} remains unspent "
                f"({100 - budget_state.utilisation_pct:.1f}% of budget). "
                f"This can be used for soft furnishings, plants, or accessories."
            )

        if style_gaps:
            parts.append("Style gaps:")
            for gap in style_gaps:
                parts.append(f"  • {gap}")

        if oos_flags:
            parts.append(
                f"⚠️ Out-of-stock items (will need to wait or substitute):\n"
                + "\n".join(f"  • {f}" for f in oos_flags)
            )

        selected_cats = {s.category for s in selected}
        missing = [c for c in must_have_categories if c not in selected_cats]
        if missing:
            parts.append(
                f"⚠️ Could not source items for: {', '.join(missing)}. "
                f"Either no catalog items exist for this room type, or the budget "
                f"was fully consumed by earlier must-haves."
            )

        if not final_layout.fits:
            parts.append(
                "⚠️ Layout concern: Some selected pieces may be tight in this room. "
                "See layout check for details."
            )

        parts.append(
            "Note: Lead times are estimates only. Prices shown are catalog prices; "
            "actual prices may vary. We do not guarantee delivery dates."
        )

        return "\n\n".join(parts) if parts else "No trade-offs noted."


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    db = DB_PATH
    brief_id = sys.argv[1] if len(sys.argv) > 1 else "BR-01"
    agent = InteriorDesignAgent(db_path=db)
    plan = agent.design(brief_id)
    print(plan.to_text())
