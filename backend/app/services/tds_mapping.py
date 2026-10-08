"""Proposes which TDS section a Tally ledger belongs to, from its name and groups.

Pure functions only. The result is only a PROPOSAL: it is stored with source 'auto', shown
to the CA on Settings -> TDS, and used for alerts only after the CA approves the client's
mapping. A ledger the rules cannot place stays 'unmapped' and is listed, never ignored.

Roles: 'base' (payments under a section), 'tds' (TDS deducted under a section),
'excluded' (looked at, and not a TDS payment/deduction - with the reason), 'unmapped'.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

BASE, TDS, EXCLUDED, UNMAPPED = "base", "tds", "excluded", "unmapped"
ROLES = (BASE, TDS, EXCLUDED, UNMAPPED)

# Ledger kinds (decided from the Master's groups when the data is captured).
PURCHASE, EXPENSE, TDS_LEDGER, TCS_LEDGER = "purchase", "expense", "tds", "tcs"
MAPPABLE_KINDS = (PURCHASE, EXPENSE, TDS_LEDGER, TCS_LEDGER)


@dataclass(frozen=True)
class Proposal:
    role: str
    section_key: str | None
    reason: str
    requires_choice: bool = False  # rent: the CA must choose 194I(a) or 194I(b)


RENT_CHOICE = (
    "RENT - choose 194I(a) (plant, machinery, equipment: 2%) or 194I(b) (land, building, "
    "furniture: 10%). Provisionally {key} from the name; the mapping cannot be approved "
    "until you choose."
)


def _rent(found: Proposal | None) -> Proposal | None:
    if found is None or found.section_key not in ("194I(a)", "194I(b)"):
        return found
    return Proposal(found.role, found.section_key, RENT_CHOICE.format(key=found.section_key), True)


def _rx(*words: str) -> re.Pattern:
    return re.compile("|".join(words), re.IGNORECASE)


# Checked in order; the first match wins. (pattern, role, section, reason)
_EXPENSE_RULES: list[tuple[re.Pattern, str, str | None, str]] = [
    (_rx(r"partner"), EXCLUDED, None,
     "Partners' remuneration / interest: not 194J or any section here (s.194T from 01.04.2025 is "
     "outside these sections)"),
    (_rx(r"interest on capital"), EXCLUDED, None, "Interest on partners' capital: not covered here"),
    (_rx(r"appropriation", r"share in (net )?profit", r"provision for tax", r"income tax"), EXCLUDED, None,
     "Appropriation of profit / tax provision, not a payment to a party"),
    (_rx(r"director"), BASE, "194J(ba)", "Director remuneration / fee: 194J(ba), no threshold"),
    (_rx(r"salar", r"wages", r"bonus", r"\besic?\b", r"provident", r"\bp\.?f\b", r"gratuity",
         r"staff", r"employee", r"uniform", r"leave encash"), EXCLUDED, None,
     "Salary / staff cost: s.192, not these sections"),
    (_rx(r"depreciation", r"amorti[sz]"), EXCLUDED, None, "Book entry, no payment to a party"),
    (_rx(r"interest", r"bank charge", r"loan processing", r"loan renew"), EXCLUDED, None,
     "Interest / bank charges: s.194A or a bank, not these sections"),
    (_rx(r"zomato", r"swiggy", r"aggregator", r"e-?commerce", r"magicpin", r"eazydiner",
         r"dineout", r"online (order|food|sales?) comm"), BASE, "194H",
     "E-commerce / food aggregator commission: 194H at 2% (the card / bank exclusion does not cover it)"),
    (_rx(r"credit card commission", r"\bmdr\b"), EXCLUDED, None,
     "Card commission (MDR) retained by the bank - verify"),
    (_rx(r"insurance"), EXCLUDED, None, "Insurance premium: no TDS"),
    (_rx(r"round", r"discount", r"rebate", r"short.*excess"), EXCLUDED, None, "Not a payment to a party"),
    (_rx(r"\bgst\b", r"\bp\.?tec\b", r"\bp\.?trc\b", r"profession(al)? tax", r"property tax",
         r"water tax", r"labou?r tax", r"stamp", r"registration", r"penalty", r"\bfine",
         r"\btax\b", r"\bduty\b", r"\bvat\b", r"\bcst\b"), EXCLUDED, None,
     "Tax / duty / penalty paid to government: no TDS"),
    (_rx(r"electric", r"telephone", r"mobile", r"internet", r"water exp", r"\bgas\b", r"coal",
         r"fuel", r"petrol", r"diesel"), EXCLUDED, None,
     "Utility / consumable purchase - no TDS section here (verify contracts)"),
    (_rx(r"donation", r"membership", r"subscription", r"association fee", r"welfare",
         r"travel", r"conveyance", r"entertainment", r"medical", r"sundry", r"miscellan",
         r"office exp", r"other (charges|exp)", r"printing", r"stationery", r"hotel exp"),
     EXCLUDED, None,
     "Generally not a contract / professional payment - verify large single payees"),
    (_rx(r"profession", r"legal", r"audit fee", r"accounting", r"accountancy", r"consult",
         r"retainer", r"architect", r"\bca fee", r"advocate", r"valuation"), BASE, "194J(b)",
     "Professional fees: 194J(b)"),
    (_rx(r"royalty", r"non.?compete"), BASE, "194J(b)", "Royalty / non-compete: 194J(b)"),
    (_rx(r"technical", r"call cent"), BASE, "194J(a)", "Technical services: 194J(a)"),
    (_rx(r"commission", r"brokerage"), BASE, "194H", "Commission / brokerage: 194H"),
    (_rx(r"(rent|hire|lease).*(machin|plant|equipment|vehicle|car|edc|sound ?box|crane|generator)",
         r"(machin|plant|equipment|vehicle|edc|sound ?box|crane|generator).*(rent|hire|lease)"),
     BASE, "194I(a)", "Rent / hire of plant, machinery or equipment: 194I(a)"),
    (_rx(r"\brent", r"rental", r"lease", r"godown", r"premises"), BASE, "194I(b)",
     "Rent of land / building / premises: 194I(b)"),
    (_rx(r"labou?r", r"job ?work", r"contract", r"trans?port", r"freight", r"cartage",
         r"carriage", r"loading", r"unloading", r"forwarding", r"advertis", r"marketing",
         r"catering", r"repair", r"maint", r"\bamc\b", r"cleaning", r"laundry",
         r"housekeeping", r"security", r"courier", r"decoration", r"delivery", r"servicing",
         r"fabrication", r"civil work", r"painting"), BASE, "194C",
     "Contract work (labour, job work, transport, advertising, repairs, ...): 194C"),
]  # fmt: skip

_PURCHASE_RULES: list[tuple[re.Pattern, str, str | None, str]] = [
    (_rx(r"discount", r"rebate", r"incentive", r"ineligible itc", r"itc reversal",
         r"round"), EXCLUDED, None, "Not a purchase of goods from a seller"),
    (_rx(r"liq(u|uo|ou)or", r"alcohol", r"\bbeer\b", r"\bwine\b", r"scrap"), EXCLUDED, None,
     "Seller collects TCS u/s 206C(1) (liquor / scrap), so s.194Q(5) excludes it - verify"),
    (_rx(r"labou?r", r"job ?work", r"contract", r"freight", r"transport", r"cartage"), BASE, "194C",
     "Work / transport booked in a purchase ledger: 194C, not 194Q"),
    (_rx(r"."), BASE, "194Q", "Purchase of goods: 194Q (applies only if the buyer's last-year turnover > ₹10 Cr)"),
]  # fmt: skip

_TDS_RULES: list[tuple[re.Pattern, str, str | None, str]] = [
    (_rx(r"receivable", r"\ba\.?y\.?", r"refund"), EXCLUDED, None,
     "TDS deducted BY others on the client's income (an asset), not TDS the client deducted"),
    (_rx(r"gst ?tds", r"tds ?gst"), EXCLUDED, None, "GST TDS (s.51 CGST Act), not income-tax TDS"),
    (_rx(r"interest", r"late fee", r"penalty"), EXCLUDED, None, "Interest / fee on TDS, not TDS"),
    (_rx(r"194 ?t\b", r"partner"), EXCLUDED, None, "s.194T (partners) - outside these sections"),
    (_rx(r"194\s*-?\s*ia\b", r"immovable"), EXCLUDED, None,
     "s.194-IA (purchase of immovable property) - outside these sections"),
    (_rx(r"194\s*-?\s*ib\b"), TDS, "194-IB", "TDS on rent by an Individual/HUF: s.194-IB"),
    (_rx(r"reimburse"), EXCLUDED, None,
     "Clearing account - the transfer to the section-wise TDS ledger is counted instead (verify)"),
    (_rx(r"194 ?q", r"purchase of goods", r"\bgoods\b", r"purchase"), TDS, "194Q", "TDS on purchase of goods: 194Q"),
    (_rx(r"director"), TDS, "194J(ba)", "TDS on director remuneration: 194J(ba)"),
    (_rx(r"technical"), TDS, "194J(a)", "TDS on technical services: 194J(a)"),
    (_rx(r"194 ?j", r"profession", r"legal", r"consult", r"royalty"), TDS, "194J(b)",
     "TDS on professional fees: 194J(b)"),
    (_rx(r"machin", r"plant", r"equipment", r"194 ?i ?\(?a"), TDS, "194I(a)",
     "TDS on rent of machinery: 194I(a)"),
    (_rx(r"\brent", r"194 ?i"), TDS, "194I(b)",
     "TDS on rent: 194I(b) (choose 194I(a) if it is rent of machinery)"),
    (_rx(r"194 ?c", r"contract", r"labou?r", r"transport", r"freight", r"job ?work"), TDS, "194C",
     "TDS on contract payments: 194C"),
    (_rx(r"194 ?h", r"commission", r"brokerage"), TDS, "194H", "TDS on commission: 194H"),
]  # fmt: skip

_ASSET_GROUPS = (
    "capital account",
    "current assets",
    "loans & advances (asset)",
    "deposits (asset)",
)


def _first(rules, text: str) -> Proposal | None:
    for pattern, role, section, reason in rules:
        if pattern.search(text):
            return Proposal(role, section, reason)
    return None


def propose(name: str, kind: str, groups: list[str]) -> Proposal:
    """Proposal for one ledger. `groups`: every group above it, nearest first."""
    if kind == TCS_LEDGER:
        return Proposal(
            EXCLUDED, None, "TCS charged by the seller - used as a hint for the s.206C(1H) flag"
        )
    if kind == TDS_LEDGER:
        found = _rent(_first(_TDS_RULES, name))
        if found and found.role == EXCLUDED:
            return found
        if any(g in _ASSET_GROUPS for g in groups):
            return Proposal(
                EXCLUDED,
                None,
                "TDS held as an asset (deducted by others), not deducted by the client",
            )
        return found or Proposal(
            UNMAPPED, None, "TDS ledger without a section in its name - choose one"
        )
    if kind == PURCHASE:
        return _first(_PURCHASE_RULES, name) or Proposal(BASE, "194Q", "Purchase of goods: 194Q")
    if kind == EXPENSE:
        found = _rent(_first(_EXPENSE_RULES, name))
        if found:
            return found
        # a ledger named after a person (e.g. under a 'Salary' group) takes its group's rule
        for group in groups:
            by_group = _first(_EXPENSE_RULES, group)
            if by_group:
                return Proposal(
                    by_group.role, by_group.section_key, f"{by_group.reason} (from group '{group}')"
                )
        return Proposal(UNMAPPED, None, "No rule matched - choose a section or exclude it")
    return Proposal(UNMAPPED, None, "Not an expense, purchase or TDS ledger")
