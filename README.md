# Interior Company — AI Design Agent
**APM Build Challenge · Aryan Sardana**

> A working AI agent that turns a customer room brief into a budget-fit design plan using real catalog products. No invented items. No silent overruns.

---

## Live Demo (Fastest — No Setup)

Open `interior_design_agent.html` directly in any browser. That's it.

- All 72 catalog items from the real SQLite DB are embedded
- All 14 briefs (BR-01 to BR-14) are loaded
- Works completely offline, no server needed

---

## Full Python Agent (Backend)

If you want to run the Python agent or eval harness:

### Requirements
- Python 3.9+
- pip

### Step 1 — Install dependencies

```bash
cd interior_design_agent-main
pip install -r requirements.txt
```

### Step 2 — Run a single brief

```bash
python agent.py BR-01
```

Try any brief: `BR-01` through `BR-14`

```bash
# Edge cases worth trying
python agent.py BR-06   # Budget trap — ₹20K for full living room
python agent.py BR-07   # Declined — wall removal request
python agent.py BR-08   # Declined — Togo/Noguchi/Eames pieces
python agent.py BR-14   # Premium ₹5L living room
```

### Step 3 — Run the Streamlit UI

```bash
streamlit run app.py
```

Opens at `http://localhost:8501`

---

## Eval Harness (27 Test Cases)

### Run without LLM judge (fast, no API key needed)

```bash
python eval_harness.py --no-llm
```

### Run with LLM-as-judge scoring (J1 style coherence + J2 rationale quality)

```bash
export ANTHROPIC_API_KEY=your_key_here
python eval_harness.py
```

### Run specific test cases

```bash
python eval_harness.py --no-llm --filter TC-06 TC-07 TC-08
```

### Output

Results are printed to terminal and saved to `eval_results.json`.

---

## Project Structure

```
interior_design_agent-main/
├── agent.py               # Core planning engine (orchestrates tools)
├── catalog_tool.py        # 4-tier style cascade search
├── budget_tool.py         # Budget tracking + feasibility check
├── layout_tool.py         # Room footprint heuristic
├── database.py            # SQLite helpers
├── guardrails.py          # Out-of-scope pattern detection
├── eval_harness.py        # 27-case eval suite
├── app.py                 # Streamlit frontend
└── interior_company_catalog.db   # 72 items, 14 briefs

interior_design_agent.html     # Standalone HTML frontend (open directly)
decision_log.docx              # Decision log (scoping, AI tool use, results)
README.md                      # This file
```

---

## How the Agent Works

```
Customer brief
      ↓
[GUARDRAIL CHECK] — structural, electrical, plumbing, designer brands → DECLINE if flagged
      ↓
[FEASIBILITY CHECK] — minimum must-have cost vs budget → DECLINE if infeasible
      ↓
[CATALOG SEARCH] — per must-have category, 4-tier cascade:
   Tier 1: Exact style match, in-stock
   Tier 2: Adjacent style (e.g. Japandi → Scandinavian)
   Tier 3: Any style, in-stock
   Tier 4: OOS fallback
      ↓
[LAYOUT CHECK] — footprint vs usable area (60% of room) → flag if tight
      ↓
[BUDGET CHECK] — running total vs budget → flag if over
      ↓
Design Plan + BOQ
```

---

## Eval Harness — Ship Gates

| Gate | Threshold | Status |
|------|-----------|--------|
| D1 Budget never silently exceeded | 100% | ✅ 27/27 |
| D2 All items real catalog items | 100% | ✅ 27/27 |
| D4 Correct decline behaviour | 100% | ✅ 27/27 |
| D3 OOS items flagged | ≥90% | ✅ 27/27 |
| D5 Infeasible briefs flagged | ≥90% | ✅ 27/27 |
| D6 Layout tool called | ≥90% | ✅ 27/27 |
| D7 No null-price in BOQ | ≥90% | ✅ 27/27 |

**Verdict: 🟢 SHIP** — all 7 deterministic gates met at 100% across 27 test cases.

---

## Tools Used

- **Claude (Anthropic)** — backend agent code, eval harness, HTML frontend
- **Python / SQLite** — data layer, tool implementations
- **Streamlit** — Python UI
- **HTML/CSS/JS** — standalone frontend with embedded DB data

Overrides made: agent.py orchestration is deterministic (not LLM-planned); guardrails.py is dead code (logic lives in agent.py); HTML frontend was corrected to use real DB data instead of hardcoded JS catalog.
