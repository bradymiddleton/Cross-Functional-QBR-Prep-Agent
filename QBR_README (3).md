# QBR Prep Agent

An autonomous AI agent that prepares a complete Quarterly Business Review package — given only a business unit and a quarter. No templates, no manual pulls, no pre-written summaries. The agent decides what to analyze, cross-references six databases, and produces a CFO-ready draft.

**Part of the Middleton Finance Suite** — a cross-functional finance analytics portfolio covering Sales Finance and Supply Chain.

---

## What Makes This a True Agent

Most "AI-enabled" finance tools answer questions you ask. This agent receives a goal and works autonomously:

| What you give it | What it does |
|---|---|
| Business unit + quarter | Calls 8 tools in sequence without being told how |
| Nothing else | Cross-references sales AND supply chain data on its own |
| | Identifies which variances matter by dollar impact |
| | Classifies root causes across pricing, COGS, volume, and demand |
| | Flags forward-looking risks with specific recommended actions |
| | Writes the full QBR document in CFO-ready language |

The agent reasons through a finance workflow the same way a senior analyst would — in about 30 seconds.

---

## The Eight Tools

The agent calls these in sequence, deciding the order and interpreting results at each step:

| # | Tool | What It Does |
|---|------|-------------|
| 1 | `get_actuals` | Revenue, GM%, discount rate by quarter and category from pricing_rebates.db |
| 2 | `get_plan` | GM% targets, plan discount rate, and quarterly quota from finance assumptions |
| 3 | `identify_variances` | Every line item off plan, ranked by dollar impact on gross profit |
| 4 | `classify_variance` | Root cause classification: pricing / COGS / volume / demand |
| 5 | `get_pipeline` | Forward pipeline health — value by stage, approval and escalation rates |
| 6 | `check_supply_chain` | Cross-references SC data: COGS waterfall, supplier variance, DIO, forecast MAPE |
| 7 | `flag_risks` | Forward-looking risks with severity and specific recommended actions |
| 8 | `build_qbr_draft` | Assembles all outputs into a structured QBR scaffold — called last |

---

## Two Ways to Run It

### HTML Version (recommended for demos)

Open `qbr_prep_agent.html` in any browser — fully standalone, no installation needed.

1. Paste your Anthropic API key into the topbar field
2. Select a business unit and quarter
3. Click **Run QBR Agent**
4. Watch each tool fire in real time in the agent feed
5. QBR draft appears at the bottom — copy with one click

**Cost:** ~$0.01 per full QBR run (claude-haiku-4-5, 8 tool calls + narrative generation)

### Python CLI Version (for integration and file output)

```bash
# Install dependencies
pip install anthropic

# Set your API key
export ANTHROPIC_API_KEY=sk-ant-...

# Run with defaults (GPUs, Q4 2024)
python qbr_prep_agent.py

# Specify business unit and quarter
python qbr_prep_agent.py --bu CPUs --quarter "Q3 2024"
python qbr_prep_agent.py --bu "Adaptive Computing" --quarter "Q4 2024"
python qbr_prep_agent.py --bu All --quarter "Q4 2024"

# Save output to file
python qbr_prep_agent.py --bu GPUs --quarter "Q4 2024" --save
```

The Python version shows each tool call in the terminal as it runs, then prints the full formatted QBR. Use `--save` to write it to a `.txt` file.

---

## Getting an API Key

1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Sign up or log in
3. Go to **API Keys** → **Create Key**
4. Copy the key — it starts with `sk-ant-api03-...`

**Cost:** Claude Haiku is approximately $0.001 per message. A full QBR run (8 tool calls + narrative) costs around $0.01 — one cent.

---

## Prerequisites

Run the data generation scripts from the Middleton Finance Suite first:

```bash
# Sales Finance databases
python 01_generate_and_load_data.py

# Supply Chain databases
python 05_generate_sc_data.py
```

The agent reads from six SQLite databases:

| Database | Used For |
|----------|----------|
| `databases/pricing_rebates.db` | Revenue actuals, discount rates, plan rates |
| `databases/deal_pipeline.db` | Pipeline value, approval rates, deal stages |
| `databases/channel_sales.db` | Rep quota attainment, regional performance |
| `sc_databases/cogs_margin.db` | COGS waterfall, standard vs. actual cost variance |
| `sc_databases/inventory.db` | DIO vs. target, holding cost, slow movers |
| `sc_databases/demand_planning.db` | Forecast MAPE, bias, actuals vs. plan |

The HTML version has all data inlined — it runs without the databases.

---

## QBR Output Structure

The agent produces a six-section document:

```
1. Executive Summary
   One-paragraph portfolio health summary with key headline numbers

2. Financial Performance
   Revenue, GM%, and discount actuals vs. plan — variances ranked by dollar impact

3. Variance Root Causes
   Classification of each material gap: pricing / COGS / volume / demand
   Cross-referenced against supply chain data

4. Supply Chain Analysis
   COGS waterfall, supplier cost overruns, DIO vs. target, forecast accuracy

5. Pipeline Outlook
   Forward pipeline by stage, approval vs. escalation rate, risk to Q1 close

6. Risks & Recommended Actions
   Prioritized by severity (High / Medium) with specific next steps for each
```

---

## Terminal Output (Python version)

```
══════════════════════════════════════════════════════════════════════════
  QBR Prep Agent
  Autonomous Quarterly Business Review Analyst
  Middleton Finance Suite — Sales Finance & Supply Chain
══════════════════════════════════════════════════════════════════════════

  Model:    claude-haiku-4-5-20251001  (~$0.001/msg · full QBR ~$0.01)
  Key:      export ANTHROPIC_API_KEY=sk-ant-...
  Get key:  console.anthropic.com → API Keys → Create Key
  HTML ver: open qbr_prep_agent.html in any browser

  ┌─ Agent Planning ───────────────────────────────────────────────────
  Goal: Prepare GPUs QBR — Q4 2024

  [Tool 1] get_actuals('GPUs', 'Q4 2024')
           → {"actuals": [...], "total_rev_mm": 38.41}

  [Tool 2] get_plan('GPUs')
           → {"gm_target_pct": 35.0, "plan_discount_pct": 8.97}

  [Tool 3] identify_variances('GPUs', 'Q4 2024')
           → {"material_count": 1, "total_gm_impact_mm": -1.6}

  [Tool 4] classify_variance('GPUs', 'Q4 2024')
           → {"driver_count": 1, "primary_drivers": [{"driver": "COGS..."}]}

  [Tool 5] get_pipeline('GPUs')
           → {"total_pipeline_mm": 488.39, "approval_rate_pct": 36.1}

  [Tool 6] check_supply_chain('GPUs', 'Q4 2024')
           → {"sc_risk_count": 4, "sc_findings": [...]}

  [Tool 7] flag_risks('GPUs')
           → {"high_count": 2, "medium_count": 3}

  [Agent] QBR scaffold assembled — writing final document...

  [Tool 8] build_qbr_draft('GPUs', 'Q4 2024', ...)
           → {"sections": 6, "status": "Draft"}
```

---

## Available Business Units & Quarters

**Business Units:** GPUs · CPUs · Embedded · Semi-Custom · Adaptive Computing · All

**Quarters:** Q1 2024 · Q2 2024 · Q3 2024 · Q4 2024

---

## Skills Demonstrated

| Area | Details |
|------|---------|
| **Agentic AI** | True agent loop — goal-directed, multi-tool, autonomous reasoning across 6 databases |
| **Cross-functional Finance** | Integrates sales finance (pricing, deals, channel) with supply chain (COGS, DIO, forecast) in one workflow |
| **Tool Design** | 8 purpose-built functions with clean schemas registered to the Anthropic tool-use API |
| **Python** | SQLite queries, argument parsing, ANSI terminal formatting, file output |
| **Finance Domain** | QBR structure, variance analysis, root cause classification, standard costing, pipeline metrics |
| **Prompt Engineering** | System prompt written to produce analyst-grade CFO-ready output, not generic summaries |

---

## Contact

**Brady Middleton**
[LinkedIn](https://linkedin.com/in/bradymiddleton) · [GitHub](https://github.com/bradymiddleton)
