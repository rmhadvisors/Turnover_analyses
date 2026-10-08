# GP/NP from Tally, and client profiles for alert relevance

Status: **implemented on 26-Sep-2026** with the recommended options (A1 yes, A2 Tally's NP,
A3 manual > P&L import > Tally with a `profit_source` column, A4 5%; B1-B4 as recommended).
One addition: GP/NP are not derived when the files hold 6 months of trading or fewer (the same
rule as the part-year warning). The design below is kept as the reference.

---

## A. Gross and Net Profit from the Tally JSON export (Step 4)

### Why GP/NP are empty today
The importer reads only the ledgers under *Sales Accounts*, *Purchase Accounts* and
*Duties & Taxes*, so it can fill turnover and purchases but nothing else. The P&L figures are
available in the same two files.

### What the Tally data provides
| Needed for P&L | Where it is in the export | Present in the 8 client-years? |
|---|---|---|
| Which ledgers are trading (GP) vs P&L | Master: every Group has Tally's own flags `affectsgrossprofit` and `isrevenue` | Yes, all 8 |
| Sales, purchases, direct / indirect incomes and expenses | Transactions: ledger lines of all posted vouchers (incl. journals) | Yes |
| Opening stock | Master: *Stock-in-hand* ledger `openingbalance` | 3 of 4 clients (not Jigeesha) |
| Closing stock | Master: *Stock-in-hand* ledger `ledgerclosingvalues` dated 31-Mar | 3 of 4 clients; each year's closing = next year's opening |

### Proposed calculation (Tally's own P&L logic)
- **GP** = Sales + Direct incomes - Purchases - Direct expenses + Closing stock - Opening stock
  (all ledgers under groups flagged `affectsgrossprofit`).
- **NP** = GP + Indirect incomes - Indirect expenses (all other groups flagged `isrevenue`).
  This matches the Net Profit on the client's Tally P&L. Custom revenue groups such as
  Hotel Kinara's "Profit & Loss App" (partners' remuneration, interest on capital, tax
  provision) are included, as Tally does; the report also shows NP *before* those items.
- Computed in the same streaming pass as today's import (no extra memory).

### Prototype results (complete exports; lakhs)
Sales and purchases from this route equal the imported turnover / purchases to the rupee for all
8 client-years, which confirms the ledger classification.

| Client | FY | GP | NP (Tally) | NP before appropriation | Notes |
|---|---|---|---|---|---|
| Advance Power | 2024-25 | 16.95 | 13.33 | 13.33 | |
| Advance Power | 2025-26 | 68.76 | 61.39 | 61.39 | Stock 58.60 -> 20.46 |
| Hotel Kinara | 2024-25 | 121.29 | 9.60 | 47.11 | 3.75 L on a ledger missing from the Master |
| Hotel Kinara | 2025-26 | 123.05 | 6.60 | 43.63 | |
| Jigeesha Auto | 2024-25 | 59.13 | 5.19 | 5.19 | **No stock ledger; 17.64 L on ledgers missing from the Master** |
| Jigeesha Auto | 2025-26 | 83.81 | 23.78 | 23.78 | No stock ledger |
| Shirke Flex | 2024-25 | 43.00 | 10.11 | 10.11 | |
| Shirke Flex | 2025-26 | 44.59 | 10.69 | 10.69 | |

These are **not yet verified** against the clients' finalised P&L statements. Year-end entries made
outside Tally (e.g. depreciation in the final accounts) would not be in the export.

### Safeguards proposed
1. **Precedence:** manual entry > P&L (Excel) import > derived from Tally. A derived value never
   overwrites a manual one. Needs a small schema change (a `profit_source` column; DB backed up first).
2. **Only complete years:** derive GP/NP only when the closing-stock value is dated at the FY end.
   In-progress years keep GP/NP empty (a YTD GP without closing stock is misleading).
3. **No stock ledger** (Jigeesha): store GP with a warning "stock not maintained in Tally".
4. **Ledgers missing from the Master:** if their total exceeds 5% of |NP|, do not store NP
   automatically; the import preview shows the gap and you choose. (Jigeesha 2024-25 would be
   held back: 17.64 L unknown vs 5.19 L NP.)
5. The import preview shows GP/NP per year before you confirm.

### Effect on alerts once GP/NP exist
GP/NP band alerts already exist in the engine and would start firing. With the numbers above:
- Critical: Advance Power GP +306%, NP +360%; Jigeesha GP +42% (NP 2024-25 held back per rule 4);
  Hotel Kinara NP -31%.
- Warning: Shirke Flex NP +5.7%.
- Normal: Hotel Kinara GP +1.5%, Shirke Flex GP +3.7%.

### Decisions needed
- A1. Approve deriving GP/NP from Tally with the calculation above?
- A2. NP definition: Tally's (after appropriation groups, recommended) or before them?
- A3. Approve the precedence rule and the `profit_source` column (schema change, backup first)?
- A4. The 5%-of-NP threshold for missing ledgers: accept, or another value?

---

## B. Client profile: which limits apply to which client (Step 5)

### Problem
All 13 statutory limits are checked for every client. Of the 93 open alerts today, most are
limits that cannot apply: all 4 clients are already GST-registered in Maharashtra (so the four
GST *registration* limits are moot, and the special-category ones never apply), none is a
professional (44ADA), and presumptive 44AD matters only if the client opted for it.

### Profile fields (per client)
| Field | Values | Filled how |
|---|---|---|
| GSTIN | text | **Automatic** from the Transactions export (`cmpgstin`); editable |
| State | e.g. Maharashtra | Automatic from GSTIN digits 1-2 (27 = Maharashtra) |
| Special-category state | yes / no | Automatic from the state (editable list) |
| GST registered | yes / no | Automatic: yes if a GSTIN exists |
| Entity type | individual / HUF / firm / LLP / company / other | Suggested from PAN (GSTIN chars 3-12): P = individual, F = firm or LLP, C = company, H = HUF; you confirm |
| Nature | business / profession / both | You choose |
| Supplies | goods / services / both | You choose |
| Presumptive scheme opted | none / 44AD / 44ADA | You choose |
| Cash receipts & payments within 5% | yes / no / unknown | You choose |

Suggested from the real data: all four are GSTIN state 27 (Maharashtra); Advance Power, Jigeesha
and Shirke Flex have an individual PAN ("P"), Hotel Kinara a firm PAN ("F").

### Which limit applies when (a rule stored on each limit, editable in Settings)
| # | Limit | Applies when |
|---|---|---|
| 1 | GST registration - goods (regular) | not GST-registered; supplies goods/both; not special-category |
| 2 | GST registration - services (regular) | not GST-registered; supplies services/both; not special-category |
| 3-4 | GST registration - special-category | not GST-registered; special-category state |
| 5 | Tax audit 44AB (Rs 1 cr) | business; cash within 5% is not "yes" |
| 6 | Tax audit 44AB (Rs 10 cr) | business; cash within 5% = yes |
| 7 | 44AD (Rs 2 cr) | presumptive = 44AD; cash within 5% is not "yes"; entity not LLP/company |
| 8 | 44AD (Rs 3 cr) | presumptive = 44AD; cash within 5% = yes |
| 9 | 44ADA (Rs 50 L) | presumptive = 44ADA; cash within 5% is not "yes" |
| 10 | 44ADA (Rs 75 L) | presumptive = 44ADA; cash within 5% = yes |
| 11 | E-invoicing | GST-registered |
| 12 | TDS 194Q purchases | previous-year turnover > Rs 10 cr; **checked per seller** (see below) |
| 13 | 194Q buyer turnover | business (information only; it is the precondition for #12) |
| 14 | LLP audit (Rs 50 L trigger) | entity = LLP |
| 15-19 | TDS 194J / 194C / 194H / 194I(a) / 194I(b) (Rs 50 L trigger) | firm, LLP or company; or individual / HUF whose accounts were audited u/s 44AB in the previous year |

Limits 14-19 were added on request on 26-Sep-2026 as Rs 50 lakh sales-turnover review triggers.
Today they fire for all 4 clients in both years (48 alerts). With the profile, the LLP audit alert
would apply to none of them (no client is an LLP), and the TDS ones only where the entity rule holds.
A later refinement could check the real per-payee TDS thresholds by mapping each client's expense
ledgers to a section (Tally's own TDS tags exist on only a few ledgers, so the mapping would be
suggested from ledger names and confirmed by you).

A field left unknown keeps today's behaviour (the limit applies), and the client page shows
"profile incomplete", so nothing silently disappears.

### Related correction: 194Q is per seller
Today limit 12 compares **total** purchases with Rs 50 lakh. The law applies per seller, and
only to buyers with turnover above Rs 10 crore in the previous year. From the stored vouchers:
Shirke Flex's largest seller is 6.01 L (2 open alerts today would disappear); Hotel Kinara's
largest "seller" is the Cash ledger; only Jigeesha (turnover about Rs 29 cr, HPCL 2,811 L)
qualifies. Proposal: a per-seller purchase metric that ignores cash / non-party ledgers.

### Existing alerts that become not applicable
Keep them as history, but mark them acknowledged by "System - not applicable to client profile"
when the profile is saved (so the badge count drops and the audit trail stays). Alternative:
leave them open for manual review. Your choice.

### Estimated effect (profile as suggested; presumptive not opted; cash within 5% unknown)
| Client | Open alerts now | After profile | After profile + 194Q per seller |
|---|---|---|---|
| Advance Power | 26 | 9 | 7 |
| Hotel Kinara | 24 | 8 | 6 |
| Jigeesha Auto | 26 | 8 | 8 |
| Shirke Flex | 17 | 5 | 3 |
| **Total** | **93** | **30** | **24** |

### Implementation outline (after approval)
- New `client_profiles` table (1:1 with clients) and an `applies_when` rule on each limit;
  DB backed up before the migration. Seeded rules as in the table above.
- `comparison_service._limit_results` skips limits whose rule does not match the profile;
  alert evaluation is otherwise unchanged.
- Clients -> Manage client -> **Profile** form, pre-filled from the GSTIN after an import.
- Settings -> each limit shows its "applies when" rule (editable).
- Tests for every rule and for the unknown-field fallback.

### Decisions needed
- B1. Approve the profile fields and the applicability table?
- B2. Unknown field -> limit still applies (recommended)?
- B3. Existing non-applicable alerts: auto-acknowledge as "System - not applicable" (recommended) or leave open?
- B4. Approve changing 194Q to per-seller with the Rs 10 crore precondition?
