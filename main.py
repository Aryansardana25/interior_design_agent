"""
main.py
-------
FastAPI backend for the Interior Company AI Design Agent.
Serves the HTML frontend + exposes /api/design and /api/briefs endpoints.

Run locally:
    pip install fastapi uvicorn
    uvicorn main:app --reload

Deploy on Render / Railway:
    Start command: uvicorn main:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(__file__))

from agent import InteriorDesignAgent
from database import get_all_room_briefs, get_all_products

DB_PATH = os.path.join(os.path.dirname(__file__), "interior_company_catalog.db")
HTML_PATH = os.path.join(os.path.dirname(__file__), "interior_design_agent.html")

app = FastAPI(title="Interior Company AI Design Agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

agent = InteriorDesignAgent(db_path=DB_PATH)


# ── Request / Response models ────────────────────────────────────────────────

class FreeTextRequest(BaseModel):
    room_type: str
    style: str
    budget_inr: int
    length_cm: int
    width_cm: int
    must_haves_text: str
    constraints: Optional[str] = ""


class DesignRequest(BaseModel):
    brief_id: Optional[str] = None
    free_text: Optional[FreeTextRequest] = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def serialize_plan(plan) -> dict:
    items = []
    for item in plan.selected_items:
        tier = item.style_match.value if hasattr(item.style_match, "value") else str(item.style_match)
        items.append({
            "item_id": item.item_id,
            "name": item.name,
            "category": item.category,
            "price_inr": item.price_inr,
            "style_match": tier,
            "adjacent_style_used": item.adjacent_style_used,
            "in_stock": item.item.in_stock,
            "color_finish": item.item.color_finish,
            "lead_time_days": item.lead_time_days,
            "width_cm": item.item.width_cm,
            "depth_cm": item.item.depth_cm,
            "height_cm": item.item.height_cm,
            "is_oos": item.warnings.is_oos,
            "style_tags": item.item.style_tags,
            "room_types": item.item.room_types,
        })

    layout = plan.layout_result
    return {
        "declined": plan.declined,
        "decline_reason": plan.decline_reason if plan.declined else None,
        "brief_id": plan.brief_id,
        "room_type": plan.room_type,
        "style": plan.style,
        "budget_inr": plan.budget_inr,
        "items": items,
        "total_spent": plan.budget_state.total_spent,
        "remaining": plan.budget_state.remaining,
        "utilisation_pct": plan.budget_state.utilisation_pct,
        "is_over_budget": plan.budget_state.is_over_budget,
        "layout": {
            "fits": layout.fits,
            "room_area_sqm": round(layout.room_area_sqcm / 10000, 2),
            "usable_sqm": round(layout.usable_area_sqcm / 10000, 2),
            "footprint_sqm": round(layout.total_footprint_sqcm / 10000, 2),
            "utilisation_pct": round(layout.footprint_utilisation_pct, 1),
        },
        "rationale": plan.rationale,
        "trade_offs": plan.trade_offs,
        "feasibility_note": plan.feasibility_note,
        "replan_log": plan.replan_log,
    }


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    """Serve the standalone HTML frontend."""
    if os.path.exists(HTML_PATH):
        with open(HTML_PATH, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Frontend not found</h1>", status_code=404)


@app.get("/api/briefs")
def get_briefs():
    """Return all 14 room briefs from the database."""
    briefs = get_all_room_briefs(DB_PATH)
    return [
        {
            "brief_id": b.brief_id,
            "room_type": b.room_type,
            "style_preference": b.style_preference,
            "budget_inr": b.budget_inr,
            "length_cm": b.length_cm,
            "width_cm": b.width_cm,
            "must_haves": b.must_haves,
            "constraints": b.constraints,
            "customer_note": b.customer_note,
        }
        for b in briefs
    ]


@app.get("/api/catalog")
def get_catalog():
    """Return all 72 catalog items from the database."""
    products = get_all_products(DB_PATH)
    return [
        {
            "item_id": p.item_id,
            "name": p.name,
            "category": p.category,
            "style_tags": p.style_tags,
            "price_inr": p.price_inr,
            "width_cm": p.width_cm,
            "depth_cm": p.depth_cm,
            "height_cm": p.height_cm,
            "color_finish": p.color_finish,
            "in_stock": p.in_stock,
            "lead_time_days": p.lead_time_days,
            "room_types": p.room_types,
        }
        for p in products
    ]


@app.post("/api/design")
def run_design(req: DesignRequest):
    """Run the design agent on a brief (DB or free-text)."""
    try:
        if req.brief_id:
            plan = agent.design(req.brief_id)
        elif req.free_text:
            ft = req.free_text
            plan = agent.design_from_text(
                room_type=ft.room_type,
                style=ft.style,
                budget_inr=ft.budget_inr,
                must_haves_text=ft.must_haves_text,
                room_length_cm=ft.length_cm,
                room_width_cm=ft.width_cm,
                constraints=ft.constraints or "",
            )
        else:
            raise HTTPException(status_code=400, detail="Provide brief_id or free_text")

        return serialize_plan(plan)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health():
    return {"status": "ok", "agent": "Interior Company AI Design Agent v1.0"}
