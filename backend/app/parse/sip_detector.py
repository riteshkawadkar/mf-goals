"""Detect recurring SIP patterns from parsed transaction lots.

Returns DetectedSip candidates for the user to confirm.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from typing import Optional
import statistics


@dataclass
class DetectedSipCandidate:
    scheme_code: str
    scheme_name: str
    suggested_amount: float
    cadence: str               # monthly | quarterly
    last_installment_date: date
    detection_confidence: str  # high | medium | low


def detect_sips(
    lots_by_scheme: dict[str, list[tuple[date, float]]],  # scheme_code → [(buy_date, amount)]
    scheme_meta: dict[str, tuple[str, str]],              # scheme_code → (scheme_name, amc)
    today: Optional[date] = None,
) -> list[DetectedSipCandidate]:
    """
    For each scheme, determine if there is a recurring monthly or quarterly
    purchase pattern over the last 12 months.
    """
    if today is None:
        today = date.today()

    results: list[DetectedSipCandidate] = []
    cutoff = date(today.year - 1, today.month, today.day)

    for scheme_code, txns in lots_by_scheme.items():
        # Keep only SIP-type (or purchase) transactions within last 12 months
        recent = [(d, amt) for d, amt in txns if d >= cutoff]
        if len(recent) < 2:
            continue

        recent_sorted = sorted(recent, key=lambda x: x[0])
        dates = [d for d, _ in recent_sorted]
        amounts = [a for _, a in recent_sorted]

        # Check monthly cadence: intervals ~25-35 days
        intervals = [(dates[i+1] - dates[i]).days for i in range(len(dates)-1)]
        monthly_like = [i for i in intervals if 20 <= i <= 45]
        quarterly_like = [i for i in intervals if 80 <= i <= 100]

        if len(monthly_like) >= len(intervals) * 0.7 and len(recent) >= 3:
            cadence = "monthly"
            confidence = "high" if len(recent) >= 6 else ("medium" if len(recent) >= 3 else "low")
        elif len(quarterly_like) >= len(intervals) * 0.7 and len(recent) >= 2:
            cadence = "quarterly"
            confidence = "high" if len(recent) >= 4 else "medium"
        else:
            continue

        median_amount = statistics.median(amounts)
        scheme_name, _ = scheme_meta.get(scheme_code, (scheme_code, ""))

        results.append(DetectedSipCandidate(
            scheme_code=scheme_code,
            scheme_name=scheme_name,
            suggested_amount=round(median_amount, 0),
            cadence=cadence,
            last_installment_date=dates[-1],
            detection_confidence=confidence,
        ))

    return results
