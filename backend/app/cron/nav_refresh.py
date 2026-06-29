"""Daily NAV refresh: pulls AMFI NAVAll and updates nav_cache."""
from __future__ import annotations
import logging
from datetime import date
from io import StringIO

import httpx

from app.database import SessionLocal
from app.models.db import NavCache

log = logging.getLogger(__name__)

AMFI_URL = "https://www.amfiindia.com/spages/NAVAll.txt"


def fetch_nav_all() -> dict[str, float]:
    """Download AMFI NAVAll and parse into {scheme_code: nav}."""
    try:
        response = httpx.get(AMFI_URL, timeout=30)
        response.raise_for_status()
    except Exception as exc:
        log.error("Failed to fetch AMFI NAVAll: %s", exc)
        return {}

    navs: dict[str, float] = {}
    for line in response.text.splitlines():
        parts = line.strip().split(";")
        if len(parts) >= 5:
            scheme_code = parts[0].strip()
            nav_str = parts[4].strip()
            if scheme_code.isdigit():
                try:
                    navs[scheme_code] = float(nav_str)
                except ValueError:
                    pass
    return navs


def refresh_navs() -> int:
    """Pull NAVAll and upsert into nav_cache. Returns number of records updated."""
    navs = fetch_nav_all()
    if not navs:
        return 0

    today = date.today()
    db = SessionLocal()
    try:
        count = 0
        for scheme_code, nav in navs.items():
            existing = db.get(NavCache, scheme_code)
            if existing:
                existing.nav = nav
                existing.nav_date = today
            else:
                db.add(NavCache(scheme_code=scheme_code, nav=nav, nav_date=today))
            count += 1
            if count % 1000 == 0:
                db.flush()
        db.commit()
        log.info("NAV refresh complete: %d records updated", count)
        return count
    except Exception as exc:
        db.rollback()
        log.error("NAV refresh failed: %s", exc)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    n = refresh_navs()
    print(f"Updated {n} NAV records")
