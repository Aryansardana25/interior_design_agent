"""
app.py
------
Streamlit UI for the Interior Company AI Design Agent.

Run with:  streamlit run app.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st
from database import get_all_room_briefs, get_room_brief
from agent import InteriorDesignAgent

DB_PATH = os.path.join(os.path.dirname(__file__), "interior_company_catalog.db")

st.set_page_config(
    page_title="Interior Company — AI Design Agent",
    page_icon="🛋️",
    layout="wide",
)

# ── Sidebar ────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image("https://via.placeholder.com/200x60/2D3142/FFFFFF?text=Interior+Co", width=200)
    st.title("Design Agent")
    st.markdown("---")

    mode = st.radio("Brief source", ["Select from database", "Enter free text"], index=0)

    if mode == "Select from database":
        briefs = get_all_room_briefs(DB_PATH)
        brief_options = {
            f"{b.brief_id} — {b.room_type} | {b.style_preference} | ₹{b.budget_inr:,}": b.brief_id
            for b in briefs
        }
        selected_label = st.selectbox("Choose a brief", list(brief_options.keys()))
        selected_brief_id = brief_options[selected_label]

        # Show brief details
        brief = get_room_brief(selected_brief_id, DB_PATH)
        if brief:
            st.markdown("**Brief details:**")
            st.markdown(f"- Room: {brief.room_type}")
            if brief.length_cm and brief.width_cm:
                st.markdown(f"- Size: {brief.length_cm/100:.1f}m × {brief.width_cm/100:.1f}m")
            st.markdown(f"- Budget: ₹{brief.budget_inr:,}")
            st.markdown(f"- Style: {brief.style_preference}")
            st.markdown(f"- Must-haves: {', '.join(brief.must_haves)}")
            if brief.constraints:
                st.markdown(f"- Constraints: {brief.constraints}")

        run_btn = st.button("🏠 Generate Design Plan", type="primary", use_container_width=True)

    else:  # free text
        room_type = st.selectbox("Room type", ["Living Room", "Bedroom", "Dining", "Study", "Kids"])
        style = st.selectbox("Style", [
            "Scandinavian", "Minimalist", "Contemporary", "Mid-Century",
            "Industrial", "Bohemian", "Coastal", "Traditional", "Japandi",
        ])
        budget = st.number_input("Budget (₹)", min_value=10000, max_value=1000000,
                                  value=200000, step=10000, format="%d")
        must_haves = st.text_area("Must-haves (comma-separated)",
                                   placeholder="sofa, coffee table, rug, floor lamp")
        col1, col2 = st.columns(2)
        with col1:
            room_length = st.number_input("Room length (cm)", min_value=0, max_value=2000, value=480)
        with col2:
            room_width = st.number_input("Room width (cm)", min_value=0, max_value=2000, value=360)
        constraints = st.text_area("Constraints / context", placeholder="e.g. rented flat, kids, south-facing...")

        run_btn = st.button("🏠 Generate Design Plan", type="primary", use_container_width=True)

    st.markdown("---")
    st.caption("Interior Company × Blocks — AI Design Agent\nProducts are sourced from our verified catalog only.")

# ── Main content ───────────────────────────────────────────────────────────
st.title("🛋️ Interior Design Agent")
st.markdown("*Real products. Real budgets. No invented items.*")

if run_btn:
    agent = InteriorDesignAgent(db_path=DB_PATH)

    with st.spinner("Searching catalog, checking budget, verifying layout..."):
        if mode == "Select from database":
            plan = agent.design(selected_brief_id)
        else:
            plan = agent.design_from_text(
                room_type=room_type,
                budget_inr=int(budget),
                style=style,
                must_haves_text=must_haves,
                room_length_cm=int(room_length) if room_length else None,
                room_width_cm=int(room_width) if room_width else None,
                constraints=constraints,
            )

    # ── Declined ──────────────────────────────────────────────────────────
    if plan.declined:
        st.error("⛔ Request Declined")
        st.markdown(plan.decline_reason)
        st.stop()

    # ── Header metrics ────────────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Budget", f"₹{plan.budget_inr:,}")
    with col2:
        st.metric("Total Spend", f"₹{plan.budget_state.total_spent:,}",
                  delta=f"₹{plan.budget_state.remaining:,} remaining",
                  delta_color="normal" if not plan.budget_state.is_over_budget else "inverse")
    with col3:
        st.metric("Items Selected", len(plan.selected_items))
    with col4:
        layout_ok = plan.layout_result.fits
        st.metric("Layout", "✅ Fits" if layout_ok else "⚠️ Check issues")

    # ── Feasibility note ──────────────────────────────────────────────────
    if plan.feasibility_note:
        st.warning(plan.feasibility_note)

    # ── Tabs ──────────────────────────────────────────────────────────────
    tab1, tab2, tab3, tab4 = st.tabs(["📋 Design Plan", "💰 BOQ", "📐 Layout Check", "🔍 Agent Log"])

    with tab1:
        st.subheader("Selected Items & Rationale")
        if plan.selected_items:
            for item in plan.selected_items:
                with st.expander(f"{item.name} — ₹{item.price_inr:,} [{item.item_id}]"):
                    col_a, col_b = st.columns(2)
                    with col_a:
                        st.markdown(f"**Category:** {item.category}")
                        st.markdown(f"**Style:** {', '.join(item.item.style_tags)}")
                        st.markdown(f"**Finish:** {item.item.color_finish or 'N/A'}")
                        st.markdown(f"**Dimensions:** {item.item.width_cm}W × {item.item.depth_cm}D × {item.item.height_cm}H cm")
                        st.markdown(f"**Lead time:** {item.item.lead_time_days} days")
                        if item.item.in_stock:
                            st.success("In stock")
                        else:
                            st.error("Out of stock")
                    with col_b:
                        match_label = {
                            "exact": "✅ Exact style match",
                            "adjacent": f"↔️ Adjacent style ({item.adjacent_style_used})",
                            "none": "⚪ Best available",
                        }.get(item.style_match.value, item.style_match.value)
                        st.markdown(f"**Style match:** {match_label}")
                        if item.warnings.has_long_lead:
                            st.warning(f"Long lead time: {item.item.lead_time_days} days")
        else:
            st.info("No items could be selected — see Trade-offs tab.")

        st.subheader("Trade-offs & Caveats")
        st.markdown(plan.trade_offs)

    with tab2:
        st.subheader("Bill of Quantities")
        if plan.budget_state.lines:
            import pandas as pd
            rows = []
            for line in plan.budget_state.lines:
                rows.append({
                    "Item ID": line.item_id,
                    "Name": line.name,
                    "Category": line.category,
                    "Price (₹)": f"₹{line.price_inr:,}",
                })
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)

            st.markdown("---")
            col_l, col_r = st.columns([3, 1])
            with col_r:
                total_color = "normal"
                st.metric("Total", f"₹{plan.budget_state.total_spent:,}")
                st.metric("Budget", f"₹{plan.budget_inr:,}")
                remaining = plan.budget_state.remaining
                st.metric(
                    "Remaining" if remaining >= 0 else "Over Budget",
                    f"₹{abs(remaining):,}",
                    delta=None,
                )
            if plan.budget_state.is_over_budget:
                st.error("⚠️ Over budget — review trade-offs above.")
            else:
                st.success(f"✅ Within budget ({plan.budget_state.utilisation_pct:.1f}% utilised)")
        else:
            st.info("No items selected.")

    with tab3:
        st.subheader("Layout Check")
        lr = plan.layout_result
        if lr.room_area_sqcm > 0:
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Room area", f"{lr.room_area_sqcm/10000:.1f} m²")
            with col2:
                st.metric("Usable area (60%)", f"{lr.usable_area_sqcm/10000:.1f} m²")
            with col3:
                st.metric("Furniture footprint", f"{lr.total_footprint_sqcm/10000:.1f} m² ({lr.footprint_utilisation_pct:.0f}%)")
            st.progress(min(lr.footprint_utilisation_pct / 100, 1.0))

        if lr.fits:
            st.success("✅ All selected pieces fit the room with adequate circulation space.")
        else:
            st.error("❌ Layout issues detected — review below.")

        if lr.issues:
            for issue in lr.issues:
                if issue.severity == "error":
                    st.error(f"{'[' + issue.item_id + '] ' if issue.item_id else ''}{issue.message}")
                else:
                    st.warning(f"{'[' + issue.item_id + '] ' if issue.item_id else ''}{issue.message}")

    with tab4:
        st.subheader("Agent Reasoning Log")
        st.caption("Tool calls and decisions made during planning")
        for i, entry in enumerate(plan.replan_log, 1):
            icon = "🔍" if "SEARCH" in entry else ("💰" if "BUDGET" in entry or "COMMITTED" in entry else ("📐" if "LAYOUT" in entry else "→"))
            st.markdown(f"`{i:02d}` {icon} {entry}")

else:
    # Landing state
    st.markdown("""
    ### How it works

    This agent turns a room brief into an **actionable design plan** with real products from our catalog.

    **What the agent does:**
    1. 🔍 **Searches** the catalog using a 4-tier style cascade (exact → adjacent → any → OOS)
    2. 💰 **Tracks budget** in real time — never silently overspends
    3. 📐 **Checks layout** — every item must fit the room with circulation space
    4. 📋 **Explains** each choice with rationale and trade-offs
    5. 🚫 **Declines** out-of-scope requests (structural, electrical, plumbing)

    **Select a brief from the sidebar to get started →**
    """)

    st.info("💡 Try BR-06 (budget trap), BR-07 (wall removal), or BR-08 (designer pieces) to see the guardrails in action.")
