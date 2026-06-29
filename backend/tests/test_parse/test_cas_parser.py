"""Parser contract tests.

These test the classification logic and STP reconstruction in isolation
(without a real PDF, which is not checked into the repo).
"""
import pytest
from datetime import date, timedelta
from app.parse.cas_parser import classify_txn_type, _reconstruct_stps, ParsedLot, ParsedHolding, _is_elss, _compute_gain_type
from app.parse.sip_detector import detect_sips


TODAY = date(2026, 6, 29)


# ─── Transaction type classification ─────────────────────────────────────────

def test_classify_sip():
    assert classify_txn_type("SIP installment") == "sip"
    assert classify_txn_type("Systematic Investment Plan") == "sip"


def test_classify_purchase():
    assert classify_txn_type("Additional Purchase") == "purchase"
    assert classify_txn_type("New Purchase") == "purchase"


def test_classify_switch_out():
    assert classify_txn_type("Switch Out to Debt Fund") == "switch_out"


def test_classify_switch_in():
    assert classify_txn_type("Switch In from Equity Fund") == "switch_in"


def test_classify_redemption():
    assert classify_txn_type("Redemption") == "redemption"
    assert classify_txn_type("Withdrawal") == "redemption"


def test_classify_idcw():
    assert classify_txn_type("IDCW Payout") == "idcw"
    assert classify_txn_type("Dividend Reinvestment") == "idcw"


def test_classify_stamp_duty():
    assert classify_txn_type("Stamp Duty") == "stamp_duty"


# ─── ELSS detection ───────────────────────────────────────────────────────────

def test_elss_detected():
    assert _is_elss("ELSS Tax Saving Fund") is True
    assert _is_elss("Tax Saving Fund") is True
    assert _is_elss("Flexi Cap Fund") is False


# ─── Gain type computation ────────────────────────────────────────────────────

def test_gain_type_locked():
    future_lock = TODAY + timedelta(days=365)
    result = _compute_gain_type(date(2024, 1, 1), future_lock, TODAY)
    assert result == "locked"


def test_gain_type_ltcg():
    past_lock = TODAY - timedelta(days=365)
    old_buy = date(2020, 1, 1)
    result = _compute_gain_type(old_buy, past_lock, TODAY)
    assert result == "ltcg"


def test_gain_type_stcg():
    recent_buy = TODAY - timedelta(days=180)
    result = _compute_gain_type(recent_buy, None, TODAY)
    assert result == "stcg"


# ─── STP reconstruction ──────────────────────────────────────────────────────

def test_stp_reconstruction():
    switch_out_lot = ParsedLot(
        scheme_code="SC001", scheme_name="Equity Fund", amc="AMC1", category="Equity",
        units=100, nav_at_buy=50, cost_basis=5000, buy_date=date(2025, 1, 15),
        lock_until=None, gain_type="ltcg", txn_type="switch_out",
    )
    switch_in_lot = ParsedLot(
        scheme_code="SC002", scheme_name="Debt Fund", amc="AMC1", category="Debt",
        units=200, nav_at_buy=25, cost_basis=5000, buy_date=date(2025, 1, 15),
        lock_until=None, gain_type="ltcg", txn_type="switch_in",
    )
    h1 = ParsedHolding("SC001", "Equity Fund", "AMC1", "Equity", 100, [switch_out_lot])
    h2 = ParsedHolding("SC002", "Debt Fund", "AMC1", "Debt", 200, [switch_in_lot])
    _reconstruct_stps([h1, h2])
    assert switch_out_lot.txn_type == "stp_out"
    assert switch_in_lot.txn_type == "stp_in"


# ─── SIP detection ────────────────────────────────────────────────────────────

def test_sip_detection_monthly():
    # 6 monthly transactions
    from datetime import date
    txns = [(date(2025, m, 10), 5000.0) for m in range(1, 7)]
    lots_by_scheme = {"SC001": txns}
    scheme_meta = {"SC001": ("Flexi Cap Fund", "AMC1")}
    results = detect_sips(lots_by_scheme, scheme_meta, today=date(2025, 7, 1))
    assert len(results) == 1
    assert results[0].cadence == "monthly"
    assert results[0].detection_confidence == "high"
    assert results[0].suggested_amount == 5000.0


def test_sip_detection_no_pattern():
    # Irregular transactions
    txns = [
        (date(2025, 1, 5), 3000),
        (date(2025, 3, 15), 7000),
        (date(2025, 5, 22), 5000),
    ]
    lots_by_scheme = {"SC001": txns}
    scheme_meta = {"SC001": ("Fund X", "AMC")}
    results = detect_sips(lots_by_scheme, scheme_meta, today=date(2025, 7, 1))
    assert len(results) == 0


def test_sip_detection_quarterly():
    txns = [
        (date(2024, 10, 1), 15000),
        (date(2025, 1, 1), 15000),
        (date(2025, 4, 1), 15000),
        (date(2025, 7, 1), 15000),
    ]
    lots_by_scheme = {"SC001": txns}
    scheme_meta = {"SC001": ("Debt Fund", "AMC")}
    results = detect_sips(lots_by_scheme, scheme_meta, today=date(2025, 8, 1))
    assert len(results) == 1
    assert results[0].cadence == "quarterly"
