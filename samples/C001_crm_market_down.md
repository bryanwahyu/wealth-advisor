# Advisory report: Andi Pratama

- Run: `20261014T132946-d03abd`  |  Status: **pending_review**  |  As of: 2026-09-30
- Risk score: **85/100 (critical)**
- Trend: First analysis for this client.

## Briefing

Andi Pratama (moderate profile) has a risk score of 85/100 with 2 critical and 3 high-severity findings. First analysis for this client. Most urgent: Large outflow: new counterparty, cross-border (HK); Transfers clustered just below reporting threshold; Unusually large shopping payment. First action: Call client to verify purpose and beneficiary of large outflow(s) C001-0103. Data gaps: CRM data missing (CRM returned HTTP 503 Service Unavailable); relationship checks skipped; live prices unavailable (market data provider unreachable); using client-file prices.

_source: template_

## Recommendations

| P | Action | Status | Approval |
|---|---|---|---|
| 1 | Call client to verify purpose and beneficiary of large outflow(s) C001-0103 | pending_review | yes |
| 1 | Escalate transactions C001-0104, C001-0105, C001-0106 to compliance for suspicious-transaction review; hold further large transfers until cleared | pending_review | yes |
| 2 | Propose trimming BBCA toward the single-position limit | pending_review | yes |
| 2 | Propose reducing crypto to within the 5% policy limit, or re-assess the client's risk profile | pending_review | yes |
| 2 | Propose rebalancing equity/crypto from 69% to at most 60%, or re-assess the risk profile | pending_review | yes |
| 2 | Verify transaction burst C001-0108, C001-0109, C001-0110, C001-0111, C001-0112, C001-0113 … with client (possible account compromise) | pending_review | no |
| 3 | Confirm 1 unusual payment(s) with client: C001-0116 | pending_review | no |
| 3 | Discuss 2026-09 spending increase with client | pending_review | no |

## Findings

| Severity | Code | Finding | Detail |
|---|---|---|---|
| critical | LARGE_OUTFLOW | Large outflow: new counterparty, cross-border (HK) | IDR 420,000,000 to Lucky Star Trading Ltd on 2026-09-12 equals 1.7x monthly income. |
| critical | POSSIBLE_STRUCTURING | Transfers clustered just below reporting threshold | 3 transfers between 2026-09-20 and 2026-09-22 each sit within 10% below IDR 500,000,000 (total IDR 1,437,000,000). Escalate to compliance. |
| high | AMOUNT_OUTLIER | Unusually large shopping payment | IDR 38,000,000 to Plaza Indonesia Boutique on 2026-09-15 is 11.6 robust z-scores above the typical IDR 3,168,000. |
| high | CRYPTO_OVEREXPOSURE | Crypto 16% exceeds moderate limit 5% | Crypto allocation is outside policy for this risk profile. |
| high | RISK_PROFILE_MISMATCH | Risky assets 69% exceed moderate limit 60% | Equity and crypto exposure is above what the client's risk profile allows. |
| medium | CONCENTRATION | BBCA is 45% of the portfolio | Single-position limit is 30%. |
| medium | HIGH_VELOCITY | Burst of outgoing transactions | 8 outflows on 2026-09-27 (limit 6), total IDR 16,000,000. |
| medium | SPENDING_SPIKE | Spending in 2026-09 up 60% vs trailing average | IDR 133,112,000 spent in 2026-09 against an average of IDR 83,257,400 over 5 prior months. |
| low | STALE_PRICES | Portfolio valued with non-live prices | BBCA, TLKM, BTC, RD-SUCORINVEST-BOND priced from fallback sources; allocation figures are approximate. |

## Key metrics

- Avg monthly spending: 91,566,500
- Avg monthly inflow: 500,000,000
- Savings rate: 82%
- Emergency fund: 9.83 months
- Portfolio: 8,779,500,000 (equity 54%, bond 21%, crypto 16%, cash 10%)

## Data quality

- [crm] CRM data missing (CRM returned HTTP 503 Service Unavailable); relationship checks skipped
- [market] live prices unavailable (market data provider unreachable); using client-file prices
- Sources: client_data=live, crm=unavailable, market=fallback

## Review

- Outcome: deferred (reviewer: defer)

## Execution

- fetch_client_data -> data_fetcher: ok (degraded)
- analyze -> analyzer: ok
- advise -> advisor: ok
- human_review -> defer: ok
- persist -> orchestrator: ok
