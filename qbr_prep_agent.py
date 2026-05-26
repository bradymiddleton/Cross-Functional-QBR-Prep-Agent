"""
QBR Prep Agent — Quarterly Business Review Autonomous Analyst
Middleton Finance Suite · Sales Finance & Supply Chain

A true agentic workflow: given a goal ("prepare the Q4 2024 QBR for GPUs"),
the agent decides which tools to call, in what order, interprets the results,
cross-references supply chain data, and produces a complete QBR draft —
without being told how to do any of it.

Eight tools, full reasoning loop, structured output:
  1.  get_actuals            — revenue, GM%, discount by quarter and category
  2.  get_plan               — plan rates and GM targets
  3.  identify_variances     — finds every line item off plan, ranked by impact
  4.  classify_variance      — root causes: pricing / volume / mix / COGS / opex
  5.  get_pipeline           — forward-looking pipeline health by category/region
  6.  check_supply_chain     — cross-references SC data to explain cost variances
  7.  flag_risks             — forward-looking risks from pipeline + inventory + COGS
  8.  build_qbr_draft        — assembles everything into a structured QBR package

Usage:
    pip install anthropic
    export ANTHROPIC_API_KEY=sk-ant-...
    python qbr_prep_agent.py

    # Specify a business unit:
    python qbr_prep_agent.py --bu "GPUs" --quarter "Q4 2024"

Prerequisites:
    Run 01_generate_and_load_data.py and 05_generate_sc_data.py first.
"""

import os
import sys
import json
import sqlite3
import argparse
import textwrap
from datetime import datetime
import anthropic

# ── Config ───────────────────────────────────────────────────

MODEL      = "claude-haiku-4-5-20251001"
MAX_TOKENS = 4096   # QBR drafts need room

DB_PRICING  = "databases/pricing_rebates.db"
DB_DEALS    = "databases/deal_pipeline.db"
DB_CHANNEL  = "databases/channel_sales.db"
DB_COGS     = "sc_databases/cogs_margin.db"
DB_INV      = "sc_databases/inventory.db"
DB_DEMAND   = "sc_databases/demand_planning.db"

GM_TARGETS  = {
    "GPUs": 35.0, "CPUs": 38.0, "Embedded": 42.0,
    "Semi-Custom": 28.0, "Adaptive Computing": 40.0,
}
PLAN_DISC   = 0.14   # 14% blended plan discount rate
QUARTERS    = ["Q1 2024", "Q2 2024", "Q3 2024", "Q4 2024"]

# ── Helpers ───────────────────────────────────────────────────

def q(db_path, sql, params=()):
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows

def color(text, code):
    codes = {"red":"31","green":"32","yellow":"33","blue":"34",
             "cyan":"36","magenta":"35","bold":"1","dim":"2","reset":"0"}
    return f"\033[{codes.get(code,'0')}m{text}\033[0m"

def divider(char="─", w=72): return char * w

def wrap(text, width=70, indent="    "):
    lines = []
    for line in text.split("\n"):
        if line.strip():
            lines.append(textwrap.fill(line, width=width,
                initial_indent=indent, subsequent_indent=indent))
        else:
            lines.append("")
    return "\n".join(lines)

def section(title):
    print()
    print(color(f"  ┌─ {title} ", "blue") + color("─" * (66-len(title)), "blue"))


# ════════════════════════════════════════════════════════════
# TOOL FUNCTIONS
# ════════════════════════════════════════════════════════════

def get_actuals(business_unit: str, quarter: str = None) -> dict:
    """
    Pull actual revenue, GM%, discount rate, and deal count for a
    business unit. Returns all quarters if quarter is None.

    Args:
        business_unit: Product category (e.g. 'GPUs', 'CPUs', or 'All')
        quarter: Specific quarter e.g. 'Q4 2024', or None for full year
    """
    where = []
    params = []
    if business_unit.lower() != "all":
        where.append("product_category = ?")
        params.append(business_unit)
    if quarter:
        where.append("quarter = ?")
        params.append(quarter)

    sql = f"""
        SELECT quarter, product_category,
               ROUND(SUM(net_revenue)/1e6,2)        AS net_rev_mm,
               ROUND(AVG(gross_margin_pct)*100,2)   AS gm_pct,
               ROUND(AVG(discount_rate)*100,2)       AS disc_pct,
               COUNT(*)                              AS n_transactions,
               ROUND(SUM(net_revenue)/COUNT(*),0)   AS avg_deal_size
        FROM transactions
        {'WHERE ' + ' AND '.join(where) if where else ''}
        GROUP BY quarter, product_category
        ORDER BY quarter, net_rev_mm DESC
    """
    rows = q(DB_PRICING, sql, params)

    # Attach YoY context (compare Q4 to Q3)
    total_rev = sum(r['net_rev_mm'] for r in rows)
    return {
        "business_unit": business_unit,
        "quarter_filter": quarter or "All quarters",
        "actuals":        rows,
        "total_rev_mm":   round(total_rev, 2),
        "n_rows":         len(rows),
    }


def get_plan(business_unit: str) -> dict:
    """
    Return plan/target rates for a business unit: GM% target,
    plan discount rate, and planned revenue (estimated from quotas).

    Args:
        business_unit: Product category or 'All'
    """
    gm_target = GM_TARGETS.get(business_unit, 35.0) if business_unit != "All" else \
                round(sum(GM_TARGETS.values()) / len(GM_TARGETS), 1)

    plan_rows = q(DB_PRICING, """
        SELECT product_category,
               ROUND(AVG(plan_discount_rate)*100,2) AS plan_disc_pct
        FROM transactions
        GROUP BY product_category
        ORDER BY product_category
    """)
    plan_disc = next((r['plan_disc_pct'] for r in plan_rows
                      if r['product_category'] == business_unit), PLAN_DISC * 100)

    quota_rows = q(DB_CHANNEL, """
        SELECT ROUND(SUM(quarterly_quota)/1e6,2) AS quarterly_quota_mm
        FROM quota_plan WHERE quarter='Q4 2024'
    """)
    quarterly_quota = quota_rows[0]['quarterly_quota_mm'] if quota_rows else 0

    return {
        "business_unit":       business_unit,
        "gm_target_pct":       gm_target,
        "plan_discount_pct":   plan_disc,
        "quarterly_quota_mm":  quarterly_quota,
        "scoring_thresholds":  {
            "auto_approve": 6.5,
            "escalate":     5.0,
        }
    }


def identify_variances(business_unit: str, quarter: str) -> dict:
    """
    Find every material variance vs. plan for a business unit in a given
    quarter — revenue, GM%, discount, pipeline approval rate.
    Returns variances ranked by dollar impact.

    Args:
        business_unit: Product category
        quarter: e.g. 'Q4 2024'
    """
    actuals = get_actuals(business_unit, quarter)
    plan    = get_plan(business_unit)

    variances = []
    for row in actuals['actuals']:
        gm_var  = round(row['gm_pct'] - plan['gm_target_pct'], 2)
        disc_var= round(row['disc_pct'] - plan['plan_discount_pct'], 2)
        variances.append({
            "category":        row['product_category'],
            "quarter":         row['quarter'],
            "net_rev_mm":      row['net_rev_mm'],
            "actual_gm_pct":   row['gm_pct'],
            "target_gm_pct":   plan['gm_target_pct'],
            "gm_variance_pp":  gm_var,
            "actual_disc_pct": row['disc_pct'],
            "plan_disc_pct":   plan['plan_discount_pct'],
            "disc_variance_pp":disc_var,
            "gm_dollar_impact_mm": round(row['net_rev_mm'] * gm_var / 100, 2),
            "material":        abs(gm_var) >= 2.0 or abs(disc_var) >= 1.0,
        })

    variances.sort(key=lambda x: abs(x['gm_dollar_impact_mm']), reverse=True)

    return {
        "business_unit": business_unit,
        "quarter":       quarter,
        "variances":     variances,
        "material_count": sum(1 for v in variances if v['material']),
        "total_gm_impact_mm": round(sum(v['gm_dollar_impact_mm'] for v in variances), 2),
    }


def classify_variance(business_unit: str, quarter: str) -> dict:
    """
    Classify variance root causes for a business unit as:
    pricing (discount), volume (deal count trend), mix (category shift),
    COGS (cost overruns vs standard), or demand (forecast miss).

    Args:
        business_unit: Product category
        quarter: e.g. 'Q4 2024'
    """
    # Discount vs plan
    disc_rows = q(DB_PRICING, """
        SELECT ROUND(AVG(discount_rate)*100,2)       AS actual_disc,
               ROUND(AVG(plan_discount_rate)*100,2)  AS plan_disc,
               COUNT(*)                              AS n_deals
        FROM transactions
        WHERE product_category=? AND quarter=?
    """, (business_unit, quarter))

    # Deal count trend (volume)
    vol_rows = q(DB_PRICING, """
        SELECT quarter, COUNT(*) n_deals,
               ROUND(SUM(net_revenue)/1e6,2) rev_mm
        FROM transactions WHERE product_category=?
        GROUP BY quarter ORDER BY quarter
    """, (business_unit,))

    # COGS variance from SC
    cogs_rows = q(DB_COGS, """
        SELECT ROUND(AVG(total_cost_variance),2)     AS avg_unit_var,
               ROUND(AVG(total_cost_variance)/AVG(standard_unit_cost)*100,2) AS var_pct,
               COUNT(CASE WHEN favorable=1 THEN 1 END) fav,
               COUNT(CASE WHEN favorable=0 THEN 1 END) unfav
        FROM standard_vs_actual
        WHERE category=? AND quarter=?
    """, (business_unit, quarter))

    # Forecast bias (demand side)
    q_month = {'Q1 2024':'2024-0%','Q2 2024':'2024-0%','Q3 2024':'2024-0%','Q4 2024':'2024-1%'}.get(quarter,'2024%')
    fc_rows = q(DB_DEMAND, """
        SELECT ROUND(AVG(mape)*100,2) mape,
               ROUND(AVG(bias_pct)*100,2) bias
        FROM forecast_accuracy WHERE category=? AND month LIKE '2024%'
    """, (business_unit,))

    disc      = disc_rows[0] if disc_rows else {}
    cogs      = cogs_rows[0] if cogs_rows else {}
    fc        = fc_rows[0] if fc_rows else {}

    # Build classification
    drivers = []
    if disc and abs(disc.get('actual_disc',0) - disc.get('plan_disc',0)) >= 1.0:
        delta = round(disc['actual_disc'] - disc['plan_disc'], 1)
        drivers.append({
            "driver": "Pricing / Discount",
            "direction": "Favorable" if delta < 0 else "Unfavorable",
            "detail": f"Actual discount {disc['actual_disc']}% vs. plan {disc['plan_disc']}% ({'+' if delta>0 else ''}{delta}pp)",
            "severity": "High" if abs(delta) >= 3 else "Medium",
        })

    if len(vol_rows) >= 2:
        prev = vol_rows[-2] if len(vol_rows) >= 2 else vol_rows[0]
        curr = vol_rows[-1]
        vol_delta = curr['n_deals'] - prev['n_deals']
        if abs(vol_delta) >= 20:
            drivers.append({
                "driver": "Volume",
                "direction": "Favorable" if vol_delta > 0 else "Unfavorable",
                "detail": f"Deal count {curr['n_deals']} vs. prior quarter {prev['n_deals']} ({'+' if vol_delta>0 else ''}{vol_delta} deals)",
                "severity": "Medium",
            })

    if cogs and cogs.get('avg_unit_var') and abs(cogs['avg_unit_var']) > 0:
        drivers.append({
            "driver": "COGS / Standard Cost",
            "direction": "Favorable" if cogs['avg_unit_var'] < 0 else "Unfavorable",
            "detail": f"Avg unit variance ${cogs['avg_unit_var']:+.0f} ({cogs.get('var_pct',0):+.1f}% of std) · {cogs.get('unfav',0)} unfavorable months",
            "severity": "High" if abs(cogs.get('var_pct',0)) >= 3 else "Medium",
        })

    if fc and fc.get('mape') and fc['mape'] > 10:
        drivers.append({
            "driver": "Demand / Forecast",
            "direction": "Unfavorable" if fc.get('bias',0) < 0 else "Neutral",
            "detail": f"MAPE {fc['mape']}% · bias {fc.get('bias',0):+.1f}% ({'over-forecast' if fc.get('bias',0)<0 else 'under-forecast'})",
            "severity": "Medium",
        })

    return {
        "business_unit": business_unit,
        "quarter":       quarter,
        "primary_drivers": drivers,
        "driver_count":    len(drivers),
    }


def get_pipeline(business_unit: str) -> dict:
    """
    Return forward-looking pipeline health: value by stage,
    approval rate, weighted pipeline, and win probability.

    Args:
        business_unit: Product category or 'All'
    """
    where = "" if business_unit == "All" else "WHERE d.product_category = ?"
    params = () if business_unit == "All" else (business_unit,)

    rows = q(DB_DEALS, f"""
        SELECT d.deal_stage,
               ROUND(SUM(d.net_deal_value)/1e6,2)          AS pipeline_mm,
               ROUND(AVG(d.gm_pct)*100,2)                  AS avg_gm,
               ROUND(AVG(d.win_probability)*100,1)          AS avg_win_prob,
               COUNT(*)                                     AS n_deals,
               COUNT(CASE WHEN s.recommendation='Approve' THEN 1 END) AS approved,
               COUNT(CASE WHEN s.recommendation='Escalate' THEN 1 END) AS escalated,
               COUNT(CASE WHEN s.recommendation='Reject' THEN 1 END) AS rejected
        FROM deals d JOIN deal_scores s ON d.deal_id=s.deal_id
        {where}
        GROUP BY d.deal_stage ORDER BY pipeline_mm DESC
    """, params)

    total    = sum(r['pipeline_mm'] for r in rows)
    approved = sum(r['approved'] for r in rows)
    total_n  = sum(r['n_deals'] for r in rows)

    return {
        "business_unit":    business_unit,
        "stages":           rows,
        "total_pipeline_mm": round(total, 2),
        "total_deals":      total_n,
        "approval_rate_pct": round(approved/total_n*100, 1) if total_n else 0,
        "escalation_rate_pct": round(sum(r['escalated'] for r in rows)/total_n*100, 1) if total_n else 0,
    }


def check_supply_chain(business_unit: str, quarter: str) -> dict:
    """
    Cross-reference supply chain data to identify if COGS overruns,
    inventory issues, or forecast errors are driving financial variances.

    Args:
        business_unit: Product category
        quarter: e.g. 'Q4 2024'
    """
    # COGS waterfall
    wf = q(DB_COGS, """
        SELECT ROUND(SUM(net_revenue)/1e6,2) net_mm,
               ROUND(SUM(cogs_total)/1e6,2) cogs_mm,
               ROUND(SUM(gross_profit)/1e6,2) gp_mm,
               ROUND(AVG(gross_margin_pct)*100,2) gm_pct,
               ROUND(SUM(materials_cogs)/SUM(cogs_total)*100,1) mat_pct,
               ROUND(SUM(yield_loss_cogs)/SUM(cogs_total)*100,1) yield_pct
        FROM margin_waterfall WHERE category=? AND quarter=?
    """, (business_unit, quarter))

    # Supplier variance
    sv = q(DB_COGS, """
        SELECT supplier,
               ROUND(AVG(total_cost_variance),2) avg_var,
               ROUND(AVG(total_cost_variance)/AVG(standard_unit_cost)*100,2) var_pct,
               COUNT(CASE WHEN favorable=0 THEN 1 END) unfav_months
        FROM standard_vs_actual WHERE category=?
        AND month LIKE '2024-1%'
        GROUP BY supplier ORDER BY avg_var DESC LIMIT 3
    """, (business_unit,))

    # Inventory position
    inv = q(DB_INV, """
        SELECT ROUND(AVG(ip.days_inventory_outstanding),1) avg_dio,
               ROUND(AVG(ip.target_dio),1) target_dio,
               ROUND(SUM(ip.inventory_value)/1e6,2) inv_mm
        FROM inventory_positions ip WHERE ip.category=?
        AND ip.month LIKE '2024%'
    """, (business_unit,))

    # Forecast accuracy
    fc = q(DB_DEMAND, """
        SELECT ROUND(AVG(fa.mape)*100,2) mape,
               ROUND(AVG(fa.bias_pct)*100,2) bias,
               SUM(fa.under_forecast) under_count,
               SUM(fa.over_forecast) over_count
        FROM forecast_accuracy fa WHERE fa.category=? AND fa.quarter=?
    """, (business_unit, quarter))

    findings = []
    wf_row = wf[0] if wf else {}
    inv_row = inv[0] if inv else {}
    fc_row  = fc[0] if fc else {}

    if wf_row.get('gm_pct') and wf_row['gm_pct'] < GM_TARGETS.get(business_unit, 35):
        gap = round(wf_row['gm_pct'] - GM_TARGETS.get(business_unit, 35), 1)
        findings.append(f"COGS-driven GM gap of {gap}pp — materials at {wf_row.get('mat_pct',0)}%, yield loss at {wf_row.get('yield_pct',0)}% of COGS")

    if inv_row.get('avg_dio') and inv_row.get('target_dio'):
        excess = round(inv_row['avg_dio'] - inv_row['target_dio'], 0)
        if excess > 5:
            findings.append(f"DIO {inv_row['avg_dio']} days vs. {inv_row['target_dio']}-day target (+{int(excess)} days excess = ${round(inv_row['inv_mm']*excess/inv_row['avg_dio'],1)}M tied up)")

    if fc_row.get('mape') and fc_row['mape'] > 10:
        findings.append(f"Forecast MAPE {fc_row['mape']}% with {fc_row.get('bias',0):+.1f}% bias — demand planning accuracy is affecting inventory build")

    top_supplier = sv[0] if sv else {}
    if top_supplier.get('var_pct') and abs(top_supplier['var_pct']) >= 2:
        findings.append(f"Top supplier variance: {top_supplier['supplier']} at {top_supplier['var_pct']:+.1f}% above standard cost ({top_supplier['unfav_months']} unfavorable months in quarter)")

    return {
        "business_unit":      business_unit,
        "quarter":            quarter,
        "cogs_waterfall":     wf_row,
        "top_supplier_variance": sv,
        "inventory":          inv_row,
        "forecast_accuracy":  fc_row,
        "sc_findings":        findings,
        "sc_risk_count":      len(findings),
    }


def flag_risks(business_unit: str) -> dict:
    """
    Identify forward-looking risks from pipeline health,
    inventory exposure, COGS trajectory, and forecast bias.

    Args:
        business_unit: Product category
    """
    risks = []

    # Pipeline escalation risk
    pipe = get_pipeline(business_unit)
    if pipe['escalation_rate_pct'] > 40:
        risks.append({
            "category": "Pipeline",
            "risk": f"High VP escalation rate ({pipe['escalation_rate_pct']}% of {pipe['total_deals']} deals) — deal approval bottleneck may slow Q1 close",
            "severity": "High",
            "recommended_action": "Recalibrate scoring model weights; coach reps on sub-15% discount structures",
        })

    # Inventory write-off risk
    slow = q(DB_INV, """
        SELECT ROUND(SUM(write_off_risk)/1e6,2) risk_mm,
               SUM(slow_mover_flag) slow_count
        FROM slow_movers WHERE category=?
    """, (business_unit,))
    if slow and slow[0]['risk_mm'] and slow[0]['risk_mm'] > 1:
        risks.append({
            "category": "Inventory",
            "risk": f"${slow[0]['risk_mm']}M write-off exposure across {int(slow[0]['slow_count'])} slow-mover flags — aging inventory risk ahead of Q1",
            "severity": "Medium",
            "recommended_action": "Initiate sellthrough program with commercial team; consider price adjustment on aged SKUs",
        })

    # COGS trajectory risk
    cogs_trend = q(DB_COGS, """
        SELECT w.quarter,
               ROUND(AVG(w.gross_margin_pct)*100,2) gm_pct,
               COUNT(CASE WHEN s.favorable=0 THEN 1 END) unfav
        FROM margin_waterfall w JOIN standard_vs_actual s
          ON w.category=s.category AND w.month=s.month
        WHERE w.category=? GROUP BY w.quarter ORDER BY w.quarter
    """, (business_unit,))
    if len(cogs_trend) >= 2:
        q1_gm = cogs_trend[0]['gm_pct']
        q4_gm = cogs_trend[-1]['gm_pct']
        if q4_gm < q1_gm - 2:
            risks.append({
                "category": "COGS",
                "risk": f"GM% declining through the year: {q1_gm}% (Q1) → {q4_gm}% (Q4) — cost structure deteriorating quarter over quarter",
                "severity": "High",
                "recommended_action": "Initiate supplier cost review; model FY2025 standard costs using Q3/Q4 actuals as new baseline",
            })

    # Demand planning risk
    fc = q(DB_DEMAND, """
        SELECT ROUND(AVG(mape)*100,2) mape, ROUND(AVG(bias_pct)*100,2) bias
        FROM forecast_accuracy WHERE category=?
    """, (business_unit,))
    if fc and fc[0]['mape'] and fc[0]['mape'] > 12:
        direction = "under-forecast (stockout risk)" if fc[0]['bias'] > 0 else "over-forecast (inventory risk)"
        risks.append({
            "category": "Demand Planning",
            "risk": f"MAPE {fc[0]['mape']}% with systematic {direction} — forecast error will compound into Q1 inventory position",
            "severity": "Medium",
            "recommended_action": "Review S&OP assumptions; implement statistical forecasting for high-MAPE SKUs",
        })

    # West region risk
    west = q(DB_CHANNEL, """
        SELECT rep_name, ROUND(quota_attainment_pct*100,1) att
        FROM rep_performance WHERE region='West' AND quota_attainment_pct < 1.0
    """)
    if len(west) >= 2:
        risks.append({
            "category": "Channel / Territory",
            "risk": f"West region: {len(west)} of 3 reps below quota ({', '.join(r['rep_name']+' '+str(r['att'])+'%' for r in west)}) — consistent regional miss points to competitive/territory issue",
            "severity": "Medium",
            "recommended_action": "Initiate West territory review before Q1 quota-setting; evaluate whether quotas need recalibration",
        })

    return {
        "business_unit": business_unit,
        "risks":         risks,
        "high_count":    sum(1 for r in risks if r['severity']=='High'),
        "medium_count":  sum(1 for r in risks if r['severity']=='Medium'),
        "total_risks":   len(risks),
    }


def build_qbr_draft(business_unit: str, quarter: str,
                    actuals_summary: str, variance_summary: str,
                    classification_summary: str, pipeline_summary: str,
                    sc_summary: str, risk_summary: str) -> dict:
    """
    Assemble all tool outputs into a structured QBR draft with
    executive summary, variance table, root cause analysis,
    supply chain cross-reference, pipeline outlook, and risks/actions.

    This tool is called last by the agent after all other tools have run.
    The agent passes summaries it has synthesized from prior tool outputs.

    Args:
        business_unit: Product category
        quarter: e.g. 'Q4 2024'
        actuals_summary: Agent's synthesis of get_actuals output
        variance_summary: Agent's synthesis of identify_variances output
        classification_summary: Agent's synthesis of classify_variance output
        pipeline_summary: Agent's synthesis of get_pipeline output
        sc_summary: Agent's synthesis of check_supply_chain output
        risk_summary: Agent's synthesis of flag_risks output
    """
    # This tool returns a structured scaffold; the LLM fills in the narrative
    return {
        "document_type":  "Quarterly Business Review",
        "business_unit":  business_unit,
        "period":         quarter,
        "prepared_by":    "QBR Prep Agent — Middleton Finance Suite",
        "prepared_at":    datetime.now().strftime("%B %d, %Y %H:%M"),
        "sections": [
            {"section": "1. Executive Summary",      "content": actuals_summary},
            {"section": "2. Financial Performance",  "content": variance_summary},
            {"section": "3. Variance Root Causes",   "content": classification_summary},
            {"section": "4. Supply Chain Analysis",  "content": sc_summary},
            {"section": "5. Pipeline Outlook",       "content": pipeline_summary},
            {"section": "6. Risks & Recommended Actions", "content": risk_summary},
        ],
        "status": "Draft — for review by Finance Manager before distribution",
    }


# ── Tool registry ─────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_actuals",
        "description": "Pull actual revenue, GM%, discount rate, and deal count for a business unit, optionally filtered to a specific quarter.",
        "input_schema": {
            "type": "object",
            "properties": {
                "business_unit": {"type": "string", "description": "Product category: GPUs, CPUs, Embedded, Semi-Custom, Adaptive Computing, or All"},
                "quarter":       {"type": "string", "description": "Quarter e.g. 'Q4 2024', or omit for full year"},
            },
            "required": ["business_unit"],
        },
    },
    {
        "name": "get_plan",
        "description": "Return the finance plan rates for a business unit: GM% target, plan discount rate, and quarterly quota.",
        "input_schema": {
            "type": "object",
            "properties": {
                "business_unit": {"type": "string", "description": "Product category or All"},
            },
            "required": ["business_unit"],
        },
    },
    {
        "name": "identify_variances",
        "description": "Find every material variance vs. plan for a business unit in a quarter, ranked by dollar impact on gross profit.",
        "input_schema": {
            "type": "object",
            "properties": {
                "business_unit": {"type": "string"},
                "quarter":       {"type": "string", "description": "e.g. 'Q4 2024'"},
            },
            "required": ["business_unit", "quarter"],
        },
    },
    {
        "name": "classify_variance",
        "description": "Classify variance root causes as pricing, volume, mix, COGS, or demand. Pulls from both sales and supply chain databases.",
        "input_schema": {
            "type": "object",
            "properties": {
                "business_unit": {"type": "string"},
                "quarter":       {"type": "string"},
            },
            "required": ["business_unit", "quarter"],
        },
    },
    {
        "name": "get_pipeline",
        "description": "Return forward-looking pipeline health: value by stage, approval and escalation rates, win probability.",
        "input_schema": {
            "type": "object",
            "properties": {
                "business_unit": {"type": "string"},
            },
            "required": ["business_unit"],
        },
    },
    {
        "name": "check_supply_chain",
        "description": "Cross-reference supply chain data — COGS waterfall, supplier variance, inventory DIO, forecast accuracy — to explain financial variances.",
        "input_schema": {
            "type": "object",
            "properties": {
                "business_unit": {"type": "string"},
                "quarter":       {"type": "string"},
            },
            "required": ["business_unit", "quarter"],
        },
    },
    {
        "name": "flag_risks",
        "description": "Identify forward-looking risks from pipeline health, inventory exposure, COGS trajectory, and forecast bias.",
        "input_schema": {
            "type": "object",
            "properties": {
                "business_unit": {"type": "string"},
            },
            "required": ["business_unit"],
        },
    },
    {
        "name": "build_qbr_draft",
        "description": "Final tool — assemble all previous tool outputs into a structured QBR package scaffold. Call this last after all other tools have run.",
        "input_schema": {
            "type": "object",
            "properties": {
                "business_unit":           {"type": "string"},
                "quarter":                 {"type": "string"},
                "actuals_summary":         {"type": "string", "description": "Your synthesis of the actuals data"},
                "variance_summary":        {"type": "string", "description": "Your synthesis of the variance analysis"},
                "classification_summary":  {"type": "string", "description": "Your root cause classification narrative"},
                "pipeline_summary":        {"type": "string", "description": "Your pipeline outlook narrative"},
                "sc_summary":              {"type": "string", "description": "Your supply chain cross-reference findings"},
                "risk_summary":            {"type": "string", "description": "Your risk flags and recommended actions"},
            },
            "required": ["business_unit", "quarter", "actuals_summary",
                         "variance_summary", "classification_summary",
                         "pipeline_summary", "sc_summary", "risk_summary"],
        },
    },
]

TOOL_MAP = {
    "get_actuals":       get_actuals,
    "get_plan":          get_plan,
    "identify_variances":identify_variances,
    "classify_variance": classify_variance,
    "get_pipeline":      get_pipeline,
    "check_supply_chain":check_supply_chain,
    "flag_risks":        flag_risks,
    "build_qbr_draft":   build_qbr_draft,
}


# ── System prompt ─────────────────────────────────────────────

SYSTEM_PROMPT = """You are the QBR Prep Agent, an autonomous finance analyst for the Middleton Finance Suite.

Your goal is to autonomously prepare a complete Quarterly Business Review package given only a business unit and quarter. You have eight tools — use them in the right sequence without being told how.

AGENT PLANNING APPROACH:
You are a senior finance analyst preparing for a QBR. Think through what you need:
1. What actually happened (actuals + plan)
2. Where we missed or beat (variances, ranked by impact)
3. Why it happened (root cause classification across sales AND supply chain)
4. What's ahead (pipeline health)
5. What could go wrong (forward risks)
6. The final document (assembled QBR draft)

TOOL SEQUENCING (you decide, but this is the logical order):
- get_actuals + get_plan → understand the performance picture
- identify_variances → find the gaps that matter
- classify_variance → diagnose root causes
- check_supply_chain → cross-reference SC data to explain COGS/margin issues
- get_pipeline → forward-looking outlook
- flag_risks → what to watch for next quarter
- build_qbr_draft → assemble everything (call this LAST)

WRITING STYLE:
- Finance-analyst-to-VP tone: direct, specific, data-grounded
- Use exact numbers from tool outputs — never generalize
- Every variance needs a root cause; every risk needs a recommended action
- The QBR draft should be something a Finance Manager could hand to the CFO

CRITICAL: Call build_qbr_draft as your final tool with your synthesized summaries.
After build_qbr_draft returns, write the complete QBR document in full — not just a summary.
"""


# ── Agent loop ─────────────────────────────────────────────────

def run_qbr_agent(client, business_unit, quarter):
    """Full agentic loop with tool use until QBR is complete."""

    goal = f"""Prepare a complete Quarterly Business Review package for the {business_unit} business unit, {quarter}.

This is an autonomous task — you decide which tools to call and in what order.
Use all relevant tools, cross-reference supply chain data, and produce a complete QBR draft.
The output should be ready for a Finance Manager to review before presenting to a VP."""

    messages = [{"role": "user", "content": goal}]
    tool_call_count = 0
    qbr_result = None

    section("Agent Planning")
    print(color(f"  Goal: Prepare {business_unit} QBR — {quarter}", "cyan"))
    print()

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            # Extract final narrative
            for block in response.content:
                if hasattr(block, "text") and block.text:
                    return block.text, qbr_result, tool_call_count
            return "", qbr_result, tool_call_count

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    tool_call_count += 1
                    name  = block.name
                    inp   = block.input
                    tid   = block.id

                    # Print agent's reasoning if present
                    for b in response.content:
                        if hasattr(b,'text') and b.text and b.text.strip():
                            print(color("  [Thinking] ", "dim") +
                                  wrap(b.text.strip()[:200], width=68, indent="").strip())
                            break

                    print(color(f"  [Tool {tool_call_count}] ", "yellow") +
                          color(name, "bold") +
                          color(f"({', '.join(f'{k}={repr(v)}' for k,v in inp.items())})", "dim"))

                    fn     = TOOL_MAP.get(name)
                    result = fn(**inp) if fn else {"error": f"Unknown tool: {name}"}

                    if name == "build_qbr_draft":
                        qbr_result = result
                        print(color("  [Agent] QBR scaffold assembled — writing final document...", "green"))

                    # Show key result snippet
                    snippet = json.dumps(result)[:120].replace('\n', ' ')
                    print(color(f"         → {snippet}...", "dim"))
                    print()

                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": tid,
                        "content":     json.dumps(result),
                    })

            messages.append({"role": "user", "content": tool_results})
        else:
            break

    return "", qbr_result, tool_call_count


# ── Output formatting ─────────────────────────────────────────

def print_qbr(narrative, qbr_meta, tool_count, bu, quarter):
    """Print the final QBR document to terminal."""
    print()
    print(color("═" * 72, "blue"))
    print(color(f"  QUARTERLY BUSINESS REVIEW — {bu.upper()} — {quarter}", "bold"))
    print(color(f"  {qbr_meta['prepared_by'] if qbr_meta else 'QBR Prep Agent'}", "dim"))
    print(color(f"  {qbr_meta['prepared_at'] if qbr_meta else datetime.now().strftime('%B %d, %Y')}", "dim"))
    print(color("═" * 72, "blue"))
    print()
    print(color(f"  Tools called: {tool_count}  |  Model: {MODEL}", "dim"))
    print(color(f"  Status: {qbr_meta['status'] if qbr_meta else 'Draft'}", "dim"))
    print()
    print(color("─" * 72, "blue"))
    print()

    import re
    lines = narrative.split("\n")
    for line in lines:
        line = re.sub(r'\*\*(.+?)\*\*', lambda m: color(m.group(1), "bold"), line)
        line = re.sub(r'^#{1,3}\s+', '', line)
        if line.strip():
            print(wrap(line, width=70, indent="  "))
        else:
            print()

    print()
    print(color("─" * 72, "blue"))
    print(color("  End of QBR Draft — Review before distribution", "dim"))
    print(color("─" * 72, "blue"))


def save_qbr(narrative, bu, quarter):
    """Save QBR to a text file."""
    filename = f"qbr_{bu.lower().replace(' ', '_')}_{quarter.lower().replace(' ', '_')}.txt"
    with open(filename, "w") as f:
        f.write(f"QUARTERLY BUSINESS REVIEW — {bu} — {quarter}\n")
        f.write(f"Generated by QBR Prep Agent — Middleton Finance Suite\n")
        f.write(f"{datetime.now().strftime('%B %d, %Y %H:%M')}\n")
        f.write("=" * 72 + "\n\n")
        f.write(narrative)
    return filename


# ── Main ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="QBR Prep Agent — Middleton Finance Suite")
    parser.add_argument("--bu",      default="GPUs",    help="Business unit (default: GPUs)")
    parser.add_argument("--quarter", default="Q4 2024", help="Quarter (default: Q4 2024)")
    parser.add_argument("--save",    action="store_true", help="Save QBR to .txt file")
    args = parser.parse_args()

    # Header
    print()
    print(color("═" * 72, "blue"))
    print(color("  Middleton Finance Suite — QBR Prep Agent", "bold"))
    print(color("  Autonomous Quarterly Business Review Analyst", "cyan"))
    print(color("═" * 72, "blue"))
    print()
    print(color("  Model:   ", "bold") + f"{MODEL}  (~$0.001/msg)")
    print(color("  Key:     ", "bold") + "export ANTHROPIC_API_KEY=sk-ant-...")
    print(color("  Get key: ", "bold") + "console.anthropic.com → API Keys")
    print()

    # Check key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(color("  ✗  ANTHROPIC_API_KEY not set.", "red"))
        print()
        print("  Steps to get a key:")
        print(color("    1. console.anthropic.com", "cyan"))
        print(color("    2. Sign up → API Keys → Create Key", "cyan"))
        print(color("    3. export ANTHROPIC_API_KEY=sk-ant-...", "yellow"))
        print(color("    4. python qbr_prep_agent.py --bu GPUs --quarter 'Q4 2024'", "yellow"))
        print()
        print("  Cost: Haiku is ~$0.001/msg. A full QBR run costs ~$0.01.")
        sys.exit(1)

    # Check databases
    missing = [db for db in [DB_PRICING, DB_DEALS, DB_CHANNEL, DB_COGS, DB_INV, DB_DEMAND]
               if not os.path.exists(db)]
    if missing:
        print(color("  ✗  Missing databases:", "red"))
        for db in missing: print(f"    {db}")
        print()
        print("  Run: python 01_generate_and_load_data.py")
        print("  Run: python 05_generate_sc_data.py")
        sys.exit(1)

    bu      = args.bu
    quarter = args.quarter

    print(color(f"  Preparing QBR for: {bu} · {quarter}", "bold"))
    print(color("  The agent will autonomously call all 8 tools.", "dim"))
    print()

    client   = anthropic.Anthropic(api_key=api_key)
    narrative, qbr_meta, tool_count = run_qbr_agent(client, bu, quarter)

    if narrative:
        print_qbr(narrative, qbr_meta, tool_count, bu, quarter)
        if args.save:
            fname = save_qbr(narrative, bu, quarter)
            print(color(f"\n  ✓  Saved to {fname}", "green"))
    else:
        print(color("  ✗  Agent did not return a narrative.", "red"))


if __name__ == "__main__":
    main()
