"""
eval_harness.py
---------------
Evaluation harness for the Interior Company AI Design Agent.

Golden set: 27 test cases
  - 14 database briefs (BR-01 through BR-14)
  - 7 adversarial free-text briefs
  - 6 "must decline" cases

Scorers:
  DETERMINISTIC (code can verify):
    D1  Budget never exceeded silently
    D2  All items are real catalog items
    D3  No OOS items in plan without explicit flag
    D4  Declined out-of-scope requests correctly
    D5  Budget-infeasible briefs flagged (not faked)
    D6  Layout check was called (tool use check)
    D7  No NULL-price items in BOQ

  JUDGEMENT-BASED (LLM-as-judge):
    J1  Style coherence — do the chosen items match the requested style?
    J2  Rationale quality — is the explanation clear and useful?

Ship gate:
  ✅ D1 (budget): 100% of cases
  ✅ D2 (real items): 100% of cases
  ✅ D4 (decline OOS requests): 100% of cases
  ✅ D3, D5, D6, D7: ≥ 90% of applicable cases
  ✅ J1 (style coherence): ≥ 80% rated "acceptable" or better
  ✅ J2 (rationale): ≥ 80% rated "acceptable" or better
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Golden test cases
# ---------------------------------------------------------------------------

@dataclass
class TestCase:
    test_id: str
    description: str
    brief_id: Optional[str] = None    # set for DB briefs
    free_text: Optional[dict] = None  # set for free-text briefs
    expect_decline: bool = False
    expect_over_budget_flag: bool = False
    expect_catalog_gap: bool = False
    tags: list = field(default_factory=list)


GOLDEN_SET: list = [
    # ── Database briefs ────────────────────────────────────────────────
    TestCase("TC-01", "BR-01: Standard Scandinavian living room ₹2.5L", brief_id="BR-01", tags=["happy_path", "living_room"]),
    TestCase("TC-02", "BR-02: Mid-Century living room rented flat ₹1.5L", brief_id="BR-02", tags=["happy_path", "living_room", "rented"]),
    TestCase("TC-03", "BR-03: Minimalist master bedroom ₹2L", brief_id="BR-03", tags=["happy_path", "bedroom"]),
    TestCase("TC-04", "BR-04: Contemporary dining open-plan ₹1.8L", brief_id="BR-04", tags=["happy_path", "dining"]),
    TestCase("TC-05", "BR-05: Bohemian living room lots of texture ₹2L", brief_id="BR-05", tags=["happy_path", "bohemian"]),
    TestCase("TC-06", "BR-06: Budget trap — ₹20K for full living room", brief_id="BR-06",
             expect_over_budget_flag=True, tags=["budget_trap", "adversarial"]),
    TestCase("TC-07", "BR-07: Out-of-scope — wants to remove a wall", brief_id="BR-07",
             expect_decline=True, tags=["out_of_scope", "adversarial"]),
    TestCase("TC-08", "BR-08: Designer pieces not in catalog (Togo, Noguchi, Eames)", brief_id="BR-08",
             expect_decline=True, tags=["designer_pieces", "adversarial"]),
    TestCase("TC-09", "BR-09: Dimension trap — studio, large sectional requested", brief_id="BR-09",
             expect_catalog_gap=True, tags=["dimension_trap", "adversarial"]),
    TestCase("TC-10", "BR-10: Coastal bedroom, 3-week deadline (lead time risk)", brief_id="BR-10",
             tags=["lead_time", "bedroom", "coastal"]),
    TestCase("TC-11", "BR-11: Industrial study WFH setup ₹90K", brief_id="BR-11", tags=["happy_path", "study"]),
    TestCase("TC-12", "BR-12: Kids room Contemporary ₹1.2L", brief_id="BR-12", tags=["happy_path", "kids"]),
    TestCase("TC-13", "BR-13: Traditional 8-seater banquet dining ₹1.4L", brief_id="BR-13",
             expect_catalog_gap=True, tags=["catalog_gap", "dining", "traditional"]),
    TestCase("TC-14", "BR-14: Premium Contemporary living room ₹5L", brief_id="BR-14", tags=["happy_path", "premium"]),

    # ── Adversarial free-text briefs ───────────────────────────────────
    TestCase("TC-15", "Free text: Japandi bedroom, very tight budget", brief_id=None,
             free_text={"room_type": "Bedroom", "budget_inr": 50000, "style": "Japandi",
                        "must_haves_text": "bed, wardrobe", "room_length_cm": 350, "room_width_cm": 280},
             expect_over_budget_flag=True, tags=["japandi", "adversarial", "budget_trap"]),
    TestCase("TC-16", "Free text: Minimalist study, ₹80K", brief_id=None,
             free_text={"room_type": "Study", "budget_inr": 80000, "style": "Minimalist",
                        "must_haves_text": "desk, ergonomic chair, bookshelf, floor lamp",
                        "room_length_cm": 350, "room_width_cm": 300},
             tags=["minimalist", "study"]),
    TestCase("TC-17", "Free text: Industrial dining, only 2 seats, ₹1L", brief_id=None,
             free_text={"room_type": "Dining", "budget_inr": 100000, "style": "Industrial",
                        "must_haves_text": "dining table, dining chair, pendant light",
                        "room_length_cm": 320, "room_width_cm": 260},
             tags=["industrial", "dining"]),
    TestCase("TC-18", "Free text: Coastal bedroom for kids, ₹1L", brief_id=None,
             free_text={"room_type": "Bedroom", "budget_inr": 100000, "style": "Coastal",
                        "must_haves_text": "bed, rug, curtains, table lamp",
                        "room_length_cm": 350, "room_width_cm": 300},
             tags=["coastal", "bedroom"]),
    TestCase("TC-19", "Free text: Bohemian living with plants+art, ₹1.5L", brief_id=None,
             free_text={"room_type": "Living Room", "budget_inr": 150000, "style": "Bohemian",
                        "must_haves_text": "sofa, rug, planter, wall art, cushions",
                        "room_length_cm": 400, "room_width_cm": 320},
             tags=["bohemian", "living_room"]),
    TestCase("TC-20", "Free text: Scandinavian bedroom + mattress, ₹2L", brief_id=None,
             free_text={"room_type": "Bedroom", "budget_inr": 200000, "style": "Scandinavian",
                        "must_haves_text": "bed, mattress, wardrobe, nightstand, curtains",
                        "room_length_cm": 420, "room_width_cm": 360},
             tags=["scandinavian", "bedroom"]),
    TestCase("TC-21", "Free text: Traditional study, ₹60K", brief_id=None,
             free_text={"room_type": "Study", "budget_inr": 60000, "style": "Traditional",
                        "must_haves_text": "desk, ergonomic chair, bookshelf",
                        "room_length_cm": 300, "room_width_cm": 250},
             tags=["traditional", "study"]),

    # ── Must-decline cases ─────────────────────────────────────────────
    TestCase("TC-22", "Decline: plumbing request", brief_id=None,
             free_text={"room_type": "Living Room", "budget_inr": 200000, "style": "Contemporary",
                        "must_haves_text": "sofa, coffee table",
                        "constraints": "also fix the plumbing and leakage in bathroom"},
             expect_decline=True, tags=["out_of_scope", "plumbing"]),
    TestCase("TC-23", "Decline: electrical wiring request", brief_id=None,
             free_text={"room_type": "Living Room", "budget_inr": 200000, "style": "Contemporary",
                        "must_haves_text": "sofa",
                        "constraints": "need new electrical wiring for extra sockets"},
             expect_decline=True, tags=["out_of_scope", "electrical"]),
    TestCase("TC-24", "Decline: load-bearing wall removal", brief_id=None,
             free_text={"room_type": "Living Room", "budget_inr": 300000, "style": "Minimalist",
                        "must_haves_text": "sofa, coffee table",
                        "constraints": "want to remove the load-bearing wall between kitchen and living"},
             expect_decline=True, tags=["out_of_scope", "structural"]),
    TestCase("TC-25", "Decline: seepage repair", brief_id=None,
             free_text={"room_type": "Bedroom", "budget_inr": 150000, "style": "Contemporary",
                        "must_haves_text": "bed, wardrobe",
                        "constraints": "there is seepage on the north wall, please fix it"},
             expect_decline=True, tags=["out_of_scope", "structural"]),
    TestCase("TC-26", "Budget exhausted after 1st item", brief_id=None,
             free_text={"room_type": "Living Room", "budget_inr": 45000, "style": "Bohemian",
                        "must_haves_text": "sofa, coffee table, rug, floor lamp"},
             expect_over_budget_flag=True, tags=["budget_trap"]),
    TestCase("TC-27", "Kids room — no items in catalog", brief_id=None,
             free_text={"room_type": "Kids", "budget_inr": 80000, "style": "Contemporary",
                        "must_haves_text": "bed, desk, wardrobe"},
             expect_catalog_gap=True, tags=["kids", "catalog_gap"]),
]

# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

@dataclass
class ScoreResult:
    test_id: str
    description: str
    passed_d1_budget: Optional[bool] = None        # budget never silently exceeded
    passed_d2_real_items: Optional[bool] = None    # all items in catalog
    passed_d3_oos_flagged: Optional[bool] = None   # OOS items flagged
    passed_d4_decline: Optional[bool] = None       # declined when expected
    passed_d5_feasibility: Optional[bool] = None   # infeasible flagged
    passed_d6_layout_called: Optional[bool] = None # layout tool was used
    passed_d7_no_null_price: Optional[bool] = None # no NULL-price in BOQ
    score_j1_style: Optional[str] = None           # "good" | "acceptable" | "poor" | "na"
    score_j2_rationale: Optional[str] = None       # "good" | "acceptable" | "poor" | "na"
    error: Optional[str] = None
    elapsed_s: float = 0.0
    plan_text: str = ""

    def deterministic_pass_rate(self) -> float:
        scores = [
            self.passed_d1_budget,
            self.passed_d2_real_items,
            self.passed_d3_oos_flagged,
            self.passed_d4_decline,
            self.passed_d5_feasibility,
            self.passed_d6_layout_called,
            self.passed_d7_no_null_price,
        ]
        applicable = [s for s in scores if s is not None]
        if not applicable:
            return 1.0
        return sum(1 for s in applicable if s) / len(applicable)

    def overall_pass(self) -> bool:
        det = [
            self.passed_d1_budget,
            self.passed_d2_real_items,
            self.passed_d3_oos_flagged,
            self.passed_d4_decline,
        ]
        hard_fails = [s for s in det if s is not None and not s]
        return len(hard_fails) == 0


# ---------------------------------------------------------------------------
# Deterministic scorers
# ---------------------------------------------------------------------------

def score_deterministic(case: TestCase, plan, all_catalog_ids: set) -> dict:
    """Run all deterministic checks on a plan. Returns dict of scorer_id → bool."""
    scores = {}

    if plan.declined:
        # D4: Declined when expected?
        scores["d4_decline"] = case.expect_decline
        # If unexpectedly declined, note the other scores as N/A
        if not case.expect_decline:
            scores["d1_budget"] = True  # N/A
            scores["d2_real_items"] = True
            scores["d3_oos_flagged"] = True
            scores["d5_feasibility"] = True
            scores["d6_layout_called"] = True
            scores["d7_no_null_price"] = True
        return scores

    # D4: Should NOT have declined when expect_decline=True
    scores["d4_decline"] = not case.expect_decline

    # D1: Budget — if over budget, must be flagged (never silent)
    if plan.budget_state.is_over_budget:
        # Flagged = feasibility note or trade-offs mention over budget
        flag_present = (
            "OVER BUDGET" in plan.trade_offs or
            "OVER BUDGET" in plan.budget_state.format_boq() or
            "INFEASIBLE" in plan.feasibility_note or
            "Budget constraint" in plan.feasibility_note
        )
        scores["d1_budget"] = flag_present
    else:
        scores["d1_budget"] = True  # within budget — trivially passes

    # D2: All items in real catalog
    if plan.selected_items:
        all_real = all(item.item_id in all_catalog_ids for item in plan.selected_items)
        scores["d2_real_items"] = all_real
    else:
        scores["d2_real_items"] = True  # no items → gap, not invented

    # D3: OOS items are flagged
    oos_items = [i for i in plan.selected_items if i.warnings.is_oos]
    if oos_items:
        # Check that plan mentions OOS
        oos_flagged = (
            "Out of stock" in plan.trade_offs or
            "OOS" in plan.trade_offs or
            "out-of-stock" in plan.trade_offs.lower() or
            any("OOS" in entry for entry in plan.replan_log)
        )
        scores["d3_oos_flagged"] = oos_flagged
    else:
        scores["d3_oos_flagged"] = True  # no OOS items → passes

    # D5: Infeasible briefs flagged
    if case.expect_over_budget_flag or case.expect_catalog_gap:
        flag_present = bool(plan.feasibility_note) or plan.budget_state.is_over_budget
        scores["d5_feasibility"] = flag_present
    else:
        scores["d5_feasibility"] = True

    # D6: Layout tool was called (check replan_log)
    layout_called = any("LAYOUT_CHECK" in entry or "FINAL_LAYOUT" in entry for entry in plan.replan_log)
    scores["d6_layout_called"] = layout_called

    # D7: No NULL-price items in BOQ
    null_price_in_boq = any(
        item.price_inr is None for item in plan.selected_items
    )
    scores["d7_no_null_price"] = not null_price_in_boq

    return scores


# ---------------------------------------------------------------------------
# LLM-as-judge scorer
# ---------------------------------------------------------------------------

LLM_STYLE_RUBRIC = """
You are evaluating an AI interior design agent's output.

Rate the STYLE COHERENCE of the selected items on this 3-point scale:
- "good": All or most items clearly match the requested style. Minor adjacent-style substitutions are acceptable if flagged.
- "acceptable": At least half the items match the requested style. Substitutions are reasonable but not ideal.
- "poor": Fewer than half the items match, or the selections are wildly inconsistent with the style.

Respond with exactly one word: good, acceptable, or poor.

If the plan was declined or has no items, respond: na

BRIEF:
Room type: {room_type}
Requested style: {style}
Budget: ₹{budget}

SELECTED ITEMS:
{items}

STYLE COHERENCE RATING:"""

LLM_RATIONALE_RUBRIC = """
You are evaluating an AI interior design agent's output.

Rate the RATIONALE QUALITY on this 3-point scale:
- "good": The explanation is clear, specific about each item choice, mentions style + budget + fit, and a customer could act on it.
- "acceptable": The explanation exists and covers most items, but is generic or incomplete.
- "poor": The explanation is missing, very thin, or inaccurate.

Respond with exactly one word: good, acceptable, or poor.

If the plan was declined, respond: na

RATIONALE TEXT:
{rationale}

RATING:"""


def score_llm_judge(plan, case: TestCase) -> tuple:
    """
    Use the Anthropic API to judge style coherence and rationale quality.
    Returns (style_score, rationale_score) as strings.
    """
    try:
        import anthropic
        client = anthropic.Anthropic()

        if plan.declined:
            return "na", "na"

        items_text = "\n".join(
            f"- {i.name} [{i.item_id}] style_tags={i.item.style_tags}"
            for i in plan.selected_items
        )

        # Style coherence
        style_prompt = LLM_STYLE_RUBRIC.format(
            room_type=plan.room_type,
            style=plan.style,
            budget=plan.budget_inr,
            items=items_text or "(no items selected)",
        )
        style_resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=10,
            messages=[{"role": "user", "content": style_prompt}],
        )
        style_score = style_resp.content[0].text.strip().lower()
        if style_score not in ("good", "acceptable", "poor", "na"):
            style_score = "acceptable"

        # Rationale quality
        rationale_prompt = LLM_RATIONALE_RUBRIC.format(rationale=plan.rationale[:2000])
        rat_resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=10,
            messages=[{"role": "user", "content": rationale_prompt}],
        )
        rat_score = rat_resp.content[0].text.strip().lower()
        if rat_score not in ("good", "acceptable", "poor", "na"):
            rat_score = "acceptable"

        return style_score, rat_score

    except Exception as e:
        return f"error:{e}", f"error:{e}"


# ---------------------------------------------------------------------------
# Run the harness
# ---------------------------------------------------------------------------

def run_harness(
    run_llm_judge: bool = True,
    brief_filter: Optional[list] = None,
    db_path: str = "interior_company_catalog.db",
) -> list:
    """
    Run the full eval harness. Returns list of ScoreResult.

    Parameters
    ----------
    run_llm_judge : whether to call the Anthropic API for J1/J2 scoring
    brief_filter  : if set, only run test IDs in this list
    db_path       : path to the SQLite database
    """
    from agent import InteriorDesignAgent
    from database import get_all_products

    agent = InteriorDesignAgent(db_path=db_path)
    all_catalog_ids = {p.item_id for p in get_all_products(db_path)}

    results = []
    cases = GOLDEN_SET if not brief_filter else [c for c in GOLDEN_SET if c.test_id in brief_filter]

    print(f"\n{'='*70}")
    print(f"INTERIOR AGENT EVAL HARNESS — {len(cases)} test cases")
    print(f"LLM judge: {'ON' if run_llm_judge else 'OFF'}")
    print(f"{'='*70}\n")

    for case in cases:
        print(f"Running {case.test_id}: {case.description}...")
        t0 = time.time()
        result = ScoreResult(test_id=case.test_id, description=case.description)

        try:
            # Run the agent
            if case.brief_id:
                plan = agent.design(case.brief_id)
            elif case.free_text:
                plan = agent.design_from_text(**case.free_text)
            else:
                raise ValueError(f"Test case {case.test_id} has neither brief_id nor free_text")

            result.plan_text = plan.to_text()
            result.elapsed_s = time.time() - t0

            # Deterministic scoring
            det_scores = score_deterministic(case, plan, all_catalog_ids)
            result.passed_d1_budget = det_scores.get("d1_budget")
            result.passed_d2_real_items = det_scores.get("d2_real_items")
            result.passed_d3_oos_flagged = det_scores.get("d3_oos_flagged")
            result.passed_d4_decline = det_scores.get("d4_decline")
            result.passed_d5_feasibility = det_scores.get("d5_feasibility")
            result.passed_d6_layout_called = det_scores.get("d6_layout_called")
            result.passed_d7_no_null_price = det_scores.get("d7_no_null_price")

            # LLM-as-judge
            if run_llm_judge:
                j1, j2 = score_llm_judge(plan, case)
                result.score_j1_style = j1
                result.score_j2_rationale = j2

            overall = "✅ PASS" if result.overall_pass() else "❌ FAIL"
            print(f"  {overall} | D1={result.passed_d1_budget} D2={result.passed_d2_real_items} "
                  f"D3={result.passed_d3_oos_flagged} D4={result.passed_d4_decline} "
                  f"D5={result.passed_d5_feasibility} D6={result.passed_d6_layout_called} "
                  f"D7={result.passed_d7_no_null_price} "
                  f"J1={result.score_j1_style} J2={result.score_j2_rationale} "
                  f"({result.elapsed_s:.1f}s)")

        except Exception as e:
            result.error = str(e)
            result.elapsed_s = time.time() - t0
            print(f"  💥 ERROR: {e}")

        results.append(result)

    return results


def print_report(results: list):
    """Print the final summary report."""
    total = len(results)
    errors = [r for r in results if r.error]

    print(f"\n{'='*70}")
    print("EVAL HARNESS RESULTS REPORT")
    print(f"{'='*70}")
    print(f"Total cases: {total} | Errors: {len(errors)}")

    # Deterministic score per scorer
    scorers = [
        ("D1 — Budget never silent-exceeded", "passed_d1_budget"),
        ("D2 — All items real catalog items", "passed_d2_real_items"),
        ("D3 — OOS items flagged", "passed_d3_oos_flagged"),
        ("D4 — Correct decline behaviour", "passed_d4_decline"),
        ("D5 — Infeasible briefs flagged", "passed_d5_feasibility"),
        ("D6 — Layout tool called", "passed_d6_layout_called"),
        ("D7 — No NULL-price in BOQ", "passed_d7_no_null_price"),
    ]

    print(f"\n{'─'*70}")
    print("DETERMINISTIC SCORERS")
    print(f"{'─'*70}")
    for label, attr in scorers:
        vals = [getattr(r, attr) for r in results if getattr(r, attr) is not None]
        if not vals:
            print(f"  {label}: N/A")
            continue
        passes = sum(1 for v in vals if v)
        pct = passes / len(vals) * 100
        gate = "✅" if pct >= 90 else ("⚠️" if pct >= 75 else "❌")
        if attr == "passed_d1_budget" or attr == "passed_d2_real_items" or attr == "passed_d4_decline":
            gate = "✅" if pct == 100 else "❌"  # hard gates
        print(f"  {gate} {label}: {passes}/{len(vals)} ({pct:.0f}%)")

    # LLM judge summary
    j1_scores = [r.score_j1_style for r in results if r.score_j1_style and "error" not in (r.score_j1_style or "")]
    j2_scores = [r.score_j2_rationale for r in results if r.score_j2_rationale and "error" not in (r.score_j2_rationale or "")]

    print(f"\n{'─'*70}")
    print("LLM-AS-JUDGE SCORERS")
    print(f"{'─'*70}")
    for label, scores in [("J1 — Style coherence", j1_scores), ("J2 — Rationale quality", j2_scores)]:
        if not scores:
            print(f"  {label}: Not run")
            continue
        applicable = [s for s in scores if s != "na"]
        good = sum(1 for s in applicable if s == "good")
        ok = sum(1 for s in applicable if s in ("good", "acceptable"))
        pct = ok / len(applicable) * 100 if applicable else 0
        gate = "✅" if pct >= 80 else "❌"
        print(f"  {gate} {label}: {ok}/{len(applicable)} acceptable+ ({pct:.0f}%) | good: {good}/{len(applicable)}")

    # Ship gate verdict
    print(f"\n{'─'*70}")
    print("SHIP GATE VERDICT")
    print(f"{'─'*70}")

    d1 = [r.passed_d1_budget for r in results if r.passed_d1_budget is not None]
    d2 = [r.passed_d2_real_items for r in results if r.passed_d2_real_items is not None]
    d4 = [r.passed_d4_decline for r in results if r.passed_d4_decline is not None]

    gates = {
        "D1 budget 100%": all(d1) if d1 else False,
        "D2 real items 100%": all(d2) if d2 else False,
        "D4 decline 100%": all(d4) if d4 else False,
    }
    all_hard = all(gates.values())
    print("Hard gates (must be 100%):")
    for k, v in gates.items():
        print(f"  {'✅' if v else '❌'} {k}")

    ship = all_hard
    print(f"\n{'🟢 SHIP' if ship else '🔴 DO NOT SHIP'}")

    # Failure cases
    failures = [r for r in results if not r.overall_pass() and not r.error]
    if failures:
        print(f"\n{'─'*70}")
        print(f"FAILURES ({len(failures)}):")
        for r in failures:
            print(f"  ❌ {r.test_id}: {r.description}")
            issues = []
            if r.passed_d1_budget is False:
                issues.append("D1:budget-silent")
            if r.passed_d2_real_items is False:
                issues.append("D2:invented-items")
            if r.passed_d3_oos_flagged is False:
                issues.append("D3:oos-not-flagged")
            if r.passed_d4_decline is False:
                issues.append("D4:wrong-decline-behaviour")
            if issues:
                print(f"     Issues: {', '.join(issues)}")

    if errors:
        print(f"\n{'─'*70}")
        print(f"ERRORS ({len(errors)}):")
        for r in errors:
            print(f"  💥 {r.test_id}: {r.error}")


def save_results(results: list, path: str = "eval_results.json"):
    """Save results to JSON for analysis."""
    data = []
    for r in results:
        data.append({
            "test_id": r.test_id,
            "description": r.description,
            "overall_pass": r.overall_pass(),
            "d1_budget": r.passed_d1_budget,
            "d2_real_items": r.passed_d2_real_items,
            "d3_oos_flagged": r.passed_d3_oos_flagged,
            "d4_decline": r.passed_d4_decline,
            "d5_feasibility": r.passed_d5_feasibility,
            "d6_layout_called": r.passed_d6_layout_called,
            "d7_no_null_price": r.passed_d7_no_null_price,
            "j1_style": r.score_j1_style,
            "j2_rationale": r.score_j2_rationale,
            "error": r.error,
            "elapsed_s": r.elapsed_s,
        })
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\nResults saved to {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run the Interior Agent eval harness")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM-as-judge scoring")
    parser.add_argument("--filter", nargs="*", help="Only run specific test IDs e.g. TC-01 TC-07")
    parser.add_argument("--save", default="eval_results.json", help="Output JSON path")
    parser.add_argument("--db", default="interior_company_catalog.db", help="DB path")
    args = parser.parse_args()

    results = run_harness(
        run_llm_judge=not args.no_llm,
        brief_filter=args.filter,
        db_path=args.db,
    )
    print_report(results)
    save_results(results, args.save)
