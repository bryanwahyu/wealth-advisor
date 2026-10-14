# Wealth Advisor Assistant

A small multi-agent system that reads a client's financial data, pulls context from a CRM and a market-data feed, detects anomalies and risk signals, and produces prioritized recommendations for a wealth advisor. A human review checkpoint sits before anything is final, and each run is stored in long-term memory (SQLite by default, ScyllaDB optional) so the next run can tell new problems from recurring ones.

The core runs on the Python 3.10+ standard library. ScyllaDB and Claude are optional extras.

```bash
python3 scripts/generate_sample_data.py          # writes data/ (already committed)
python3 -m wealth_advisor run --all --review auto
```

```
C001   completed                risk=90   findings=11  recs=11  -> output/C001/<run>.md
C002   completed_with_warnings  risk=38   findings=3   recs=3   -> output/C002/<run>.md
C003   completed_with_warnings  risk=24   findings=3   recs=2   -> output/C003/<run>.md
C004   completed                risk=0    findings=0   recs=0   -> output/C004/<run>.md
C005   failed                   risk=-    findings=0   recs=0   -> output/C005/<run>.md
```

## Contents

1. [Architecture](#1-architecture)
2. [Agent interactions and flow](#2-agent-interactions-and-flow)
3. [Tools](#3-tools)
4. [Analysis and anomaly detection](#4-analysis-and-anomaly-detection)
5. [Memory](#5-memory)
6. [Human-in-the-loop checkpoint](#6-human-in-the-loop-checkpoint)
7. [Error handling](#7-error-handling)
8. [Logging and observability](#8-logging-and-observability)
9. [Key decisions and trade-offs](#9-key-decisions-and-trade-offs)
10. [Assumptions](#10-assumptions)
11. [Running the project](#11-running-the-project)
12. [Sample input and output](#12-sample-input-and-output)
13. [Project layout](#13-project-layout)

## 1. Architecture

```
                         ┌──────────────────────────────┐
   CLI (__main__.py) ───▶│      Orchestrator Agent      │
                         │  plan, routing, checkpoint,  │
                         │  status, persistence         │
                         └──┬──────────┬──────────┬─────┘
              Task(kind)    │          │          │        Reviewer (HITL)
                 ┌──────────▼───┐ ┌────▼──────┐ ┌─▼──────────┐   auto | defer |
                 │ Data Fetcher │ │ Analyzer  │ │  Advisor   │   interactive | file
                 └──────┬───────┘ └────┬──────┘ └─────┬──────┘
                        │   shared SessionMemory (short-term)
                        ▼              ▼              ▼
                 ┌─────────────────────────────────────────────┐
                 │ ToolExecutor: retry, backoff, circuit       │
                 │ breaker, fallback, session cache, logging   │
                 └──────────────────────┬──────────────────────┘
                                        │ call("name", **kwargs)
   ┌──────────────┬──────────────┬──────┴───────┬──────────────┬───────────────┐
   client_data.*  crm.get_client market.quotes  analysis.*     llm.summarize   memory.*
   (JSON files)   (mock CRM API) (mock feed)    (pure funcs)   (Claude/template) (SQLite|Scylla)
```

Layers, from the outside in:

| Layer | Package | Responsibility |
|---|---|---|
| Composition root | `app.py` | The only module that imports concrete tools and agents and wires them. |
| Orchestrator | `agents/orchestrator.py` | Runs the plan, routes tasks by kind, decides whether a failure is fatal, runs the review checkpoint, persists. |
| Specialized agents | `agents/` | Data Fetcher, Analyzer, Advisor. Each declares the task kinds it handles. |
| Tool layer | `tools/` | `Tool` interface, `ToolRegistry`, `ToolExecutor`. Agents call tools by name and never import implementations. |
| Domain logic | `analytics/` | Metrics and detectors as pure functions. No I/O, easy to test. |
| Memory | `memory/` | `SessionMemory` (short-term) and `LongTermMemory` with SQLite and ScyllaDB backends. |
| Cross-cutting | `observability.py`, `hitl.py`, `report.py` | Structured logs, review checkpoint, report rendering. |

## 2. Agent interactions and flow

Agents talk through two channels. The orchestrator sends each one a `Task(kind, payload)` and gets back an `AgentResult(ok, output, issues, degraded, error)`. The agents share facts (profile, metrics, findings, recommendations) through `SessionMemory`, which also records every decision as an event, so the run leaves an audit trail.

```
Orchestrator           DataFetcher            Analyzer               Advisor              Reviewer
     │ fetch_client_data   │                      │                      │                    │
     ├────────────────────▶│ client_data.load     │                      │                    │
     │                     │ client_data.normalize│                      │                    │
     │                     │ crm.get_client  (optional)                  │                    │
     │                     │ market.get_quotes (fallback: file prices)   │                    │
     │◀──── ok/degraded ───┤ session: profile, crm, quotes, as_of        │                    │
     │ analyze             │                      │                      │                    │
     ├───────────────────────────────────────────▶│ analysis.metrics     │                    │
     │                     │                      │ analysis.detect_anomalies                 │
     │                     │                      │ memory.recall (recurrence, trend)         │
     │◀──────────────────────────── ok ───────────┤ session: findings, risk_score            │
     │ advise              │                      │                      │                    │
     ├──────────────────────────────────────────────────────────────────▶│ playbooks          │
     │                     │                      │                      │ llm.summarize      │
     │◀───────────────────────────────── ok, needs_review ───────────────┤                    │
     │ review (only if risk >= 60 or an action needs sign-off)           │                    │
     ├───────────────────────────────────────────────────────────────────────────────────────▶│
     │◀──────────────────────────────── decisions | None (defer) ─────────────────────────────┤
     │ memory.store_run, memory.store_session                                                 │
     │ build report (JSON + Markdown)                                                          │
```

The plan is data (`DEFAULT_PLAN` in `orchestrator.py`), not hard-coded calls. Routing is a lookup from task kind to agent, built when the orchestrator starts, and it fails at startup if two agents claim the same kind or a step has no handler. Adding an agent (for example a tax or goal-planning agent) means writing a class with `handles = {"plan_goals"}` and adding a `Step` to the plan.

Run status:

| Status | Meaning |
|---|---|
| `completed` | All steps succeeded with live data; review approved or not required. |
| `completed_with_warnings` | Finished, but with degraded sources (CRM down, stale prices, memory off) or data-quality issues. The report lists each one. |
| `pending_review` | A reviewer deferred, or some recommendations still have no decision. |
| `failed` | A required step failed (no client file, corrupt JSON, nothing to analyze). The report says which step and why. |

## 3. Tools

Every external call or processing module is a `Tool` with a `name`, a `description` and `run(**kwargs)`. Tools raise typed errors (`TransientToolError`, `NotFoundError`, `ValidationError`) and may define `fallback()`. The `ToolExecutor` adds the behaviour every call needs:

- retries with exponential backoff, only for errors marked `retryable`
- a per-tool circuit breaker (opens after N failed calls, half-opens after 30s)
- fallback when retries run out, with the result marked `source="fallback"`
- a per-session cache for tools flagged `cacheable`
- one log record per attempt, retry, fallback and circuit change
- it never raises; agents get a `ToolResult` and decide what a failure means for them

| Tool | Purpose | Failure behaviour |
|---|---|---|
| `client_data.load` | Read `data/clients/<id>.json` | Missing file → `NotFoundError`; bad JSON or unsafe id → `ValidationError` |
| `client_data.normalize` | Validate and convert to domain models | Drops bad rows and reports each one; only a client-id mismatch is fatal |
| `crm.get_client` | KYC expiry, last contact, open tickets | Mock supports `flaky`, `down`, `slow`; 404 is not retried |
| `market.get_quotes` | Price and 30-day change | Fallback: last price from the client file, marked stale |
| `analysis.metrics` | Cash flow, savings rate, allocation, emergency fund | |
| `analysis.detect_anomalies` | Runs all detectors | A crashing detector is isolated and reported; the others still run |
| `llm.summarize` | Advisor briefing | Template fallback when the LLM is off, missing, erroring or refusing |
| `memory.recall` / `memory.store_run` / `memory.store_session` | Long-term memory | Transport errors retried; a disabled store is not |

`MockCRMService` and `MockMarketDataService` sit behind small protocols (`CRMClient`), so a real HTTP client can replace them in `app.py` without touching an agent.

## 4. Analysis and anomaly detection

Detectors are pure functions over the normalized profile, metrics and CRM record. Thresholds live in `config.py`.

| Code | Category | Signal | Method |
|---|---|---|---|
| `POSSIBLE_STRUCTURING` | transaction | Several transfers just under the IDR 500M reporting line within 3 days | Band 10% below threshold, sliding window |
| `LARGE_OUTFLOW` | transaction | Outflow above 50% of monthly income | Escalated to high for a new or foreign counterparty, critical for both |
| `AMOUNT_OUTLIER` | transaction | Payment far above the client's norm | Modified z-score (median/MAD) per category, with a 2x-median floor; recurring payees skipped |
| `HIGH_VELOCITY` | transaction | Burst of outflows in one day | Count per day |
| `SPENDING_SPIKE` | trend | Last complete month vs trailing average | Ratio ≥ 1.4 |
| `NEGATIVE_CASHFLOW` | trend | Spending above income for 3 months running | Monthly net, investments excluded |
| `CONCENTRATION` | portfolio | Single equity or crypto position above 30% | Funds, bonds and cash excluded |
| `RISK_PROFILE_MISMATCH`, `CRYPTO_OVEREXPOSURE` | portfolio | Allocation outside policy for the client's risk profile | `RiskPolicy` limits |
| `DRAWDOWN` | portfolio | Position down 15%+ in 30 days and over 5% weight | Market feed |
| `LOW_LIQUIDITY` | portfolio | Cash below 3 months of spending | Spending excludes transfers and investments |
| `STALE_PRICES` | portfolio | Valuation used non-live prices | Price source per position |
| `KYC_EXPIRED`, `KYC_EXPIRING`, `STALE_RELATIONSHIP`, `OPEN_COMPLAINT` | relationship | CRM signals | Dates relative to the analysis date |

When several detectors flag the same transaction, the most specific explanation wins (structuring beats large outflow, which beats statistical outlier), so one transfer does not produce three findings.

The risk score is `100 * (1 - exp(-Σ severity_weight / 25))` with weights low=1, medium=3, high=6, critical=10. It is bounded, does not depend on order, and one critical finding alone lands around 33.

The Advisor maps finding codes to playbooks (action, base priority, whether sign-off is needed). Critical findings force priority 1 and sign-off. A finding that was also present in a previous run moves up one priority level, because it has gone unresolved.

## 5. Memory

### Short-term: `SessionMemory`

One instance per run. It holds the facts the agents share, caches tool results for the session, and keeps an ordered event trail (task received, fact written, decision taken, review outcome). At the end of the run the trail is written to long-term storage with a 24-hour TTL.

### Long-term: `LongTermMemory`

Stores a summary of each run and every finding, keyed for the two questions the Analyzer asks:

1. What were the latest runs for this client? (risk trend, findings resolved since last time)
2. Has this client had finding X before? (marks findings as recurring)

Two backends implement the same interface:

| | SQLite (default) | ScyllaDB (`--memory scylla`) |
|---|---|---|
| Setup | none, one file under `output/` | `docker compose up -d scylla` |
| Use | local runs, tests, reviewers | multi-instance, high write volume, multi-DC |

The Scylla design (`memory/scylla.py`, `memory/migrations/001_init.cql`) follows the usual Scylla rules:

- Query-first, denormalized tables, one per read pattern:
  - `advisor_runs_by_client ((client_id), created_at DESC, run_id)` for "latest N runs" as a single-partition read
  - `findings_by_client_code ((client_id, code), created_at DESC, run_id)` for recurrence checks
  - `advisor_runs_by_id ((run_id))` for lookup by id
  - `session_events ((session_id), seq)` with `default_time_to_live = 86400`; the long-term tables have no TTL
- A run and its finding rows go in one LOGGED batch; the single-partition session trail uses an UNLOGGED batch.
- Prepared statements, `LOCAL_QUORUM`, `TokenAwarePolicy(DCAwareRoundRobinPolicy(local_dc))`.
- Timestamps as `bigint` unix milliseconds. Last-write-wins only, so there are no LWT, counters or materialized views.
- Migrations are embedded `.cql` files using `IF NOT EXISTS`, tracked in `schema_migrations`, and applied at startup. Keyspace and DC names are validated before they are substituted into CQL.
- Settings come from `SCYLLA_HOSTS`, `SCYLLA_PORT`, `SCYLLA_KEYSPACE`, `SCYLLA_LOCAL_DC`, `SCYLLA_RF`, `SCYLLA_USERNAME`, `SCYLLA_PASSWORD`.

One rule applies to every backend: **a storage error is never turned into an empty result.** "No history" and "could not read history" lead to different advice, because a recurring finding would be reported as new. So:

- If the configured backend cannot be reached at startup, the run continues with memory disabled. It does not switch to another store, which would split a client's history across two databases.
- If `memory.recall` fails, the report says `history.status = "unavailable"` and the run ends `completed_with_warnings`.
- If `memory.store_run` fails, the report says `memory.persisted = false`.

## 6. Human-in-the-loop checkpoint

The checkpoint runs after recommendations are drafted and before the run is persisted as final. It triggers when the risk score is 60 or higher, or when any recommendation needs sign-off (compliance escalation, rebalancing, KYC suspension, verifying large transfers). Otherwise the recommendations are auto-approved, and the report says so.

| Reviewer | Use |
|---|---|
| `--review interactive` | Terminal prompt per recommendation: approve, reject, modify (new action text plus a note), or defer the whole run. Low-risk items are approved in one batch. |
| `--review file --review-file decisions.json` | Asynchronous review or override. Unknown ids are reported; recommendations without a decision stay `pending_review`. |
| `--review defer` | Parks the run as `pending_review` for a human. |
| `--review auto` | Approves everything and labels each decision as automatic. For demos and CI. |

If a reviewer raises, or stdin closes mid-review, the run is deferred. A failing review UI never approves anything.

```json
{"reviewer": "r.wijaya", "decisions": [
  {"id": "REC-CONCENTRATION", "decision": "reject", "note": "long-term dividend holding agreed with client"},
  {"id": "REC-LARGE_OUTFLOW", "decision": "modify", "action": "Visit client in person to verify the HK transfer"}
]}
```

## 7. Error handling

| Failure | Where it is caught | Outcome |
|---|---|---|
| Client file missing, corrupt JSON, path traversal in the id | `client_data.load` → Data Fetcher | Run `failed`, report names the step and reason |
| Bad rows (no amount, bad date, duplicate id, holding without symbol) | `client_data.normalize` | Row dropped, data-quality issue recorded, analysis continues |
| `monthly_income` missing | normalizer | Inferred from the median of monthly salary-type inflows, flagged |
| Unknown or miscased `risk_profile` | normalizer | Lowercased, or defaults to conservative (the safest limits), flagged |
| CRM 5xx, timeout, flaky | executor retries, then Data Fetcher | Relationship checks skipped, `completed_with_warnings` |
| CRM 404 | not retried | Same as above, source shown as `not_found` |
| Market feed down | `market.get_quotes` fallback | Client-file prices, `STALE_PRICES` finding |
| Repeated tool failures | circuit breaker | Later calls short-circuit instead of waiting on a dead dependency |
| A detector throws | `analysis.detect_anomalies` | Other detectors still run; the error is in the data-quality section |
| An agent throws | `Agent.handle` | Converted to a failed `AgentResult` with the error logged; never crashes the orchestrator |
| LLM unavailable, erroring or refusing | `llm.summarize` fallback | Template briefing, reason recorded |
| Memory store down | memory tools | Run continues; report states memory was unavailable |
| Reviewer crashes or no input | orchestrator checkpoint | Run deferred, nothing auto-approved |

Try them:

```bash
python3 -m wealth_advisor run C001 --review auto --simulate-crm down --simulate-market down
python3 -m wealth_advisor run C001 --review auto --simulate-crm flaky     # succeeds on retry
python3 -m wealth_advisor run C003 --review auto                         # messy file, not in CRM
python3 -m wealth_advisor run C005 --review auto                         # corrupt JSON -> failed
python3 -m wealth_advisor --memory none run C001 --review auto           # memory off
```

## 8. Logging and observability

- Console: one readable line per event with time, level, agent and event name.
- `logs/run-<batch>.jsonl`: one JSON object per event with `run_id`, `agent`, `event`, `msg` and structured `data` (tool name, attempt, duration, error type, decision details).
- `run_id` and the active agent come from `contextvars`, so every record from tools and nested calls is attributed without passing loggers around.
- Event names are stable and greppable: `route`, `agent.task.done|failed`, `tool.call|error|fallback|circuit_tripped|cache_hit`, `hitl.requested|result|skipped`, `memory.unavailable|persist_failed`, `run.start|done|aborted`.
- Each report also carries an `execution` trace (step, agent, ok, degraded, error) and the session event trail is stored in memory.

```bash
grep '"event": "tool.fallback"' logs/*.jsonl
jq -c 'select(.run_id=="<run_id>") | [.agent,.event,.msg]' logs/run-*.jsonl
```

## 9. Key decisions and trade-offs

**Deterministic analysis, LLM only for prose.** Findings, severities, scores and recommendations come from code. For an advisory and compliance setting that makes results reproducible, auditable and testable, and the system runs with no API key. The cost is less flexibility on unusual cases. Claude (`--llm`, model `claude-opus-5-5`) writes only the briefing, from a JSON of facts the code already decided, with an instruction not to add numbers. Refusals and errors fall back to the template. The natural next step for the LLM is triage of `medium` findings, where judgement helps, with its output still going through the review checkpoint.

**A fixed plan instead of LLM-driven routing.** The orchestrator follows a declared plan and routes by task kind. An LLM planner could pick steps dynamically, but this workflow is the same every time, and a fixed plan is cheaper, faster and easier to debug. The plan being data keeps the door open.

**In-process agents.** Agents are Python objects that call each other through the orchestrator, not services behind a queue. That keeps the project small and runnable with one command. The `Task`/`AgentResult` contract is serializable, so moving agents behind a queue later does not change their code.

**Tool executor owns resilience.** Retry, backoff, circuit breaking, fallback and caching live in one place instead of in every agent. Agents only decide what a failed result means for their task: fatal for client data, a warning for CRM.

**Degrade instead of fail, but say so.** Only missing or unusable client data stops a run. Everything else produces a partial report with `completed_with_warnings` and a list of what was missing. An advisor gets the structuring alert even when the CRM is down.

**Unknown is not empty.** Memory errors are never read as "first run" (see section 5). Silently replacing a transport error with an empty result hides real problems and changes the advice.

**Robust statistics over mean and standard deviation.** Median/MAD does not get pulled by the outliers it is trying to find. Recurring-payee detection and a materiality floor remove the obvious false positives (monthly rent, small swings in dining spend). Detection is per client only; there is no peer-group comparison.

**SQLite default, ScyllaDB optional.** Reviewers can run everything without Docker, and the Scylla backend shows how this would run at scale. Both backends answer the same two queries with the same keys.

**Standard library only for the core.** No framework (LangChain, CrewAI and similar). The assignment is about agent abstraction, so the abstractions are written out and kept small.

## 10. Assumptions

- Amounts are in IDR. Negative amounts are outflows, positive are inflows.
- The reporting threshold for structuring is IDR 500,000,000 (the PPATK cash-transaction line), configurable in `config.py`.
- The analysis date is the client file's `statement_end`, else the latest transaction date, else `--as-of`. Wall-clock time is never used, so the same input gives the same output.
- A month counts as complete only once it has ended relative to the analysis date, so a partial month never shows up as a dip.
- `investment`, `savings` and `transfer_internal` are not spending. `transfer` (payments to third parties) is tracked separately from consumption so a large transfer does not inflate the emergency-fund target.
- Risk limits per profile (`RiskPolicy`): conservative 30% equity+crypto and 0% crypto, moderate 60% and 5%, aggressive 90% and 15%.
- Cash holdings are valued at par. Positions without a quote fall back to the client file's last price, then to cost basis.
- The CRM and market feed are mocked from JSON files; their failure modes are simulated.
- Reviewer identity comes from the decisions file or the reviewer type. There is no authentication.

## 11. Running the project

Requirements: Python 3.10+.

```bash
cd wealth-advisor
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"                 # pytest only; the core has no dependencies

python3 -m wealth_advisor run C001                     # interactive review
python3 -m wealth_advisor run --all --review auto      # every client in data/clients
python3 -m wealth_advisor run C001 --review file --review-file samples/review_decisions.json
python3 -m wealth_advisor run C001 --review auto --json   # also print the JSON report
python3 -m wealth_advisor history C001                 # runs stored in long-term memory

pytest -q
```

Reports go to `output/<client_id>/<run_id>.{json,md}`, logs to `logs/`.

### With ScyllaDB

```bash
pip install -e ".[scylla]"
docker compose up -d scylla             # wait until healthy (about 30-60 s)
python3 -m wealth_advisor --memory scylla run --all --review auto
python3 -m wealth_advisor --memory scylla history C001
SCYLLA_HOSTS=127.0.0.1 pytest -q tests/test_scylla_integration.py
```

If Scylla is not reachable, the driver spends about 20-30 seconds trying before the run continues with memory disabled.

### With Claude for the briefing

```bash
pip install -e ".[llm]"
export ANTHROPIC_API_KEY=...
python3 -m wealth_advisor run C001 --review auto --llm
```

The request opts into server-side refusal fallbacks (`fallbacks: "default"`). Without the package or a key, the run uses the template briefing and records why.

### CLI reference

```
python3 -m wealth_advisor [--memory sqlite|scylla|none] [--data-dir D] [--output-dir D] run
    [CLIENT_ID ...] [--all] [--review interactive|auto|defer|file] [--review-file F]
    [--simulate-crm none|flaky|down|slow] [--simulate-market none|down]
    [--as-of YYYY-MM-DD] [--llm] [--json] [-v]
python3 -m wealth_advisor [--memory ...] history CLIENT_ID [--limit N]
```

## 12. Sample input and output

Input (`data/`, generated by `scripts/generate_sample_data.py` with fixed seeds):

| Client | Profile | What it exercises |
|---|---|---|
| C001 Andi Pratama | moderate, high net worth | Large transfer to a new Hong Kong counterparty, three transfers just under IDR 500M in three days, e-wallet burst, crypto above policy, 46% in one stock, BTC down 22%, KYC expiring, no contact for 8 months |
| C002 Siti Rahayu | conservative | Spending above income for 3 months, no holdings field, open complaint, KYC expiring |
| C003 Budi Santoso | aggressive | Messy file: no income field, duplicate id, missing amounts, impossible date, holding without symbol, unknown symbol, not in CRM |
| C004 Dewi Lestari | moderate | Healthy control case: no findings expected |
| C005 | | Truncated JSON: run fails cleanly |

Output (`samples/`):

| File | Shows |
|---|---|
| `C001_reviewed.{md,json}` | Full report with file-based review: approvals, a rejection and a modified action |
| `C001_crm_market_down.{md,json}` | CRM and market feed down, deferred review → `pending_review` |
| `console_C001_degraded.log`, `run_log_C001_degraded.jsonl` | Console and JSONL logs for that run, including retries and fallback |
| `C002.md`, `C003_messy_data.md`, `C004_clean.md`, `C005_corrupt_file.md` | The other cases |
| `review_decisions.json` | Example decisions file |

Excerpt from `samples/C001_reviewed.md`:

```
- Risk score: **90/100 (critical)**

| P | Action                                                                           | Status   |
|---|----------------------------------------------------------------------------------|----------|
| 1 | Visit client in person to verify the HK transfer before any further wire         | modified |
| 1 | Escalate transactions C001-0104, C001-0105, C001-0106 to compliance for ...       | approved |
| 2 | Propose trimming BBCA toward the single-position limit                           | rejected |
| 2 | Propose reducing crypto to within the 5% policy limit, or re-assess ...          | approved |

| critical | POSSIBLE_STRUCTURING | 3 transfers between 2026-09-20 and 2026-09-22 each sit within 10% below
                                  IDR 500,000,000 (total IDR 1,437,000,000). Escalate to compliance. |
```

## 13. Project layout

```
wealth-advisor/
├── wealth_advisor/
│   ├── __main__.py          CLI
│   ├── app.py               composition root
│   ├── config.py            thresholds, risk policy, settings
│   ├── models.py            Transaction, Holding, ClientProfile, Finding, Recommendation
│   ├── errors.py            typed errors (retryable vs not)
│   ├── observability.py     JSONL + console logging, run/agent context
│   ├── hitl.py              reviewers and decision application
│   ├── report.py            JSON report and Markdown rendering
│   ├── agents/              base, orchestrator, data_fetcher, analyzer, advisor
│   ├── tools/               base (Tool, Registry, Executor), client_data, crm, market,
│   │                        analysis, narrator, memory_tools
│   ├── analytics/           metrics.py, detectors.py (pure functions)
│   └── memory/              short_term.py, long_term.py (interface + SQLite),
│                            scylla.py, migrations/001_init.cql
├── data/                    mock clients, CRM, market quotes
├── samples/                 sample reports and logs
├── scripts/generate_sample_data.py
├── tests/                   unit, orchestration, LLM-path and Scylla integration tests
├── docker-compose.yml       single-node ScyllaDB
└── pyproject.toml
```
