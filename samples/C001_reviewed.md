# Advisory report: Andi Pratama

- Run: `20261014T132935-8e1126`  |  Status: **completed**  |  As of: 2026-09-30
- Risk score: **90/100 (critical)**
- Trend: First analysis for this client.

## Briefing

Andi Pratama (moderate profile) has a risk score of 90/100 with 2 critical and 4 high-severity findings. First analysis for this client. Most urgent: Large outflow: new counterparty, cross-border (HK); Transfers clustered just below reporting threshold; Unusually large shopping payment. First action: Call client to verify purpose and beneficiary of large outflow(s) C001-0103.

_source: template_

## Recommendations

| P | Action | Status | Approval |
|---|---|---|---|
| 1 | Visit client in person to verify the HK transfer before any further wire | modified | yes |
| 1 | Escalate transactions C001-0104, C001-0105, C001-0106 to compliance for suspicious-transaction review; hold further large transfers until cleared | approved | yes |
| 2 | Propose trimming BBCA toward the single-position limit | rejected | yes |
| 2 | Propose reducing crypto to within the 5% policy limit, or re-assess the client's risk profile | approved | yes |
| 2 | Propose rebalancing equity/crypto from 68% to at most 60%, or re-assess the risk profile | approved | yes |
| 2 | Verify transaction burst C001-0108, C001-0109, C001-0110, C001-0111, C001-0112, C001-0113 … with client (possible account compromise) | approved | no |
| 3 | Confirm 1 unusual payment(s) with client: C001-0116 | approved | no |
| 3 | Review investment thesis for BTC | approved | no |
| 3 | Schedule KYC renewal before 2026-10-20 | approved | no |
| 3 | Discuss 2026-09 spending increase with client | approved | no |
| 4 | Schedule a periodic portfolio review meeting | approved | no |

## Findings

| Severity | Code | Finding | Detail |
|---|---|---|---|
| critical | LARGE_OUTFLOW | Large outflow: new counterparty, cross-border (HK) | IDR 420,000,000 to Lucky Star Trading Ltd on 2026-09-12 equals 1.7x monthly income. |
| critical | POSSIBLE_STRUCTURING | Transfers clustered just below reporting threshold | 3 transfers between 2026-09-20 and 2026-09-22 each sit within 10% below IDR 500,000,000 (total IDR 1,437,000,000). Escalate to compliance. |
| high | AMOUNT_OUTLIER | Unusually large shopping payment | IDR 38,000,000 to Plaza Indonesia Boutique on 2026-09-15 is 11.6 robust z-scores above the typical IDR 3,168,000. |
| high | CONCENTRATION | BBCA is 46% of the portfolio | Single-position limit is 30%. |
| high | CRYPTO_OVEREXPOSURE | Crypto 13% exceeds moderate limit 5% | Crypto allocation is outside policy for this risk profile. |
| high | RISK_PROFILE_MISMATCH | Risky assets 68% exceed moderate limit 60% | Equity and crypto exposure is above what the client's risk profile allows. |
| medium | DRAWDOWN | BTC down 22% in 30 days | Position weight 13%; unrealized P/L -243,000,000. |
| medium | HIGH_VELOCITY | Burst of outgoing transactions | 8 outflows on 2026-09-27 (limit 6), total IDR 16,000,000. |
| medium | KYC_EXPIRING | KYC expires in 20 days | KYC expiry 2026-10-20. |
| medium | SPENDING_SPIKE | Spending in 2026-09 up 60% vs trailing average | IDR 133,112,000 spent in 2026-09 against an average of IDR 83,257,400 over 5 prior months. |
| low | STALE_RELATIONSHIP | No advisor contact for 239 days | Last contact 2026-02-03. |

## Key metrics

- Avg monthly spending: 91,566,500
- Avg monthly inflow: 500,000,000
- Savings rate: 82%
- Emergency fund: 9.83 months
- Portfolio: 8,380,500,000 (equity 55%, bond 22%, crypto 13%, cash 11%)
- Sources: client_data=live, crm=live, market=live

## Review

- Outcome: reviewed (reviewer: file)
- REC-POSSIBLE_STRUCTURING: approve (STR draft sent to compliance)
- REC-LARGE_OUTFLOW: modify (phone verification not enough for this amount)
- REC-CONCENTRATION: reject (BBCA position is a long-term dividend holding agreed with client)
- REC-CRYPTO_OVEREXPOSURE: approve
- REC-RISK_PROFILE_MISMATCH: approve
- REC-HIGH_VELOCITY: approve
- REC-AMOUNT_OUTLIER: approve
- REC-DRAWDOWN: approve
- REC-KYC_EXPIRING: approve
- REC-SPENDING_SPIKE: approve
- REC-STALE_RELATIONSHIP: approve

## Execution

- fetch_client_data -> data_fetcher: ok
- analyze -> analyzer: ok
- advise -> advisor: ok
- human_review -> file: ok
- persist -> orchestrator: ok
