# p2p-process-insights

A process review of a real purchase-to-pay operation: where purchase orders and
invoices wait, which company policies are broken and how often, and what an
operations team should fix first. Built on the public **BPI Challenge 2019**
event log, which covers 251,734 purchase-order lines and 1,595,923 logged steps
from a multinational coatings company.

**The short version.** Most invoices that take longer than 60 days to pay are probably not a process failure: 68% of them sit in one spend area (Packaging), which pays at a median of 69 days — the signature of longer agreed terms. The delays the company does control are upstream: invoices wait a median 9.6 days to be booked, and goods receipts are posted late enough to hold payments up.

**Read this before the numbers.** The log has no payment terms, so "paid after
60 days" is a yardstick (the EU Late Payment Directive's default
ceiling), not a breach of contract. Finding 1 shows why that matters. The rest
of the limitations are [listed below](#limitations) rather than in a footnote.

Everything here is written by `make_report.py` from `outputs/headline_numbers.json`,
which a single seeded run of `run_all.py` produces. No number is typed by hand.

| Deliverable | File |
|---|---|
| One-page recommendation memo | [`docs/recommendation_memo.pdf`](docs/recommendation_memo.pdf) |
| Seven-slide deck | [`slides/p2p_process_insights.pptx`](slides/p2p_process_insights.pptx) |
| Interactive dashboard | [`dashboard/index.html`](dashboard/index.html) |
| The analysis, as SQL | [`sql/`](sql/) |

---

## Headline numbers

| | |
|---|---|
| PO lines / logged steps / vendors | 251,734 / 1,595,923 / 1,975 |
| Value ordered | €996.8M |
| Process variants (distinct paths) | 7,438. The top one covers 20%; 38 paths cover 80% |
| Median days, invoice booked to paid | 42.0 |
| Paid after 60 days | 41.6% of 211,355 lines with a known outcome |
| Share of those in Packaging | 68.1% (it has 43.7% of all lines) |
| Vendors behind 80% of slow-paid lines | 57 (3.1% of vendors) |
| Median days, vendor invoice date to booked | 9.6 (42% over 14 days) |
| Invoice booking done by hand | 95.9%, by 29 people |
| Extra slow-payment rate when invoice beats goods receipt (within vendor) | +7.7 pp |
| POs raised after the vendor invoiced | 3,621 lines, €49.4M |
| Manual workload lift in week 4 of the month | 25% (month-end weeks: 6%) |
| 4-week workload forecast error (best method) | 11.2% |

## Findings

### 1. Check the terms before chasing “late” payments

42% of PO lines are paid after 60 days, but 57 vendors (3% of them) account for 80% of those lines, and Packaging alone accounts for 68%.

**Action.** Pull the contract terms for the 57 vendors. Where terms are 60 days or less, this is real late payment; where they are longer, re-baseline the KPI so it stops flagging agreed behaviour.

### 2. Invoices wait to be booked

Median 9.6 days from the vendor's invoice date to booking; 42% wait over two weeks. Booking is 96% manual, done by 29 people (219,288 bookings), although batch jobs already book 9,472.

**Action.** Pilot e-invoicing / OCR capture with the highest-volume vendors and track invoice-date-to-booking weekly, with a target median of 3 days.

### 3. Late goods receipts hold payments

When the invoice arrives before the goods receipt is posted, the line is paid after 60 days 7.7 percentage points more often — comparing within the same vendor (589 vendors). Blocked invoices spend 33% of their wait before the block is lifted (median 6.0 days).

**Action.** Set a 2-working-day posting target for goods receipts and report it by site. GR posting is 73% manual across 246 people; batch already posts 85,034, so extend automatic receipt to the vendors that support it.

### 4. POs raised after the invoice

3,621 lines (1.4%, €49.4M) were ordered after the vendor had already invoiced — a median 12 days later. Trading & End Products produces 40% of them; in Real Estate, 52% of lines are retroactive.

**Action.** Enforce “no PO, no pay” in Trading & End Products and Real Estate first, and brief the buyers behind the 83 vendors that make up 80% of cases.

### 5. Staff for week 4 of the month, not the average week

Manual work in the fourth week of the month (days 22-28) runs 25% above the other weeks. A forecast that knows this misses by 11% four weeks ahead, against 17% for a flat 4-week average (6 backtest folds). The obvious suspect, month-end close, does not explain it: weeks holding a month end are only 6% busier.

**Action.** Plan the accounts-payable and receiving rota from the week-of-month forecast. Find out what lands in week 4 (a payment-run or reporting cut-off is the likely candidate) before moving work out of it.

## Checked and ruled out

- Rework does not slow payment. Lines with changes or cancellations are paid after 60 days 41.7% of the time, against 41.6% without — no difference worth acting on.
- A machine-learning model adds little to a simple rule. Ranking new invoices by the vendor's past record scores AUC 0.825; gradient boosting scores 0.842. Among the riskiest 10% of invoices, the model is right 100% of the time and the rule 97% — use the rule.

## How the analysis works

**Unit of analysis.** One case is one purchase-order line. The log follows each
line through up to 42 kinds of step: create the PO, receive goods, book the invoice,
clear (pay) it, plus changes, cancellations and payment blocks. Each line belongs
to one of four matching flows:

| Flow | Rule | Share of lines |
|---|---|---|
| 3-way match, invoice before GR | Invoice may be booked before the goods arrive, but is blocked until they do | 87.8% |
| 3-way match, invoice after GR | Goods receipt must be posted before the invoice is booked | 6.0% |
| Consignment | Goods are invoiced outside the PO, so there is no PO-level invoice | 5.8% |
| 2-way match | Invoice is matched to the PO only; no goods receipt needed | 0.4% |

**Compliance checks.** These run as SQL over each line's milestone dates ([`sql/02_case_facts.sql`](sql/02_case_facts.sql)):

| Check | PO lines | Share | PO value |
|---|---|---|---|
| Vendor invoiced before the PO existed | 3,621 | 1.44% | €49.4M |
| Paid before goods were received (3-way match) | 655 | 0.26% | €1.8M |
| Invoice booked before goods receipt (invoice-after-GR flow) | 0 | 0.00% | €0 |
| PO-level invoice on a consignment line | 0 | 0.00% | €0 |

**Payment timing** ([`sql/03_invoices.sql`](sql/03_invoices.sql)). The log never says which payment
settles which invoice. So each line is measured from its first booked invoice to
its first clearance. Invoices booked less than 60 days before the log ends
are excluded *whether or not they were paid*. Keeping only the fast payers among
them would bias the rate down; a test pins this. 24 lines where the clearance
predates the invoice are excluded as unpairable.

How much "paid after N days" depends on N:

| Threshold | Share of lines paid after it |
|---|---|
| 30 days | 67.6% |
| 45 days | 53.1% |
| 60 days | 41.6% |
| 90 days | 24.7% |

**Where the time goes** ([`sql/04_transitions.sql`](sql/04_transitions.sql)). Every step-to-next-step hand-off,
ranked by *total* days waited rather than the median, so a moderate delay that
happens 100,000 times outranks a long one that happens twice.

**Slow-payment classifier** ([`src/p2p/model.py`](src/p2p/model.py)). Each line is scored at the moment its
invoice is booked, using only what is known then. Rework counts only changes
made *before* the invoice, and vendor history is learned from the training
period only. The split is out-of-time: trained on invoices booked before
2018-09-01 (132,484 lines), tested on the rest (78,871 lines).

| Ranking | AUC | Precision in riskiest 10% | Capture in riskiest 10% |
|---|---|---|---|
| Rule: vendor's past record | 0.825 | 96.6% | 20.2% |
| Logistic regression | 0.821 | 98.9% | 20.7% |
| Gradient boosting | 0.842 | 99.9% | 20.9% |
| Random order | 0.502 | 48.4% | 10.1% |

Capture tops out at 20.9%. With 48% of test lines slow-paid,
reviewing 10% of them cannot catch more, so precision is the number to compare.

**Workload forecast** ([`src/p2p/workload.py`](src/p2p/workload.py)). Weekly manual steps, backtested with
6 rolling origins, 4 weeks ahead. The log only contains POs *created in 2018*, which
under-counts early 2018 and collapses in 2019. So the series is cut to the steady
window (2018-02-12 to 2018-12-17), chosen from the data.

| Method | Mean absolute % error |
|---|---|
| moving average week of month | 11.2% |
| moving average 4w | 16.6% |
| holt damped | 16.8% |
| moving average month end | 16.9% |
| naive last week | 23.0% |

## Limitations

- The log has no payment terms. “Paid after 60 days” uses the EU Late Payment Directive's default ceiling as a yardstick, not as the company's contract; the sensitivity table shows the rate at 30, 45 and 90 days.
- The log does not link a payment to the invoice it settles, so each PO line is measured on its first invoice to its first clearance. Lines with many invoices (framework orders) are represented by that first one.
- Values are line values (“cumulative net worth”), not invoice amounts; where a line has several invoices, its value is split evenly between them.
- The week-of-month pattern was spotted by looking at the whole weekly series. The forecast learns its multipliers only from weeks before each origin, but the choice to try it was informed by the data, so its backtest error is somewhat optimistic.
- One company, one year (POs created in 2018), anonymised vendors and users. Findings describe this process, not purchase-to-pay in general.
- The statutory late-interest figure (€11.5M) is an upper bound that assumes 60-day terms everywhere. Given finding 1, it is not quoted as a cost.

## Reproduce

```bash
make install
make data      # downloads the 700 MB event log once and checks its MD5
make run       # ~1 min on a laptop; writes outputs/
make docs      # memo, deck, dashboard, this README
make test
```

Run on Python 3.13.2, Windows-11-10.0.26200-SP0, seed 20261014. `requirements.txt` pins the
versions these numbers came from.

## Repository map

| Path | What it is |
|---|---|
| `sql/` | The analysis itself: events, case facts and compliance, invoices, hand-offs, variants, vendor Pareto, automation, weekly workload |
| `src/p2p/ingest.py` | Downloads the XES log, checks its MD5 and streams it into Parquet |
| `src/p2p/process.py` | Runs the SQL and summarises it |
| `src/p2p/model.py` | Slow-payment classifier, out-of-time |
| `src/p2p/workload.py` | Weekly workload forecast and backtest |
| `run_all.py` | One command; writes `outputs/headline_numbers.json` |
| `make_report.py` | Renders memo, deck, dashboard and README from that JSON |
| `tests/` | Hand-built event logs with known answers, one per rule |

**Data.** van Dongen, B. (2019). *BPI Challenge 2019*. 4TU.ResearchData.
https://doi.org/10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1
