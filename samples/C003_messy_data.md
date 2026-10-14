# Advisory report: Budi Santoso

- Run: `20261014T132940-694b46`  |  Status: **completed_with_warnings**  |  As of: 2026-09-30
- Risk score: **24/100 (low)**
- Trend: First analysis for this client.

## Briefing

Budi Santoso (aggressive profile) has a risk score of 24/100 with 0 critical and 0 high-severity findings. First analysis for this client. First action: Propose trimming ANTM toward the single-position limit. Data gaps: transaction amount missing or non-numeric; dropped; transaction date '2026-13-45' unparseable; dropped; transaction amount missing or non-numeric; dropped.

_source: template_

## Recommendations

| P | Action | Status | Approval |
|---|---|---|---|
| 2 | Propose trimming ANTM toward the single-position limit | approved | yes |
| 3 | Review investment thesis for GOTO | approved | no |

## Findings

| Severity | Code | Finding | Detail |
|---|---|---|---|
| medium | CONCENTRATION | ANTM is 30% of the portfolio | Single-position limit is 30%. |
| medium | DRAWDOWN | GOTO down 18% in 30 days | Position weight 15%; unrealized P/L -62,000,000. |
| low | STALE_PRICES | Portfolio valued with non-live prices | PRIVATE-FUND-X priced from fallback sources; allocation figures are approximate. |

## Key metrics

- Avg monthly spending: 39,858,500
- Avg monthly inflow: 60,000,000
- Savings rate: 34%
- Emergency fund: 5.27 months
- Portfolio: 841,500,000 (equity 75%, cash 25%)

## Data quality

- [client_data] transaction amount missing or non-numeric; dropped (`C003-0011`)
- [client_data] transaction date '2026-13-45' unparseable; dropped (`C003-0021`)
- [client_data] transaction amount missing or non-numeric; dropped (`C003-0041`)
- [client_data] duplicate transaction id; dropped (`C003-0031`)
- [client_data] holding without symbol; dropped
- [client_data] monthly_income missing; inferred 60,000,000 from income transactions
- [crm] CRM data missing (CRM has no record for C003 (HTTP 404)); relationship checks skipped
- [market] no quote for PRIVATE-FUND-X; valued at last known price or cost
- Sources: client_data=live, crm=not_found, market=live

## Review

- Outcome: reviewed (reviewer: auto)
- REC-CONCENTRATION: approve (auto-approved (no human reviewer configured))
- REC-DRAWDOWN: approve (auto-approved (no human reviewer configured))

## Execution

- fetch_client_data -> data_fetcher: ok (degraded)
- analyze -> analyzer: ok
- advise -> advisor: ok
- human_review -> auto: ok
- persist -> orchestrator: ok
