# Goal-Based MF Tracker — Development Plan

**Type:** Web application (responsive, data-rich, desktop-first). No mobile app / PWA.
**Status:** Engine design FROZEN. This document is the build-ready plan.
**Audience:** Indian retail MF investors with low goal-investing knowledge.

> This plan supersedes the earlier frozen-design file. It additionally locks two input-layer decisions: (a) ongoing SIP is a **confirmed user input** that the CAS only *seeds*, and (b) an **input-freshness / nudge layer** on top of the engine.

---

## 1. Product Description

A single-purpose tool that links a user's **existing** mutual fund holdings to their financial goals, explains the reasoning behind every link, and tells them — honestly, with uncertainty surfaced — whether each goal is on track.

It is deliberately *not* a feature-heavy tracker. One core job: **goal ↔ fund earmarking with traceable reasoning.** Data comes from a user-uploaded CAS PDF (snapshot model; no live API). Valuation refreshes daily from free NAV feeds. The product stays on the **analytics-only** side of SEBI's advice line: it reorganizes and explains what the user already owns; it never names a fund to buy and never issues directives.

Every output the engine produces carries a machine-stored **reasoning object**, which powers both the always-visible "why" on each card and a **context-aware chatbot** scoped strictly to the user's own portfolio and this app's outputs.

## 2. Goals

**Product goals**
- Make goal-based investing legible to someone who has never defined a goal.
- Replace false "you're on track" confidence with honest, assumption-visible, scenario-based status.
- Surface structural portfolio problems (no emergency reserve, over-concentration) *before* per-goal status.
- Every recommendation is explainable and traceable to a rule — no black box.

**Engineering goals**
- Deterministic, auditable engine; LLM used only to *phrase* and *retrieve* reasoning, never to invent it.
- Data-rich web interface exposing full analytics with progressive disclosure — never a black box, never overwhelming.
- Solo-dev maintainable: managed infra, minimal moving parts, parser maintenance isolated.

**Explicit non-goals**
- No trade execution, no buy/sell recommendations, no new-fund suggestions.
- No live brokerage/AA integration at launch.
- No single-number "score" that hides assumptions (see frozen design §7).

## 3. Design Principles & Challenges

**Principles**
- **Portfolio Health → Goal Health → Goal Allocation** ordering, everywhere.
- Descriptive, never prescriptive language ("under your assumptions…", "tracking allocation" not "recommended allocation").
- Assumptions always visible and editable at point of display.
- Detail-on-demand via expandable/collapsible panels, never navigation to separate pages or hidden menus.

**Hard challenges and how the plan addresses them**

| Challenge | Mitigation |
|---|---|
| Snapshot data goes stale between uploads | Re-value daily on NAV with no upload; nudge only on *expected change* (§9) |
| CAS has no forward SIP info | Ongoing SIP is a confirmed user input, CAS seeds a suggestion (§7.1) |
| Parser breaks when RTAs change PDF layout | Isolate parsing in one service; pin casparser version; contract tests on sample CAS set |
| False confidence from projections | p10/p50/p90 only, never a single number; path-safety shown separately |
| Regulatory (advice) exposure | Engine outputs consequences not directives; chatbot grounded + scoped; legal sign-off pre-launch |
| Data-rich web without overwhelming the user | Progressive disclosure: portfolio-health summary first, drill into full analytics on demand |
| Chatbot drifting off-scope or inventing advice | Retrieval-only over stored reasoning objects + scope guard (§7.3) |

## 4. System Architecture

**Frontend (Web app):** React + Vite + TypeScript, Tailwind, Recharts for analytics, React Query for data/state. Responsive **desktop-first**, data-rich multi-panel layout optimized for larger screens (degrades gracefully to tablet width). No service worker, no installability, no mobile single-view — this is a browser web app.

**Backend:** FastAPI (Python) — chosen because `casparser` is Python. Two logical services: **Parse service** (CAS → structured transactions/lots, then discards the PDF) and **Engine service** (pure deterministic Python module: characterization → earmarking → diagnosis → reasoning objects).

**Data & auth:** plain **PostgreSQL** (SQLAlchemy 2.0 + Alembic), owned entirely by the FastAPI backend — managed instance (Neon / Railway / Render Postgres). Auth is the backend's own JWT (email-OTP or Google **sign-in** — sign-in only, no Gmail data scope, so no CASA assessment). Tenant isolation by filtering every query on `user_id` from the JWT; optional Postgres RLS as defense-in-depth (DPDP posture).

**LLM (chatbot):** Claude API. System-prompted to answer *only* from the user's stored reasoning objects + engine outputs, scoped to this app's domain, descriptive-only.

**Free data feeds:** daily cron pulls AMFI `NAVAll` → latest NAV per scheme; mfapi.in for NAV history used in projections.

**Data flow:**
`Upload CAS → Parse service (extract txns/lots, delete PDF) → Postgres → user confirms goals + active SIPs → Engine service computes earmarks + diagnoses + reasoning objects → store → web UI reads → chatbot reads reasoning objects on demand.`

**Privacy:** PDF lives only in ephemeral storage during parse, then deleted; only derived transactions/lots/valuations persist.

## 5. Recommendation Engine — Detail (frozen)

Four layers, surfaced in the locked hierarchy **Portfolio Health → Goal Health → Goal Allocation**.

**Layer 0 — Portfolio Health (shown first).** Whole-portfolio structural read, goal-independent: emergency-reserve adequacy; aggregate equity/debt/liquid mix; concentration on **style, AMC, and sector** axes; liquidity posture. One blunt summary line. *This layer ensures green goals can never mask a broken portfolio.*

**Layer 1 — Characterization.**
- *Goal →* archetype (`emergency` / `near-term-fixed` / `education` / `retirement` / `perpetual-wealth` / `recurring-liability`), horizon, future-value target (today's cost × editable per-archetype inflation), priority, target equity band, and a **Goal Confidence tag** (High = known nominal / Medium = estimable / Low = retirement-type → leads with "this is an estimate, revisit it").
- *Holding →* asset class + equity fraction, expected return μ and volatility σ (editable), liquidity/lock-in tracked **per tax-lot** (each ELSS SIP installment unlocks 3y from its own date), tax position per lot (cost basis, unrealized gain, STCG/LTCG), style-cluster id.

**Layer 2 — Earmarking allocator (most-constrained-first).**
1. Build eligibility matrix (hard constraints gate unsuitable pairs: high-σ equity vs sub-3y goal; locked ELSS lot vs goal needing earlier cash; non-safe vs emergency).
2. Order holdings by lowest `eligible_supply ÷ eligible_demand` (scarcity relative to need).
3. Fill each goal's asset-class demand from eligible supply; split holdings fractionally.
4. Cost-aware tie-break: high-gain / high-σ equity → longest-horizon goals.
5. Over-funded goal **releases surplus** back to the eligible pool.
6. Reconcile to 100% per holding with explicit **Unallocated** bucket.
7. **Never force an unsuitable asset to fake funding** — honest gap > false fit.
8. **Respect user locks** permanently once set.

**Layer 3 — Diagnosis (three independent verdicts per goal).**
- *Sufficiency (probabilistic):* p10/p50/p90 terminal values from blended μ, σ — never a single number; labelled "illustrative scenarios, not predictions." Essential goals judged vs p10; aspirational vs p50.
- *Path-safety (separate):* named stress scenarios (Moderate −20% / Severe −35% / 2008-style −55% / COVID-style −38%+recovery) → per-goal fragility level.
- *Structural flags:* band mismatch · lot-level lock conflict · over-funding (+surplus released) · duplicate-exposure/concentration (style/AMC/sector) · emergency adequacy · **portfolio fragmentation** (too many goals → prioritize fewer).

**Frozen defaults (all editable):** horizon equity bands (<3y 0–15% … 15y+ 70–90%); inflation (education 10%, healthcare 8%, general/retirement 6%, house 6%, wedding 6%, travel 5%); μ/σ per category (liquid 6/1, short-debt 7/3, hybrid 9/8, diversified equity 12/18, small-cap 13/24, international 11/16).

**Consciously deferred:** LP/marginal-cost allocator; portfolio-derived μ/σ; non-normal return modelling/Monte Carlo; full post-tax engine.
**Declined on principle:** single-number "Allocation Confidence %" (manufactures false precision).

## 6. Recommendation Engine — Workflow

1. **Ingest.** User uploads detailed CAS (full transaction history, not summary). Parse service extracts per-scheme transactions → tax-lots (date, NAV, units, cost basis), classifies transaction types from narration (purchase / SIP / switch-in-out / redemption / IDCW / stamp-duty), reconstructs STPs from switch pairs. PDF deleted.
2. **Seed SIP suggestion.** Detect recurring monthly purchases (same scheme, similar amount, monthly cadence over last N months) → present as a *suggested active-SIP list*. **User confirms/edits amount, scheme, run-until.** (CAS cannot tell active vs stopped — confirmation closes that gap.)
3. **Elicit goals (gamified onboarding).** If no goals: "Why are you investing?" → archetype picker → amount + horizon + priority. Confidence tag auto-assigned.
4. **Characterize** goals and holdings (Layer 1).
5. **Portfolio Health** computed (Layer 0) — rendered at top.
6. **Earmark** (Layer 2) → produces `(holding, goal, %)` rows + Unallocated, each with a reasoning object.
7. **Diagnose** (Layer 3) → sufficiency envelope, path-safety, structural flags, each with a reasoning object.
8. **Render** the web UI from the computed objects.
9. **Refresh loop** (§9): daily NAV re-valuation (no upload); nudge on expected change; re-run engine on each new upload or assumption edit.

Every step that emits a user-facing statement writes a **reasoning object** (§7.2) — that is what makes "every action has a why" literally true rather than cosmetic.

## 7. Reasoning Layer & Chatbot

### 7.1 Ongoing SIP handling (locked)
Existing corpus needs no SIP/lumpsum distinction (a unit is a unit). Only *future contributions* matter for sufficiency, and those are a **confirmed user input**, never read as truth off the CAS. The CAS only seeds the suggestion (workflow step 2). Tax-lots, by contrast, *are* taken from the CAS — which is why the **detailed** statement (full history) is required, not the summary.

### 7.2 Reasoning objects (the spine of "every action has a why")
Each recommendation/flag stores a structured object:
```
ReasoningObject {
  id, type (earmark | flag | sufficiency | path_safety | portfolio_health),
  subject_ref (goal_id | holding_id | earmark_id | portfolio),
  rule_id,                       // which deterministic rule fired
  inputs_used [],                // the exact numbers/assumptions used
  assumptions_referenced [],     // editable assumptions this depends on
  plain_language                 // template-filled human explanation
}
```
The card UI shows `plain_language`; the chatbot retrieves the full object.

### 7.3 Chatbot — context-aware and strictly scoped
- **Grounding:** answers *only* from the user's stored reasoning objects + engine outputs. It explains the engine's already-computed deterministic reasoning; it does **not** generate new financial reasoning or advice.
- **Reference-aware:** can be invoked on any specific card ("why is my flexicap split across two goals?") and resolves the referenced `subject_ref`.
- **Scope guard:** a pre-check classifies each query as in-scope (this user's portfolio / goals / earmarks / flags / the app's own behavior) or out-of-scope (stock tips, tax filing, markets, anything else). Out-of-scope → polite refusal + redirect, no answer.
- **Honesty rule:** if the engine didn't compute something, the bot says so rather than inventing — preserves the analytics-only line.
- **System-prompt invariants:** descriptive not prescriptive; never name a fund to buy; always reference the assumptions an answer depends on.

## 8. UX Design (web, data-rich)

### 8.1 Layout
Responsive **three-panel** desktop layout, honoring the locked **Portfolio Health → Goal Health → Goal Allocation** ordering:
- **Left rail** — Portfolio Health (the blunt structural summary + flags) shown *first/topmost*, then the goals list/navigation.
- **Center** — the selected goal's full analytics: p10/p50/p90 projection chart, glide path, all named stress scenarios, allocation/earmark breakdown, tax-lot table, and concentration views (style/AMC/sector).
- **Right rail** — the reasoning list for the current view + the chatbot panel.

All engine/LLM outputs are exposed. Usability is preserved through **progressive disclosure** within panels (collapsible sections, expand-on-demand), never through hidden menus or navigating away. Degrades gracefully to tablet width; no dedicated mobile view.

### 8.2 Reasoning, everywhere
Every figure, earmark, status chip, and flag carries its `plain_language` "why" inline (or one expand away), and the full reasoning object is retrievable via the chatbot. This is what makes "every action has a why" literal rather than decorative.

### 8.3 Gamified onboarding
A simple progress bar across steps: *Why invest → Pick goals → Set amounts/horizons → Upload CAS → Confirm SIPs → Confirm earmarks.* A small badge per completed step ("Goals defined", "Portfolio linked"). Kept short; no guilt/streak mechanics (would clash with the calm, honest tone).

## 9. Input-Freshness / Nudge Layer (locked)
- **Daily NAV re-valuation** of existing holdings — no upload needed; dashboard stays live on price.
- **Nudge on expected change, not the calendar:** if confirmed SIPs exist, nudge a few days after installments post ("your SIPs should have posted — refresh to stay accurate"); otherwise a gentle quarterly prompt. Always state *why now*; trivially snoozable; user-set cadence.
- **Delivery on launch-safe rails only:** reminder email/push with one-tap link to the upload screen, or **email-forward-to-dedicated-address** ingestion (validate sender → parse). **No Gmail OAuth / restricted scope at launch** (avoids CASA).
- Onboarding tip: help the user set up CAMS/KFintech's **recurring monthly CAS to their own email**, so the nudge becomes "forward this month's" rather than "go generate one".

## 10. Build Spec — Data Model

```
users(id, email, ...)                          -- backend-owned auth (JWT)
holdings(id, user_id, scheme_code, scheme_name, amc, category,
         asset_class, equity_fraction, style_cluster_id, sector_tags)
tax_lots(id, holding_id, units, nav_at_buy, cost_basis, buy_date,
         lock_until, gain_type)                 -- lot-level; ELSS unlock per lot
active_sips(id, user_id, scheme_code, amount, cadence, run_until,
            source = 'detected'|'confirmed')    -- confirmed input
goals(id, user_id, archetype, horizon_date, target_today, inflation_rate,
      target_future_value, priority, confidence_tag, equity_band_low,
      equity_band_high, glide_start_date)
earmarks(id, holding_id, goal_id, percentage, locked_by_user bool)
                                                -- must reconcile to 100%/holding
assumptions(id, user_id, key, value, is_default) -- bands, μ, σ, inflation, stress set
nav_cache(scheme_code, nav, nav_date)            -- daily AMFI pull
reasoning_objects(id, user_id, type, subject_ref, rule_id, inputs_used jsonb,
                  assumptions_referenced jsonb, plain_language)
diagnoses(id, goal_id, p10, p50, p90, sufficiency_verdict,
          path_safety_fragility, structural_flags jsonb)
```

**Core modules / endpoints**
- `POST /cas/upload` → parse, persist txns/lots, delete PDF, return detected-SIP suggestions.
- `POST /sips/confirm` → store confirmed active SIPs.
- `POST /goals` / `PATCH /goals/:id` → CRUD + characterization.
- `POST /engine/run` → Layers 0–3 → earmarks + diagnoses + reasoning objects.
- `PATCH /assumptions` → edit → triggers `engine/run`.
- `PATCH /earmarks/:id` → user lock/adjust → re-reconcile.
- `POST /chat` → scoped, grounded query over reasoning objects.
- `cron /nav/refresh` → daily AMFI pull + re-valuation.

**Engine module boundaries (pure Python, no I/O):** `characterize.py`, `eligibility.py`, `allocate.py` (most-constrained-first), `diagnose.py` (sufficiency / path_safety / flags), `reasoning.py` (templated objects). Unit-testable in isolation.

## 11. Scenario Test Suite (acceptance criteria, expected outcomes)

Each is an executable acceptance test: given the portfolio/goals, assert the engine's behavior. Expected results are against the frozen logic.

| # | Scenario | Expected engine behavior | Result |
|---|---|---|---|
| 1 | Typical SIP investor (flexicap + index + liquid) | Emergency←liquid (flag ₹1L gap); Trip←small equity sleeve; Education/Retirement←equity; Retirement tagged Low-confidence | **PASS** |
| 2 | Small-cap-only, near-term House goal | Small-cap ruled **ineligible** for House; House largely **Unallocated**; "cannot safely earmark" message | **PASS** |
| 3 | All-ELSS, half locked | Unlocked lots → Emergency first; locked lots → Retirement; still-underfunded shown honestly, locked units never forced into Emergency | **PASS** |
| 4 | Goal overload (10 goals) | Each thinly funded; **portfolio-fragmentation flag** fires recommending prioritization | **PASS** |
| 5 | One goal massively over-funded | Over-funding flag; **surplus auto-released** to eligible pool for other goals | **PASS** |
| 6 | User has existing mental allocations | User locks honored; engine allocates around locks, never overrides | **PASS** |
| 7 | Market crash on a long-horizon funded goal | Status dips but path-safety notes horizon allows recovery; no panic/action implied | **PASS** |
| 8 | Wealthy investor, all goals covered | No invented reallocation; "portfolio exceeds stated goals"; remainder Unallocated | **PASS** |
| 9 | CAS uploaded, zero goals | Onboarding "Why are you investing?" archetype elicitation triggers | **PASS** |
| 10 | Wealth-creation-only (perpetual) | **No "on track"/completion**; only policy band + current mix + drift | **PASS** |
| 11 | 100% equity, no liquid, emergency goal | Emergency Unallocated (correct) **and** Layer-0 Portfolio Health surfaces "no emergency reserve / 100% equity / no debt" *before* goal status — so the portfolio-level problem isn't hidden by individual goal cards | **PASS** (passes *because* of the portfolio-health layer) |

All 11 pass against the frozen design. Scenario 11 is the one that would have failed pre-freeze and is the reason Layer 0 exists.

## 12. Testing Criteria (full)

- **Unit:** each engine module pure-function tested — band derivation, eligibility gating, scarcity ordering (incl. supply÷demand tie cases), lot-level ELSS unlock math, FV/projection math, surplus release, reconciliation to 100%.
- **Parser contract tests:** a fixture set of real CAS layouts (CAMS + KFintech, summary vs detailed); assert correct lot extraction and txn-type classification; alert on layout drift.
- **Integration:** upload → parse → confirm → run → render path; assumption edit re-runs engine; user lock re-reconciles.
- **Acceptance:** the 11 scenarios in §11 as automated tests.
- **Chatbot tests:** (a) in-scope "why" resolves to the correct reasoning object; (b) out-of-scope queries (stock tips, tax filing, general markets) are refused; (c) the bot never emits a buy/sell directive or names a fund to buy; (d) bot states "not computed" rather than inventing when the engine lacks the answer.
- **Web UI tests:** the data-rich layout renders the full `DashboardState`; Portfolio Health is shown before any goal status; progressive disclosure (expand/collapse) works; layout holds at common desktop and tablet widths.
- **Regulatory copy audit:** scan all user-facing strings for prohibited terms ("recommend", "should", "best", "optimal", "buy", "suitable") outside approved contexts.

## 13. Build Plan (solo-dev sequencing)

These are construction milestones for the **one frozen scope** — not feature tiers to ship-then-revise. Order minimizes rework by building the deterministic core first and the renderers last.

1. **Foundation** — PostgreSQL instance + SQLAlchemy/Alembic schema (from §10); backend JWT auth; FastAPI skeleton; CI + test harness.
2. **Parse service** — integrate pinned `casparser`; lot extraction; txn-type classification; STP reconstruction; parse-and-discard; SIP-pattern detection. Land parser contract tests here.
3. **NAV feed** — daily AMFI cron + `nav_cache`; mfapi history fetch for projections.
4. **Engine core (pure Python)** — characterize → eligibility → allocate → diagnose → reasoning objects. Build §11 + §12 unit/acceptance tests *alongside* this; this is where correctness is won.
5. **API layer** — upload, sips/confirm, goals, engine/run, assumptions, earmarks, chat endpoints.
6. **Web app shell + onboarding** — app shell, auth wiring, gamified onboarding flow, daily re-valuation + nudge layer.
7. **Data-rich analytics UI** — three-panel layout, Portfolio Health rail, goal analytics (p10/p50/p90, glide path, named stress scenarios), tax-lot tables, concentration views, inline reasoning.
8. **Chatbot** — Claude API, retrieval over reasoning objects, scope guard, chatbot test suite.
9. **Freshness/nudge** — email/push reminders, email-forward ingestion, recurring-CAS onboarding tip.
10. **Hardening** — regulatory copy audit, DPDP review (retention/deletion), web + acceptance pass, **legal sign-off on advice positioning before public launch**.

**Standing external dependency:** SEBI-specialist opinion on the analytics-only positioning — must clear before launch; does not block construction.
