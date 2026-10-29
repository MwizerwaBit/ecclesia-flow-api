"""Building and exporting reports. Every report reads through the caller's
church (row-level security) and branch (Scope), exactly like the screens do."""

import csv
import io
from collections import Counter
from datetime import date

from prisma import Prisma

from app.core.authz import Scope
from app.core.exceptions import AppError, NotFoundError
from app.core.spreadsheet import safe_cell
from app.modules.reports import repository
from app.modules.reports.definitions import REPORTS, Report

MAX_MONTHS = 120


class BadFilterError(AppError):
    status_code = 422
    code = "invalid_filter"


def get_report(key: str) -> Report:
    report = REPORTS.get(key)
    if report is None:
        raise NotFoundError("No such report")
    return report


def _date(params: dict[str, str], key: str, label: str) -> date | None:
    raw = (params.get(key) or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise BadFilterError(f"{label} must be a date like 2026-01-31") from exc


def _months(start: date, end: date) -> list[str]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


async def run(db: Prisma, scope: Scope, key: str, params: dict[str, str]) -> dict:
    report = get_report(key)
    facts = scope.filter_members(await repository.member_facts(db))
    applied: dict[str, str] = {}

    if key == "membership_by_status":
        counts = Counter(f["status"] for f in facts)
        total = sum(counts.values())
        order = ["active", "visitor", "prospect", "inactive"]
        rows = [
            {"status": s, "people": counts.get(s, 0), "share": round(counts.get(s, 0) / total, 4) if total else 0}
            for s in order
        ]
    elif key == "new_people_by_month":
        end = _date(params, "to", "To") or date.today()
        start = _date(params, "from", "From") or date(end.year - 1, end.month, 1)
        if start > end:
            raise BadFilterError("From can't be after To")
        if (end.year - start.year) * 12 + end.month - start.month >= MAX_MONTHS:
            raise BadFilterError(f"Choose a range of at most {MAX_MONTHS} months")
        counts = Counter(str(f["since"])[:7] for f in facts)
        rows = [{"month": m, "people": counts.get(m, 0)} for m in _months(start, end)]
        applied = {"from": start.isoformat(), "to": end.isoformat()}
    else:  # described in definitions.py but no builder yet
        raise NotFoundError("No such report")

    return {
        "key": report.key,
        "title": report.title,
        "columns": [{"key": c.key, "label": c.label, "kind": c.kind} for c in report.columns],
        "rows": rows,
        "filters": applied,
    }


def to_csv(result: dict) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow([c["label"] for c in result["columns"]])
    for row in result["rows"]:
        cells = []
        for c in result["columns"]:
            value = row[c["key"]]
            cells.append(f"{value * 100:.1f}%" if c["kind"] == "percent" else safe_cell(str(value)))
        writer.writerow(cells)
    return "﻿" + out.getvalue()
