# Build Vriddhi — AI Growth Copilot for Paytm Merchants

You are building a hackathon prototype called **Vriddhi**: an agentic AI copilot for Paytm merchants (Track 1: Merchant Growth AI). Read this entire spec before writing code — it defines scope, architecture, data contracts, and what "done" looks like. Build in the order given under **Build order**, and stop to show me a working demo after each phase rather than building everything silently.

## 1. What this product is (read this first)

Paytm merchants already have an AI assistant in their app (voice/chat, answers questions on request, read-only). Vriddhi is the **upgrade to that assistant**: instead of only answering when asked, it continuously watches a merchant's own transaction data, **notices** growth opportunities and problems on its own, and **executes** the right action — not just recommends it. A chatbot answers; Vriddhi acts.

Capability priority order (build and demo in this order — do not build #5 before #1-4 work):
1. **Understand** — build a live picture of the merchant's business from their own data
2. **Grow sales** — diagnose a problem, execute a fix (promo, reorder)
3. **Manage operations** — inventory/reorder nudges, settlement summaries
4. **Serve customers** — auto-replies to common queries, feedback summarization
5. **Financial nudge (supporting, not the centerpiece)** — surface a loan/insurance offer only when the data genuinely supports it, with explicit merchant consent before any submission

## 2. Architecture (build exactly this shape)

```
Merchant channel (simulated Paytm merchant app + WhatsApp)
        │
        ▼
Orchestrator agent  (diagnoses, plans, routes each step — explicit plan → act → observe loop)
        │
   ┌────┼────┬────────┬──────────┐
   ▼    ▼    ▼         ▼           ▼
Insight  Growth   Ops        Service      Financial nudge
agent    action   agent      agent        agent
(RAG)    agent
   │       │        │          │             │
   ▼       ▼        ▼          ▼             ▼
merchant  campaign  merchant  campaign    lending/insurance
data      /CRM API  data      /CRM API    partner API (mock)
store     (mock)    store     (mock)           │
                                                ▼
                                    escalate to human RM above
                                    risk threshold (mock queue)

Underneath everything: shared memory + guardrails layer
(conversation state, merchant consent, risk thresholds, full audit log)
```

**Orchestrator agent**: receives a trigger (a scheduled data scan, or a merchant message) and runs an explicit **plan → act → observe** loop using LLM tool-calling. It must decide *which* sub-agent(s) to invoke and in what order, and the reasoning must be visible in a UI panel (see Section 5) — this reasoning-trace visibility is the single most important demo feature. Do not hide the plan inside a black-box single LLM call.

**Insight agent (RAG)**: retrieves relevant category benchmarks (`category_benchmarks.json`) and the merchant's own transaction history (`transactions.csv`), compares actual vs. expected patterns, and produces a diagnosis with a cited reason — e.g. "weekday revenue is 46% below your normal weekday-to-weekend ratio for apparel retailers, for 3 consecutive weeks — this is outside normal seasonal variance." Ground every claim in retrieved data; never let the LLM invent a benchmark number that isn't in `category_benchmarks.json`.

**Growth action agent**: given a diagnosis, selects and drafts a concrete action (promo campaign from `campaign_templates.json`, price tweak, bundle) and — after merchant approval in the UI — "executes" it against the mock campaign API (Section 4).

**Ops agent**: scans `catalog.csv` for items where `stock_qty` is near `reorder_threshold` relative to `units_sold_last_30d`, and drafts a reorder recommendation.

**Service agent**: matches incoming simulated customer queries against `campaign_templates.json`'s `customer_service_autoreply` templates and answers, or summarizes `customer_feedback.csv` into a short weekly digest.

**Financial nudge agent**: checks `lending_eligibility_rules.json` against a merchant's `avg_monthly_settlement_inr` and `settlement_consistency_score`, computes the eligible tier, and either (a) surfaces an auto-approvable offer with one-tap consent, or (b) escalates to a mock "relationship manager" queue per the `escalation_rules` — implement both paths, since the escalation path is explicitly part of the pitch (Glow Up Salon, merchant M004, is designed to trigger this path).

**Shared memory & guardrails layer**: not a visual agent — a service layer that:
- Persists conversation/session state per merchant (so campaign history and past nudges are remembered across sessions — this is a stated differentiator, implement it for real, don't fake it)
- Enforces guardrails before any state-changing action: max discount %, one promo per customer per 14 days, one loan nudge per 30 days, no auto-submission without explicit consent, all from the `guardrails` fields already present in `campaign_templates.json` and `lending_eligibility_rules.json`
- Writes an append-only audit log of every action taken (who/what/when/why) — expose this log in the UI, it's a concrete "enterprise-readiness" demo point

## 3. Tech stack

- **Orchestrator LLM**: Claude (or whatever LLM API key is available in this environment) with tool/function calling. Implement the plan → act → observe loop explicitly in code (a visible state machine), not by hoping the model narrates it — you need structured intermediate output to render the reasoning-trace panel.
- **Orchestration**: a hand-rolled state machine (plain TypeScript/Python, no need for LangGraph unless it's faster for you) — prioritize something you can fully explain and debug live over a framework you're translating on the fly.
- **RAG**: no need for a vector DB given the small, structured JSON/CSV data — do direct structured retrieval/filtering against `category_benchmarks.json` and `transactions.csv` (a real vector store is overkill for this dataset size and adds risk; only add embeddings if you have spare time after Phase 4).
- **Backend**: a small API server (FastAPI or Express — pick whichever this environment is faster with) exposing the mock endpoints in Section 4.
- **Frontend**: a single-page app with two panels side by side — see Section 5.
- **Data**: the provided CSV/JSON files (Section 6) as the merchant/catalog/transaction source of truth. Load them into SQLite or just read them in-memory at startup — don't spend time standing up Postgres for a hackathon demo.
- **State/memory**: SQLite (or a JSON file store if faster) keyed by `merchant_id` — must survive a page refresh during the demo.

## 4. Mock API contracts (build these as real endpoints, even though they're mocked)

```
GET  /api/merchants                          → list of merchants (from merchants.json)
GET  /api/merchants/:id                      → merchant detail
GET  /api/merchants/:id/transactions?days=90 → transaction history
GET  /api/merchants/:id/catalog              → catalog/inventory
GET  /api/merchants/:id/feedback             → customer feedback

POST /api/agent/scan/:merchantId             → triggers the orchestrator to run a full diagnostic pass
                                                 for that merchant; returns a structured plan + reasoning trace

POST /api/actions/campaign/send              → mock-sends a WhatsApp promo (log it, don't call a real WhatsApp API)
                                                 body: { merchantId, templateId, customerSegment, discountPct, itemId }

POST /api/actions/inventory/reorder          → mock-creates a reorder request (log it)
                                                 body: { merchantId, itemId, quantity }

POST /api/actions/lending/apply              → mock-submits a loan application to the "partner"
                                                 body: { merchantId, tier, amountInr }
                                                 must check escalation_rules first; if escalation triggers,
                                                 return { status: "escalated", queuedForRelationshipManager: true }
                                                 instead of auto-approving

GET  /api/audit-log/:merchantId              → full audit trail for that merchant
```

Every POST endpoint must (a) check guardrails before acting, (b) write to the audit log regardless of outcome, (c) return enough detail for the UI to show what was checked and why it passed/blocked.

## 5. Frontend — the two things judges will actually look at

**Panel A — Merchant view** (left, ~60% width): looks like a simplified Paytm merchant app screen. Shows the merchant's dashboard (revenue trend chart, catalog/stock status, recent feedback) and a card feed of Vriddhi's recommendations, each with an **Approve** / **Dismiss** button. Approving a card actually calls the corresponding `/api/actions/*` endpoint.

**Panel B — Reasoning trace** (right, ~40% width): a live, timestamped log of what the orchestrator is doing — "Scanning M001... Insight agent: retrieved category_benchmarks for apparel_sarees... Diagnosis: weekday revenue 46% below expected ratio, 3 weeks running (cites the exact numbers)... Routing to growth action agent... Drafted promo: 12% off Cotton Handloom Saree, expires in 5 days... Awaiting merchant approval." This panel is the single biggest differentiator in the whole demo — do not cut it under time pressure, cut something else first.

A merchant switcher (dropdown) at the top to flip between the 4 seeded merchants for different demo beats (see Section 7).

## 6. Datasets (provided — do not invent your own)

All files are in the attached `vriddhi_dataset.zip`. Load them as-is; do not regenerate or reinvent merchant data.

| File | Rows/Records | Purpose |
|---|---|---|
| `merchants.json` | 4 merchants | Merchant profiles, settlement history, category — read the `notes` field on each, it tells you which demo beat that merchant is for |
| `transactions.csv` | ~17,000 rows, 90 days × 4 merchants | Real transaction-level data. **M001 (Meera Sarees) has an engineered weekday sales dip in the last ~20 days** — this is the data your insight agent must detect for the main demo walkthrough. Verify it yourself: weekday revenue Sept 5-24 is meaningfully below the pre-dip weekday average, while weekends stay strong. |
| `catalog.csv` | 24 items across 4 merchants | Inventory with `stock_qty`, `reorder_threshold`, `units_sold_last_30d` — M002 (Sharma Kirana) has items intentionally close to reorder threshold, use it for the ops-agent restock demo |
| `category_benchmarks.json` | 4 categories | The RAG ground-truth the insight agent retrieves from — expected weekday/weekend ratios, seasonal notes, common causes of a dip. Ground every diagnosis claim in this file's numbers, don't let the LLM hallucinate a benchmark |
| `customer_feedback.csv` | ~20 rows | For the service agent's feedback-summarization feature |
| `lending_eligibility_rules.json` | tiers + escalation rules | The financial nudge agent's decision logic — M003 (Spice Junction) should qualify for an auto-approvable Silver/Gold tier offer; M004 (Glow Up Salon) has a `settlement_consistency_score` of 0.79, below the 0.8 escalation threshold, so it must escalate to a human RM instead of auto-nudging |
| `campaign_templates.json` | 4 template types | Promo/reorder/loan-nudge/customer-service message templates with `{placeholders}` to fill and `guardrails` fields to enforce |

## 7. Demo script to build toward (this is your acceptance test)

1. Load the app, select **Meera Sarees (M001)**. Dashboard shows the revenue chart with a visible weekday dip in the last 3 weeks.
2. Click "Run diagnostic scan" (or have it auto-trigger on merchant select). Watch Panel B populate live: insight agent retrieves the apparel benchmark, computes the actual weekday/weekend ratio from `transactions.csv`, flags the anomaly with the real numbers, routes to the growth action agent, which drafts a specific promo referencing an actual catalog item.
3. Click **Approve** on the promo card. It calls `/api/actions/campaign/send`, guardrails are checked (discount within max, no duplicate promo to same segment in 14 days), and the audit log updates.
4. Switch to **Sharma Kirana (M002)**. Ops agent flags a specific low-stock item with days-to-stockout math. Approve the reorder.
5. Switch to **Spice Junction QSR (M003)**. Financial nudge agent surfaces an eligible working-capital loan tier with the real numbers from `lending_eligibility_rules.json`. Approve → shows as auto-approved (under the escalation threshold).
6. Switch to **Glow Up Salon (M004)**. Financial nudge agent evaluates the same logic but this merchant's consistency score triggers escalation — show it landing in a "pending relationship manager review" state instead of auto-approving. This proves the system knows its own limits.
7. Open the audit log for any merchant — show every action taken, when, and why it passed guardrails.

If you can only build a subset under time pressure, this is the cut order (most important first): **(1) M001 dip-detection + reasoning trace + promo approval, (2) audit log, (3) M002 restock, (4) M003 auto-approved loan, (5) M004 escalation path.** Steps 1-2 alone, done well, are a stronger demo than all 5 done shallowly.

## 8. What "done" looks like

- All 4 merchants load with real data from the provided files, not placeholder/lorem text
- The reasoning-trace panel shows real intermediate agent steps, not a canned string
- At least the M001 dip-detection → promo-approval flow works end-to-end against the mock APIs (not just UI mockup — an actual POST that gets logged)
- Guardrails are enforced in code, not just described in a tooltip (try to break one during testing — e.g. approve the same promo twice in a row — and confirm it's blocked)
- The audit log is real and queryable per merchant
- The app survives a page refresh without losing merchant state (SQLite/file store, not just React state)

Build in this order: (1) data loading + API server + guardrail/audit logic, (2) orchestrator + insight agent against M001 only, (3) frontend Panel A + B wired to that one flow, (4) growth action agent + approve/execute, (5) ops agent + M002, (6) financial nudge agent + M003/M004 escalation split, (7) service agent + feedback summary (lowest priority — cut first if short on time).
