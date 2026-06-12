"""
budget_tool.py
--------------
Budget calculator for the Interior Company AI Design Agent.

Tracks selected items, computes running total, compares to budget,
and reports remaining spend. The agent must call this after each
item selection — never silently exceed the budget.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from catalog_tool import get_item_by_id, CatalogItemResult


@dataclass
class BOQLine:
    """One line in the Bill of Quantities."""
    item_id: str
    name: str
    category: str
    price_inr: int
    quantity: int = 1
    notes: str = ""

    @property
    def line_total(self) -> int:
        return self.price_inr * self.quantity


@dataclass
class BudgetState:
    """
    Live budget state for the agent's planning loop.

    The agent holds one BudgetState for the duration of a brief.
    Call add_item() to commit a selection, remove_item() to back out,
    and check() to see the current position at any time.
    """
    budget_inr: int
    lines: list = field(default_factory=list)
    _item_ids: set = field(default_factory=set, repr=False)

    # ------------------------------------------------------------------
    # Computed properties
    # ------------------------------------------------------------------
    @property
    def total_spent(self) -> int:
        return sum(line.line_total for line in self.lines)

    @property
    def remaining(self) -> int:
        return self.budget_inr - self.total_spent

    @property
    def is_over_budget(self) -> bool:
        return self.total_spent > self.budget_inr

    @property
    def utilisation_pct(self) -> float:
        if self.budget_inr == 0:
            return 0.0
        return (self.total_spent / self.budget_inr) * 100

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------
    def add_item(
        self,
        item_id: str,
        name: str,
        category: str,
        price_inr: int,
        quantity: int = 1,
        notes: str = "",
        db_path: Optional[str] = None,
    ) -> "BudgetCheckResult":
        """
        Add an item to the plan and return the resulting budget state.

        If adding this item would exceed the budget, the item is still
        added but the result is flagged as over_budget so the agent
        can decide whether to swap it out or report the shortfall.

        The agent must NEVER silently ignore an over_budget result.
        """
        # Validate the item exists in the catalog
        if db_path:
            catalog_item = get_item_by_id(item_id, db_path=db_path)
            if catalog_item is None:
                return BudgetCheckResult(
                    success=False,
                    error=f"Item {item_id} not found in catalog. Only catalog items may be added.",
                    budget_inr=self.budget_inr,
                    total_spent=self.total_spent,
                    remaining=self.remaining,
                    is_over_budget=self.is_over_budget,
                    lines=list(self.lines),
                )

        line = BOQLine(item_id=item_id, name=name, category=category, price_inr=price_inr, quantity=quantity, notes=notes)
        self.lines.append(line)
        self._item_ids.add(item_id)

        return BudgetCheckResult(
            success=True,
            error=None,
            budget_inr=self.budget_inr,
            total_spent=self.total_spent,
            remaining=self.remaining,
            is_over_budget=self.is_over_budget,
            lines=list(self.lines),
        )

    def remove_item(self, item_id: str) -> bool:
        """Remove an item by item_id. Returns True if removed, False if not found."""
        before = len(self.lines)
        self.lines = [l for l in self.lines if l.item_id != item_id]
        self._item_ids.discard(item_id)
        return len(self.lines) < before

    def has_item(self, item_id: str) -> bool:
        return item_id in self._item_ids

    def has_category(self, category: str) -> bool:
        return any(l.category == category for l in self.lines)

    def check(self) -> "BudgetCheckResult":
        """Return the current budget position without mutating state."""
        return BudgetCheckResult(
            success=True,
            error=None,
            budget_inr=self.budget_inr,
            total_spent=self.total_spent,
            remaining=self.remaining,
            is_over_budget=self.is_over_budget,
            lines=list(self.lines),
        )

    def can_afford(self, price_inr: int) -> bool:
        """True if adding this price would not exceed the budget."""
        return (self.total_spent + price_inr) <= self.budget_inr

    def headroom(self, price_inr: int) -> int:
        """How many rupees over/under budget adding this item would put us. Negative = over."""
        return self.remaining - price_inr

    def boq_lines(self) -> list:
        """Ordered list of BOQ lines (same order as added)."""
        return list(self.lines)

    def format_boq(self) -> str:
        """Human-readable BOQ table for the design plan output."""
        if not self.lines:
            return "No items selected yet."
        lines = []
        lines.append(f"{'#':<4} {'Item ID':<10} {'Name':<40} {'Category':<20} {'Price (₹)':>12}")
        lines.append("-" * 90)
        running = 0
        for i, line in enumerate(self.lines, 1):
            running += line.line_total
            lines.append(
                f"{i:<4} {line.item_id:<10} {line.name:<40} {line.category:<20} {line.price_inr:>12,}"
            )
        lines.append("-" * 90)
        lines.append(f"{'TOTAL':<75} {self.total_spent:>12,}")
        lines.append(f"{'BUDGET':<75} {self.budget_inr:>12,}")
        remaining_label = "REMAINING" if self.remaining >= 0 else "OVER BUDGET BY"
        lines.append(f"{remaining_label:<75} {abs(self.remaining):>12,}")
        if self.is_over_budget:
            lines.append("\n⚠️  OVER BUDGET — agent must flag this and offer trade-offs.")
        else:
            lines.append(f"\n✅ Within budget ({self.utilisation_pct:.1f}% utilised)")
        return "\n".join(lines)


@dataclass
class BudgetCheckResult:
    """Result of a budget operation. Returned by add_item() and check()."""
    success: bool
    error: Optional[str]
    budget_inr: int
    total_spent: int
    remaining: int
    is_over_budget: bool
    lines: list

    def summary(self) -> str:
        status = "OVER BUDGET" if self.is_over_budget else "within budget"
        return (
            f"Budget: ₹{self.budget_inr:,} | Spent: ₹{self.total_spent:,} | "
            f"Remaining: ₹{self.remaining:,} | {status}"
        )


# ---------------------------------------------------------------------------
# Standalone feasibility check (before planning loop starts)
# ---------------------------------------------------------------------------

def feasibility_check(
    must_have_categories: list,
    room_type: str,
    budget_inr: int,
    db_path: str,
) -> dict:
    """
    Pre-flight check: can the must-haves physically fit in budget?

    Fetches the cheapest in-stock, priced item for each must-have
    category and sums them. If the minimum cost exceeds the budget,
    the agent must report this BEFORE building a plan, not after.

    Returns:
        feasible: bool — True if minimum cost ≤ budget
        minimum_cost: int
        remaining_after_must_haves: int
        per_category: dict mapping category → (item_id, price) or None
        missing_categories: list of categories with no catalog items at all
    """
    from catalog_tool import get_cheapest_per_category
    cheapest = get_cheapest_per_category(must_have_categories, room_type, db_path=db_path)

    minimum_cost = 0
    per_category = {}
    missing_categories = []

    for cat, result in cheapest.items():
        if result is None:
            missing_categories.append(cat)
            per_category[cat] = None
        else:
            minimum_cost += result.price_inr
            per_category[cat] = (result.item_id, result.price_inr, result.name)

    feasible = minimum_cost <= budget_inr and not missing_categories

    return {
        "feasible": feasible,
        "minimum_cost": minimum_cost,
        "budget_inr": budget_inr,
        "remaining_after_must_haves": budget_inr - minimum_cost,
        "per_category": per_category,
        "missing_categories": missing_categories,
        "verdict": (
            "OK — must-haves fit within budget at minimum prices."
            if feasible
            else (
                f"INFEASIBLE — minimum cost of must-haves is ₹{minimum_cost:,} "
                f"which exceeds the budget of ₹{budget_inr:,} by ₹{minimum_cost - budget_inr:,}."
                if minimum_cost > budget_inr
                else f"CATALOG GAP — no items found for: {', '.join(missing_categories)}"
            )
        ),
    }


if __name__ == "__main__":
    # Quick smoke test
    state = BudgetState(budget_inr=250000)
    r = state.add_item("SOF-001", "Nordby 3-Seater", "Sofa", 58000)
    print(r.summary())
    r = state.add_item("CFT-001", "Oslo Coffee Table", "Coffee Table", 16000)
    print(r.summary())
    r = state.add_item("TVU-001", "Floating TV Console", "TV Unit", 24000)
    print(r.summary())
    print()
    print(state.format_boq())
    print()
    fc = feasibility_check(
        ["Sofa", "Coffee Table", "TV Unit", "Rug", "Floor Lamp"],
        "Living Room", 250000,
        "interior_company_catalog.db"
    )
    print("Feasibility check:")
    print(fc["verdict"])
    for cat, val in fc["per_category"].items():
        if val:
            print(f"  {cat}: {val[0]} ₹{val[1]:,}")
        else:
            print(f"  {cat}: CATALOG GAP")
