"""The reports that exist. A report is data here (title, filters, columns) and
code in service.py (how its rows are built). To add one: describe it below, add
a builder in service.py, and it appears in the list, the screen and the export.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Filter:
    key: str
    label: str
    #: "date" for now; "select" and "number" arrive with the filters that need them.
    kind: str = "date"


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    #: "text", "number" or "percent": how a screen or spreadsheet should format it.
    kind: str = "text"


@dataclass(frozen=True)
class Report:
    key: str
    title: str
    description: str
    columns: list[Column]
    filters: list[Filter] = field(default_factory=list)


REPORTS: dict[str, Report] = {
    r.key: r
    for r in [
        Report(
            key="membership_by_status",
            title="Membership by status",
            description="How many people are active, visitors, prospects or inactive.",
            columns=[
                Column("status", "Status"),
                Column("people", "People", "number"),
                Column("share", "Share", "percent"),
            ],
        ),
        Report(
            key="new_people_by_month",
            title="New people by month",
            description="How many people joined or were first recorded each month.",
            columns=[Column("month", "Month"), Column("people", "New people", "number")],
            filters=[Filter("from", "From"), Filter("to", "To")],
        ),
    ]
}
