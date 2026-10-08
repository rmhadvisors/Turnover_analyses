"""The detail panel of one party + section (opened from a TDS alert or the report):
party, section and why it applies, the threshold test, the calculation step by step,
every voucher, and the compliance consequences with figures."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.repositories.tds_seed import PAYEE, WHO_DEDUCTS
from app.services import tds_compliance as tc
from app.services import tds_engine as te
from app.services.tds_engine import PAYEE_LABELS, pct_text
from app.services.tds_service import ClientTds, PartySection, base_not_covered, cross_section_cause
from app.utils.indian_format import format_indian_commas as inr

STATUS_LABELS = {status.value: label for status, (_, label) in te.STATUS_DISPLAY.items()}
STATUS_EMOJI = {status.value: emoji for status, (emoji, _) in te.STATUS_DISPLAY.items()}


def m(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def rupees(value: Decimal) -> str:
    return f"{inr(value)}"


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def rate_text(row: PartySection) -> str:
    """The rate(s) applied, e.g. '2%' or '5% on ₹10,000.00 + 2% on ₹10,000.00'."""
    used = row.evaluation.rates_used
    if not used:
        return pct_text(row.evaluation.rate.rate)
    if len(used) == 1:
        return pct_text(next(iter(used)))
    return " + ".join(
        f"{pct_text(rate)} on {rupees(amount)}" for rate, amount in sorted(used.items())
    )


def _base_why(ev: te.PartyEvaluation) -> str:
    rule = ev.rule
    if not ev.crossed:
        return "Threshold not crossed - no TDS yet"
    if rule.base == te.EXCESS:
        return (
            f"Only the amount above {rupees(rule.aggregate_threshold)} (s.194Q): "
            f"{rupees(ev.aggregate)} - {rupees(rule.aggregate_threshold)}"
        )
    if ev.crossed_by == te.CrossedBy.SINGLE:
        return (
            f"Aggregate not above {rupees(rule.aggregate_threshold)}: only the payment(s) above "
            f"{rupees(rule.single_threshold)} are liable (s.194C(5))"
        )
    if ev.crossed_by == te.CrossedBy.NO_THRESHOLD:
        return "No threshold: the whole amount"
    if ev.crossed_by == te.CrossedBy.MONTHLY:
        return (
            f"s.194-IB: rent exceeded {rupees(rule.single_threshold)} in a month, so TDS is due "
            f"once, in the last month ({ev.deduct_on:%b-%Y}), on the whole rent of the year"
        )
    return "Threshold crossed: the whole FY aggregate, earlier payments included"


def calculation_steps(row: PartySection) -> list[dict]:
    """Base -> rate (and why) -> TDS payable -> deducted -> shortfall."""
    ev = row.evaluation
    rate_note = ev.rate.reason + (f". {ev.nil_reason}" if ev.nil_reason else "")
    steps = [
        {"step": "Aggregate in the FY (excl. GST)", "value": m(ev.aggregate),
         "note": f"{len(ev.lines)} voucher(s)"},
        {"step": "TDS base", "value": m(ev.base), "note": _base_why(ev)},
        {"step": "Rate applied", "value": rate_text(row), "note": rate_note},
        {"step": "TDS payable", "value": m(ev.tds), "note": "Rounded to the nearest rupee"},
        {"step": "TDS deducted in the party's vouchers", "value": m(row.deducted_in_books),
         "note": f"{len(row.deductions)} entry(ies) in TDS ledgers mapped to {row.section_key}"},
    ]  # fmt: skip
    if row.deducted_attributed:
        steps.append(
            {
                "step": "TDS booked without a party, attributed here",
                "value": m(row.deducted_attributed),
                "note": "Lump-sum TDS entries of this section with no party; this is the only "
                "party over the section's threshold, so it can only be its TDS (counted). "
                "Verify against the challans / Form 26Q.",
            }
        )
    if row.deducted_allocated:
        steps.append(
            {
                "step": "Party-less TDS that may relate to this party (NOT counted)",
                "value": m(row.deducted_allocated),
                "note": "A pro-rata share of lump-sum TDS of this section with no party, split "
                "between several parties: it does not change the status until the CA ties it "
                "to this party (mark 'TDS deducted outside Tally' with the challan reference).",
            }
        )
    if row.deducted_outside is not None:
        steps.append(
            {
                "step": "TDS deducted outside Tally (marked)",
                "value": m(row.deducted_outside),
                "note": row.outside_note or "",
            }
        )
    steps.append({"step": "Total TDS deducted (counted)", "value": m(row.deducted), "note": ""})
    steps.append(
        {"step": "Shortfall", "value": m(row.shortfall), "note": "TDS payable - TDS deducted"}
    )
    if row.excess:
        steps.append(
            {
                "step": "Excess deducted",
                "value": m(row.excess),
                "note": "TDS deducted - TDS payable",
            }
        )
    return steps


def _date(value: date | None) -> str | None:
    return value.isoformat() if value else None


def build(analysis: ClientTds, row: PartySection, today: date | None = None) -> dict:
    today = today or date.today()
    ev, party, payer = row.evaluation, row.party, analysis.payer
    rule = ev.rule
    identified = party.identified
    compliance = tc.compliance(
        ev.deduct_on or ev.crossed_on, ev.tds, row.deducted, row.shortfall,
        base_not_covered(row) if identified else ev.aggregate, today, row.section_key,
    )  # fmt: skip
    cause = None
    if identified:
        cause = cross_section_cause(row, analysis.rows) or te.likely_cause(
            ev, row.deducted, analysis.rules
        )
    return {
        "client_id": analysis.client_id,
        "client_name": analysis.client_name,
        "fy": analysis.fy,
        "status": row.status,
        "status_label": status_label(row.status),
        "status_emoji": STATUS_EMOJI.get(row.status, ""),
        "mapping_approved": analysis.mapping_approved,
        "party": {
            "key": party.key,
            "name": party.name,
            "identified": party.identified,
            "pan": party.pan,
            "pan_source": party.pan_source,
            "payee_type": party.payee_type,
            "payee_type_label": PAYEE_LABELS.get(party.payee_type, party.payee_type),
            "payee_source": party.payee_source,
            "gstin": party.gstin,
            "deductee_type": party.deductee_type,
            "tally_transporter": party.tally_transporter,
            "transporter_declaration": party.transporter_declaration,
            "tcs_206c1h": party.tcs_206c1h,
            "tcs_vouchers": party.tcs_vouchers,
            "tcs_amount": m(party.tcs_amount),
            "note": party.note,
            "assigned": party.assigned,
            "pan_warning": party.pan_warning,
            "pan_warning_consequence": te.WRONG_PAN_NOTE if party.pan_warning else None,
        },
        "section": {
            "key": row.section_key,
            "nature": rule.nature,
            "who_must_deduct": WHO_DEDUCTS.get(row.section_key, ""),
            "payee": PAYEE.get(row.section_key, "Resident person"),
            "remarks": rule.remarks,
            "effective_from": rule.effective_from.isoformat(),
            "mapped_section": row.mapped_section,
            "provisional": row.provisional,
            "why": {
                "applies": row.decision.applies,
                "reason": row.decision.reason,
                "needs_confirmation": row.decision.needs_confirmation,
                "previous_fy": payer.previous_fy,
                "previous_turnover": m(payer.previous_turnover),
                "constitution": payer.constitution,
                "constitution_source": payer.constitution_source,
                "audit_liable": payer.audit_liable,
                "audit_source": payer.audit_source,
            },
        },
        "threshold": {
            "text": te.threshold_text(rule),
            "single_threshold": m(rule.single_threshold),
            "aggregate_threshold": m(rule.aggregate_threshold),
            "aggregate": m(ev.aggregate),
            "largest_single": m(ev.largest_single),
            "crossed": ev.crossed,
            "crossed_by": ev.crossed_by.value if ev.crossed_by else None,
            "crossing_test": ev.crossing_test.value if ev.crossing_test else None,
            "crossed_on": _date(ev.crossed_on),
            "crossed_voucher_no": ev.crossed_voucher_no,
            "excess_over_threshold": (
                m(ev.excess_over_threshold) if rule.base == te.EXCESS else None
            ),
            "approaching": ev.approaching,
            "approaching_pct": m(ev.approaching_pct),
            "months": {k: m(v) for k, v in (ev.months or {}).items()},
            "deduct_on": _date(ev.deduct_on),
        },
        "calculation": {
            "steps": calculation_steps(row),
            "base": m(ev.base),
            "rate": rate_text(row),
            "rate_reason": ev.rate.reason,
            "tds": m(ev.tds),
            "deducted": m(row.deducted),
            "deducted_in_books": m(row.deducted_in_books),
            "deducted_attributed": m(row.deducted_attributed),
            "deducted_allocated": m(row.deducted_allocated),
            "deducted_outside": m(row.deducted_outside),
            "outside_note": row.outside_note,
            "shortfall": m(row.shortfall),
            "excess": m(row.excess),
            "likely_cause": cause,
        },
        "unidentified": None if identified else _unidentified(row),
        "alternatives": [
            {
                "section": a.section_key,
                "crossed": a.crossed,
                "rate": m(a.rate),
                "tds": m(a.tds),
                "test": a.test,
            }
            for a in row.alternatives
        ],
        "vouchers": [
            {
                "date": line.payment.when.isoformat(),
                "voucher_type": line.payment.voucher_type,
                "voucher_no": line.payment.voucher_no,
                "ledger": line.payment.ledger,
                "amount": m(line.payment.amount),
                "running_total": m(line.running_total),
                "liable": m(line.liable),
                "rate": m(line.rate),
                "is_crossing": line.is_crossing,
                "voucher_key": line.payment.voucher_key,
            }
            for line in ev.lines
        ],
        "excluded_vouchers": [
            {
                "date": p.when.isoformat(),
                "voucher_type": p.voucher_type,
                "voucher_no": p.voucher_no,
                "ledger": p.ledger,
                "amount": m(p.amount),
                "voucher_key": p.voucher_key,
                "reason": "Seller charged TCS u/s 206C(1H)",
            }
            for p in row.excluded_payments
        ],
        "deductions": [
            {
                "date": d.when.isoformat(),
                "voucher_type": d.voucher_type,
                "voucher_no": d.voucher_no,
                "ledger": d.ledger,
                "amount": m(d.amount),
            }
            for d in row.deductions
        ],
        "section_pool": _pool(analysis, row.section_key),
        "estimate": estimate(row.exposure),
        "year_end_note": _year_end_note(row),
        "other_section_vouchers": [
            {
                "section": other.section_key,
                "date": line.payment.when.isoformat(),
                "voucher_type": line.payment.voucher_type,
                "voucher_no": line.payment.voucher_no,
                "ledger": line.payment.ledger,
                "amount": m(line.payment.amount),
                "voucher_key": line.payment.voucher_key,
            }
            for other in analysis.rows
            if party.identified
            and other.party.key == party.key
            and other.section_key != row.section_key
            for line in other.evaluation.lines
        ],
        "money_at_stake": m(row.at_stake),
        "compliance": _compliance(
            compliance, ev, row.section_key, analysis.deposits.get(row.section_key)
        ),
    }


def estimate(exposure: tc.Exposure | None) -> dict | None:
    """The estimated-amounts block (FY closed: what a default costs as of today), with the
    interest worked voucher by voucher (grouped by the date the TDS became deductible)."""
    if exposure is None:
        return None
    e = exposure
    lines = []
    if e.basis > 0:
        lines.append({"item": f"Basis: {e.basis_label}", "amount": m(e.basis), "working": ""})
    if e.lines:
        first, last = e.lines[0], e.lines[-1]
        passed = "passed" if e.today > last.deposit_due else "not yet passed for all"
        lines.append({"item": "Deposit due dates", "amount": None,
                      "working": f"{first.deposit_due:%d-%b-%Y} to {last.deposit_due:%d-%b-%Y} ({passed})"})  # fmt: skip
        lines.append({"item": "Interest if not deducted (1% a month or part, s.201(1A)(i))",
                      "amount": m(e.interest),
                      "working": f"per voucher from the date each became deductible to {e.today:%d-%b-%Y}, summed (see the working)"})  # fmt: skip
        lines.append({"item": "Interest if deducted but not deposited (1.5% a month or part, s.201(1A)(ii)) - alternative",
                      "amount": m(e.interest_15), "working": "same periods at 1.5%"})  # fmt: skip
    if e.disallowance:
        lines.append({"item": "Disallowance u/s 40(a)(ia)", "amount": m(e.disallowance),
                      "working": f"30% x {inr(e.disallowance_base)}"})  # fmt: skip
    if e.excess:
        lines.append(
            {
                "item": "TDS excess deducted (to correct / refund to the payee)",
                "amount": m(e.excess),
                "working": "",
            }
        )
    return {
        "label": tc.ESTIMATE_LABEL,
        "as_of": e.today.isoformat(),
        "lines": lines,
        "interest_working": [
            {
                "deductible_on": line.deductible_on.isoformat(),
                "vouchers": line.vouchers,
                "tds": m(line.tds),
                "months": line.months,
                "interest_1": m(line.interest_1),
                "interest_15": m(line.interest_15),
                "deposit_due": line.deposit_due.isoformat(),
            }
            for line in e.lines
        ],
        "interest": m(e.interest),
        "interest_15": m(e.interest_15),
        "money_at_stake": m(e.at_stake),
        "money_at_stake_working": (
            f"{inr(e.basis)} TDS + {inr(e.disallowance)} disallowance + {inr(e.interest)} interest at 1%"
            + (f" + {inr(e.excess)} excess" if e.excess else "")
        ),
        "fee_note": "The s.234E late fee is per return, not per party: see the client's return block.",
    }


def _year_end_note(row: PartySection) -> str | None:
    """A single-payment crossing caused by one year-end journal (e.g. a consolidation of
    many small bills) - in substance the bills may each be under the limit."""
    ev = row.evaluation
    if ev.crossing_test != te.CrossedBy.SINGLE or ev.crossed_by != te.CrossedBy.SINGLE:
        return None
    liable = [line for line in ev.lines if line.liable]
    if len(liable) != 1:
        return None
    p = liable[0].payment
    if "journal" not in (p.voucher_type or "").casefold() or (p.when.month, p.when.day) != (3, 31):
        return None
    return (
        f"Crossed only on the single-payment test, by one Journal ({p.voucher_no}) of {inr(p.amount)} "
        f"dated 31-Mar. If this books the year's bills in one go and each underlying bill was "
        f"{inr(ev.rule.single_threshold)} or less (with the year's total within "
        f"{inr(ev.rule.aggregate_threshold)}), no TDS is due - check the bills and, if so, mark it "
        f"resolved with a note."
    )


def _pool(analysis: ClientTds, section_key: str) -> dict | None:
    pool = analysis.pools.get(section_key)
    if pool is None:
        return None
    return {
        "amount": m(pool.amount),
        "attributed": m(pool.attributed),
        "attributed_to": pool.attributed_to,
        "allocated": m(pool.allocated),
        "unallocated": m(pool.unallocated),
        "entries": len(pool.lines),
        "deposited": m(analysis.deposits.get(section_key)),
    }


def _unidentified(row: PartySection) -> dict:
    """What is known about payments with no party ledger, and what it would cost."""
    ev = row.evaluation
    ledgers = sorted({line.payment.ledger for line in ev.lines})
    return {
        "amount": m(ev.aggregate),
        "ledgers": ledgers,
        "message": (
            f"{inr(ev.aggregate)} was paid under {row.section_key} with no party ledger (straight "
            f"from cash / bank), so the threshold cannot be tested per payee. If it went to ONE "
            f"payee it is over the threshold. Assign the payee to test it properly."
        ),
        "disallowance": m(tc.disallowance(ev.aggregate)),
        "disallowance_text": f"30% x {inr(ev.aggregate)} = {inr(tc.disallowance(ev.aggregate))} "
        "if no TDS was deducted (s.40(a)(ia))",
    }


def _compliance_194ib(items: list[dict], c: tc.Compliance, ev: te.PartyEvaluation, passed) -> None:
    items.append(_item(
        "Time of deduction (s.194-IB)",
        f"Once a year: at the time of credit / payment of the rent for the last month of the "
        f"tenancy or of the FY - here {c.deductible_on:%d-%b-%Y}. No TAN is needed.",
    ))  # fmt: skip
    items.append(_item(
        "Deposit and statement (Form 26QC)",
        f"Challan-cum-statement within 30 days of the end of the month of deduction: by "
        f"{c.deposit_due:%d-%b-%Y} - {passed[c.deposit_due_passed]}",
        _date(c.deposit_due),
    ))  # fmt: skip
    late = c.late_deduction
    items.append(_item(
        "Interest - late / no deduction (s.201(1A)(i), 1% per month or part)",
        late.text if late and late.amount > 0 else "No shortfall, so no interest for late deduction.",
        m(late.interest) if late else None,
    ))  # fmt: skip
    items.append(_item(
        "Late fee u/s 234E",
        f"₹200 per day of delay in filing Form 26QC, capped at the TDS amount ({inr(ev.tds)}).",
    ))  # fmt: skip
    items.append(_item(
        "TDS certificate (Form 16C)",
        f"Within 15 days of the Form 26QC due date: by {c.form_16a_due:%d-%b-%Y}.",
        _date(c.form_16a_due),
    ))  # fmt: skip
    items.append(
        _item(
            "Penalty u/s 271C", "Equal to the TDS not deducted / not deposited.", m(c.penalty_271c)
        )
    )
    items.append(_item(
        "Disallowance u/s 40(a)(ia)",
        f"30% of the rent on which TDS was not deducted / deposited: 30% x {inr(c.disallowance_base)}.",
        m(c.disallowance),
    ))  # fmt: skip
    items.append(_item(
        "No PAN",
        "TDS at 20% (s.206AA), but never more than the rent for the last month of the tenancy / FY.",
    ))  # fmt: skip


def _item(item: str, detail: str, figure: str | None = None) -> dict:
    return {"item": item, "detail": detail, "figure": figure}


def _compliance(
    c: tc.Compliance, ev: te.PartyEvaluation, section_key: str, deposited: Decimal | None
) -> dict:
    out = {"disclaimer": tc.DISCLAIMER, "today": _date(c.today), "items": []}
    items: list[dict] = out["items"]
    if c.deductible_on is None:
        items.append(
            _item("Time of deduction", "Threshold not crossed: nothing is deductible yet.")
        )
        return out
    passed = {True: "PASSED.", False: "not yet passed."}
    if section_key == "194-IB":
        _compliance_194ib(items, c, ev, passed)
        return out
    items.append(_item(
        "Time of deduction",
        f"Earlier of credit to the party's account or actual payment. In the books the threshold "
        f"was crossed by voucher {ev.crossed_voucher_no} on {c.deductible_on:%d-%b-%Y}: TDS on the "
        f"base became deductible then.",
    ))  # fmt: skip
    items.append(_item(
        "Deposit due date",
        f"7th of the following month (March deductions: 30 April). For a deduction on "
        f"{c.deductible_on:%d-%b-%Y}: {c.deposit_due:%d-%b-%Y} - {passed[c.deposit_due_passed]}",
        _date(c.deposit_due),
    ))  # fmt: skip
    late = c.late_deduction
    items.append(_item(
        "Interest - late / no deduction (s.201(1A)(i), 1% per month or part)",
        late.text if late and late.amount > 0 else "No shortfall, so no interest for late deduction.",
        m(late.interest) if late else None,
    ))  # fmt: skip
    # Deposit dates and return filing are not in the Tally data: these two are shown as
    # 'what it would be' in the text, never as an amount due in the Figure column.
    deposit = c.late_deposit
    if deposit is None:
        deposit_text = (
            "Applies from the date of deduction to the date of deposit, once TDS is deducted."
        )
    else:
        paid = (
            f" Tally shows {inr(deposited)} paid to the government under {section_key} in the year - compare the challan dates."
            if deposited
            else ""
        )
        deposit_text = (
            f"Only for the months the deducted TDS stayed undeposited after the due date.{paid} "
            f"If none of it has been deposited yet: {deposit.text}."
        )
    items.append(
        _item("Interest - late deposit (s.201(1A)(ii), 1.5% per month or part)", deposit_text)
    )
    items.append(_item(
        "Quarterly return (Form 26Q)",
        f"Q1 31 Jul / Q2 31 Oct / Q3 31 Jan / Q4 31 May. This deduction falls in {c.quarter}: due "
        f"{c.return_due:%d-%b-%Y} - {passed[c.return_due_passed]}",
        _date(c.return_due),
    ))  # fmt: skip
    fee_text = (
        f"₹200 per day after the {c.quarter} due date, capped at the TDS of the WHOLE return - it is "
        "levied once per return, not per party: see the client's return block in the TDS report."
    )
    items.append(_item("Late fee u/s 234E", fee_text))
    items.append(_item(
        "TDS certificate (Form 16A)",
        f"Within 15 days of the return due date: by {c.form_16a_due:%d-%b-%Y}.",
        _date(c.form_16a_due),
    ))  # fmt: skip
    items.append(
        _item(
            "Penalty u/s 271C", "Equal to the TDS not deducted / not deposited.", m(c.penalty_271c)
        )
    )
    items.append(_item("Penalty u/s 271H", f"{c.penalty_271h} for a return default."))
    items.append(_item(
        "Disallowance u/s 40(a)(ia)",
        f"30% of the expenditure on which TDS was not deducted / deposited (allowed in the year of "
        f"deposit): 30% x {inr(c.disallowance_base)}.",
        m(c.disallowance),
    ))  # fmt: skip
    items.append(_item(
        "Lower / nil deduction",
        "Certificate u/s 197 from the Assessing Officer. Form 15G/15H is not available for these "
        "payments.",
    ))  # fmt: skip
    items.append(
        _item("Non-filer higher rate (s.206AB)", "Omitted w.e.f. 01.04.2025 - not applied.")
    )
    return out
