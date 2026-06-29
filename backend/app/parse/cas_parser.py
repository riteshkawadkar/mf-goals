"""CAS PDF parse module.

Wraps casparser 1.2.1, classifies transaction types, reconstructs STPs from
switch pairs, computes lot-level ELSS lock dates. Deletes the PDF after
extraction (parse-and-discard).
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

try:
    import casparser
    from casparser.enums import TransactionType
    CASPARSER_AVAILABLE = True
except ImportError:
    casparser = None  # type: ignore
    TransactionType = None  # type: ignore
    CASPARSER_AVAILABLE = False


ELSS_LOCK_YEARS = 3

# casparser 1.2.1 TransactionType → internal txn_type string
_TXN_TYPE_MAP: dict[str, str] = {
    "PURCHASE_SIP":       "sip",
    "PURCHASE":           "purchase",
    "SWITCH_IN":          "switch_in",
    "SWITCH_IN_MERGER":   "switch_in",
    "GIFT_IN":            "switch_in",
    "SWITCH_OUT":         "switch_out",
    "SWITCH_OUT_MERGER":  "switch_out",
    "GIFT_OUT":           "switch_out",
    "REDEMPTION":         "redemption",
    "REVERSAL":           "redemption",
    "DIVIDEND_PAYOUT":    "idcw",
    "DIVIDEND_REINVEST":  "idcw",
    "STAMP_DUTY_TAX":     "stamp_duty",
    "STT_TAX":            "stamp_duty",
    "TDS_TAX":            "stamp_duty",
    "SEGREGATION":        "purchase",
    "MISC":               "purchase",
    "UNKNOWN":            "purchase",
}


def _map_txn_type(txn_type_enum) -> str:
    """Map casparser TransactionType enum to internal string."""
    if txn_type_enum is None:
        return "purchase"
    # TransactionType is a str-Enum so its value is the name string
    key = str(txn_type_enum.value) if hasattr(txn_type_enum, "value") else str(txn_type_enum)
    return _TXN_TYPE_MAP.get(key, "purchase")


@dataclass
class ParsedLot:
    scheme_code: str
    scheme_name: str
    amc: str
    category: str
    units: float
    nav_at_buy: float
    cost_basis: float
    buy_date: date
    lock_until: Optional[date]
    gain_type: str    # stcg | ltcg | locked
    txn_type: str


@dataclass
class ParsedHolding:
    scheme_code: str
    scheme_name: str
    amc: str
    category: str
    total_units: float
    lots: list[ParsedLot] = field(default_factory=list)


@dataclass
class ParseResult:
    holdings: list[ParsedHolding]
    total_tax_lots: int


def _is_elss(category: str) -> bool:
    return "elss" in category.lower() or "tax saving" in category.lower()


def _compute_gain_type(buy_date: date, lock_until: Optional[date], today: date) -> str:
    if lock_until and lock_until > today:
        return "locked"
    age_days = (today - buy_date).days
    return "ltcg" if age_days >= 365 else "stcg"


def _to_float(val) -> float:
    """Safely convert Decimal/str/None to float."""
    if val is None:
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _reconstruct_stps(holdings: list[ParsedHolding]) -> None:
    """Mark switch_out/switch_in pairs as STP (in-place annotation on txn_type)."""
    switch_outs: dict[tuple[date, float], list[ParsedLot]] = {}
    switch_ins: dict[tuple[date, float], list[ParsedLot]] = {}

    for holding in holdings:
        for lot in holding.lots:
            if lot.txn_type == "switch_out":
                key = (lot.buy_date, round(lot.cost_basis, 0))
                switch_outs.setdefault(key, []).append(lot)
            elif lot.txn_type == "switch_in":
                key = (lot.buy_date, round(lot.cost_basis, 0))
                switch_ins.setdefault(key, []).append(lot)

    for key in switch_outs:
        if key in switch_ins:
            for lot in switch_outs[key]:
                lot.txn_type = "stp_out"
            for lot in switch_ins[key]:
                lot.txn_type = "stp_in"


def _parse_date(raw) -> Optional[date]:
    if raw is None:
        return None
    if isinstance(raw, date):
        return raw
    try:
        from datetime import datetime as dt
        return dt.strptime(str(raw), "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_cas_file(pdf_path: str, password: Optional[str] = None) -> ParseResult:
    """Parse a CAS PDF and return structured holdings+lots. Deletes the PDF."""
    if not CASPARSER_AVAILABLE:
        raise RuntimeError("casparser library not installed")

    try:
        # casparser 1.2.1 API: read_cas_pdf(filename, password, output="dict")
        # output="dict" returns the CASData pydantic model directly
        data = casparser.read_cas_pdf(pdf_path, password=password or "", output="dict")
    except Exception as exc:
        _delete_file(pdf_path)
        raise ValueError(f"CAS parse failed: {exc}") from exc

    today = date.today()
    holdings_map: dict[str, ParsedHolding] = {}
    total_lots = 0

    for folio in (data.folios or []):
        amc = folio.amc or ""

        for scheme in (folio.schemes or []):
            scheme_code = scheme.isin or scheme.amfi or scheme.rta_code or ""
            scheme_name = scheme.scheme or "Unknown Scheme"
            category = scheme.type or ""

            key = scheme_code or scheme_name
            if key not in holdings_map:
                holdings_map[key] = ParsedHolding(
                    scheme_code=scheme_code,
                    scheme_name=scheme_name,
                    amc=amc,
                    category=category,
                    total_units=0.0,
                )

            holding = holdings_map[key]
            is_elss = _is_elss(category)

            for txn in (scheme.transactions or []):
                units = _to_float(txn.units)
                nav = _to_float(txn.nav)
                amount = _to_float(txn.amount)
                txn_date = _parse_date(txn.date)

                if txn_date is None:
                    continue

                txn_type = _map_txn_type(txn.type)

                # Only create lots for purchase-side transactions with positive units
                if units <= 0 or txn_type in ("switch_out", "redemption", "stamp_duty", "idcw"):
                    continue

                lock_until = None
                if is_elss:
                    lock_until = date(txn_date.year + ELSS_LOCK_YEARS, txn_date.month, txn_date.day)

                gain_type = _compute_gain_type(txn_date, lock_until, today)

                lot = ParsedLot(
                    scheme_code=scheme_code,
                    scheme_name=scheme_name,
                    amc=amc,
                    category=category,
                    units=units,
                    nav_at_buy=nav,
                    cost_basis=abs(amount),
                    buy_date=txn_date,
                    lock_until=lock_until,
                    gain_type=gain_type,
                    txn_type=txn_type,
                )
                holding.lots.append(lot)
                holding.total_units += units
                total_lots += 1

    _reconstruct_stps(list(holdings_map.values()))
    _delete_file(pdf_path)

    return ParseResult(
        holdings=list(holdings_map.values()),
        total_tax_lots=total_lots,
    )


def _delete_file(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
