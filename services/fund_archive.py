"""Database import, quarterly snapshots and holdings changes for SEC filings."""

from __future__ import annotations

import json
import zlib
from collections import defaultdict
from hashlib import sha256
from datetime import date

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from models.database import FundFiling, FundQuarterHistory, db
from services.filing_history import ARCHIVE, normalize_filing
from services.leaderboard_funds import FUNDS


def import_archive() -> dict[str, int]:
    """Idempotent upsert in small transactions so a stopped run can resume."""
    insert = pg_insert if db.engine.dialect.name == "postgresql" else sqlite_insert
    counts = {}
    for fund in FUNDS:
        slug = fund["slug"]
        paths = sorted((ARCHIVE / slug).glob("???????*-[0-9][0-9]-*.json"))
        existing = set(db.session.scalars(db.select(FundFiling.accession).where(FundFiling.fund == slug)))
        pending = [path for path in paths if path.stem not in existing]
        # Keep each database statement small even for multi-megabyte disclosures.
        for index, path in enumerate(pending, 1):
            doc = normalize_filing(json.loads(path.read_bytes()))
            statement = insert(FundFiling).values(
                fund=slug,
                accession=doc["accession"],
                period=date.fromisoformat(doc["period"]),
                filed=date.fromisoformat(doc["filed"]),
                document=doc,
            )
            db.session.execute(statement.on_conflict_do_nothing(index_elements=[FundFiling.accession]))
            if index % 10 == 0:
                db.session.commit()
        if pending:
            db.session.commit()
        counts[slug] = len(paths)
    return counts


def documents(slug: str, period: str | None = None) -> list[dict]:
    if period is None:
        # Reuse immutable accession files already imported by the build worker.
        # A fresh worker streams individual payloads from the same database.
        ids = db.session.scalars(
            db.select(FundFiling.accession)
            .where(FundFiling.fund == slug)
            .order_by(FundFiling.filed, FundFiling.accession)
        ).all()
        docs = []
        for accession in ids:
            path = ARCHIVE / slug / f"{accession}.json"
            try:
                doc = json.loads(path.read_bytes())
            except (ValueError, OSError):
                doc = db.session.scalar(
                    db.select(FundFiling.document).where(FundFiling.accession == accession)
                )
            docs.append(normalize_filing(doc))
        return docs
    stmt = db.select(FundFiling.document).where(FundFiling.fund == slug)
    if period:
        stmt = stmt.where(FundFiling.period == date.fromisoformat(period))
    return [
        normalize_filing(doc)
        for doc in db.session.scalars(stmt.order_by(FundFiling.filed, FundFiling.accession))
    ]


def periods(slug: str) -> list[str]:
    stmt = (
        db.select(FundFiling.period)
        .where(FundFiling.fund == slug)
        .distinct()
        .order_by(FundFiling.period.desc())
    )
    return [day.isoformat() for day in db.session.scalars(stmt)]


def _update(books: dict, filing: dict) -> bool:
    period = filing["period"]
    mode = filing["mode"]
    if mode == "unclassified amendment":
        return False
    if mode == "addition":
        if period not in books:
            return False
        rows = books[period]["positions"] + filing["positions"]
        sources = books[period]["sources"] + [filing]
    else:
        rows = filing["positions"]
        sources = [filing]
    books[period] = {**filing, "positions": rows, "sources": sources}
    return True


def timeline(filings: list[dict]) -> list[dict]:
    """As known after each publication date, ignoring amendments to superseded periods."""
    books = {}
    effective = {}
    for filing in sorted(filings, key=lambda f: (f["filed"], f["mode"] != "original", f["accession"])):
        if _update(books, filing) and filing["period"] == max(books):
            effective[filing["filed"]] = books[filing["period"]]
    return list(effective.values())


def quarter_books(filings: list[dict]) -> dict[str, dict]:
    """Latest usable disclosure for each quarter, including late amendments."""
    books = {}
    for filing in sorted(filings, key=lambda f: (f['filed'], f['mode'] != 'original', f['accession'])):
        _update(books, filing)
    return books


def quarter_book(filings: list[dict], period: str) -> dict | None:
    books = {}
    for filing in sorted(filings, key=lambda f: (f["filed"], f["mode"] != "original", f["accession"])):
        if filing["period"] == period:
            _update(books, filing)
    return books.get(period)


def aggregate(book: dict | None, *, equity_only: bool = False) -> list[dict]:
    """Collapse manager allocation rows while preserving security and option classes."""
    positions: dict[tuple, dict] = {}
    for row in (book or {}).get("positions", []):
        if equity_only and (row["option"] or row["quantity_type"] != "SH" or row["value"] <= 0):
            continue
        key = position_key(row)
        if key not in positions:
            positions[key] = {**row, "value": 0, "shares": 0}
        positions[key]["value"] += row["value"]
        positions[key]["shares"] += row["shares"]
    tot = sum(row["value"] for row in positions.values())
    return sorted(
        ({**row, "weight": row["value"] / tot if tot else 0} for row in positions.values()),
        key=lambda row: -row["value"],
    )


def position_key(row: dict) -> tuple:
    return row["cusip"], row["option"], row["quantity_type"]


def changes(current: list[dict], previous: list[dict]) -> list[dict]:
    """Report entries, exits and quantity/weight changes between disclosed quarters."""
    before = {position_key(row): row for row in previous}
    after = {position_key(row): row for row in current}
    result = []
    for key in before.keys() | after.keys():
        old, new = before.get(key), after.get(key)
        row = new or {**old, "value": 0, "shares": 0, "weight": 0}
        weight = (new or {}).get("weight", 0) - (old or {}).get("weight", 0)
        shares = (new or {}).get("shares", 0) - (old or {}).get("shares", 0)
        action = (
            "New"
            if old is None
            else "Exited"
            if new is None
            else "Increased"
            if shares > 0
            else "Reduced"
            if shares < 0
            else "Held"
        )
        result.append({**row, "action": action, "shares_change": shares, "weight_change": weight})
    return sorted(result, key=lambda row: (-abs(row["weight_change"]), row["issuer"], row["cusip"]))


def stats() -> dict[str, dict]:
    result = {}
    stmt = db.select(
        FundFiling.fund,
        db.func.count(),
        db.func.count(db.distinct(FundFiling.period)),
        db.func.min(FundFiling.period),
        db.func.max(FundFiling.period),
    ).group_by(FundFiling.fund)
    for slug, filings, quarters, first, last in db.session.execute(stmt):
        result[slug] = {
            "filings": filings,
            "quarters": quarters,
            "first": first.isoformat(),
            "last": last.isoformat(),
        }
    return result


def quarter_snapshot(filings, period, previous, prior_positions):
    book = quarter_book(filings, period)
    positions = aggregate(book)
    valid_previous = previous if prior_positions is not None and book else None
    rows = changes(positions, prior_positions) if valid_previous else positions
    sources = [
        {key: doc[key] for key in ("accession", "filed", "url", "form", "mode", "format")}
        | {"adjusted_units": any("reported_value" in p for p in doc["positions"]),
           "reconciliation": doc.get("reconciliation")}
        for doc in filings
    ]
    payload = {
        "book": {"filed": book["filed"]} if book else None,
        "filings": sources,
        "previous": valid_previous,
        "rows": rows,
    }
    return payload


def publish_quarters():
    """Prepare holdings and share/weight deltas offline, including each amendment."""
    FundQuarterHistory.__table__.create(db.engine, checkfirst=True)
    for fund in FUNDS:
        slug = fund["slug"]
        saved = dict(
            db.session.execute(
                db.select(FundQuarterHistory.period, FundQuarterHistory.revision).where(
                    FundQuarterHistory.fund == slug
                )
            ).all()
        )
        groups = defaultdict(list)
        source_ids = db.select(FundFiling.period, FundFiling.accession).where(
            FundFiling.fund == slug
        ).order_by(FundFiling.filed, FundFiling.accession)
        for day, accession in db.session.execute(source_ids):
            groups[day.isoformat()].append(accession)
        periods = sorted(groups)
        loaded = None
        # Both periods enter the revision so a late amendment updates its successor.
        for index, period in enumerate(periods):
            previous = periods[index - 1] if index else None
            accessions = [accession for day in (previous, period) for accession in groups.get(day, [])]
            revision = sha256(("1 " + " ".join(accessions)).encode()).hexdigest()
            day = date.fromisoformat(period)
            if saved.get(day) == revision:
                continue
            if loaded is None:
                loaded = defaultdict(list)
                for doc in documents(slug):
                    loaded[doc["period"]].append(doc)
            prior = quarter_book(loaded[previous], previous) if previous else None
            snapshot = quarter_snapshot(loaded[period], period, previous, aggregate(prior) if prior else None)
            content = zlib.compress(json.dumps(snapshot, separators=(",", ":"), allow_nan=False).encode())
            insert = pg_insert if db.engine.dialect.name == "postgresql" else sqlite_insert
            stmt = insert(FundQuarterHistory).values(
                fund=slug, period=day, revision=revision, content=content
            )
            db.session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["fund", "period"],
                    set_={"revision": stmt.excluded.revision, "content": stmt.excluded.content},
                )
            )
            db.session.commit()


def saved_quarter(slug, period):
    content = db.session.scalar(
        db.select(FundQuarterHistory.content).where(
            FundQuarterHistory.fund == slug, FundQuarterHistory.period == date.fromisoformat(period)
        )
    )
    return json.loads(zlib.decompress(content)) if content else None
