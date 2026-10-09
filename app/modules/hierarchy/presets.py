"""Starting points for a church's structure. Applying one only creates unit
types — a church can rename, add or remove them afterwards; nothing in the
code depends on these keys."""

from app.modules.hierarchy.schemas import UnitTypeCreate, UnitTypePreset


def _t(key: str, label: str, plural: str, parents: list[str], *, root: bool = False, order: int = 0) -> UnitTypeCreate:
    return UnitTypeCreate(
        key=key, label=label, plural_label=plural, allowed_parent_keys=parents, can_be_root=root, sort_order=order
    )


PRESETS: list[UnitTypePreset] = [
    UnitTypePreset(
        key="single_church",
        name="Single church with branches",
        description="One church, optionally with branches and cells.",
        types=[
            _t("church", "Church", "Churches", [], root=True, order=0),
            _t("branch", "Branch", "Branches", ["church"], order=1),
            _t("cell", "Cell", "Cells", ["church", "branch"], order=2),
        ],
    ),
    UnitTypePreset(
        key="diocesan",
        name="Diocese and parishes",
        description="A diocese of parishes, each with outstations and small Christian communities.",
        types=[
            _t("diocese", "Diocese", "Dioceses", [], root=True, order=0),
            _t("deanery", "Deanery", "Deaneries", ["diocese"], order=1),
            _t("parish", "Parish", "Parishes", ["diocese", "deanery"], root=True, order=2),
            _t("outstation", "Outstation", "Outstations", ["parish"], order=3),
            _t(
                "community",
                "Small Christian community",
                "Small Christian communities",
                ["parish", "outstation"],
                order=4,
            ),
        ],
    ),
    UnitTypePreset(
        key="multi_campus",
        name="Multi-campus church",
        description="One church meeting at several campuses, each with small groups.",
        types=[
            _t("church", "Church", "Churches", [], root=True, order=0),
            _t("campus", "Campus", "Campuses", ["church"], order=1),
            _t("small_group", "Small group", "Small groups", ["campus"], order=2),
        ],
    ),
    UnitTypePreset(
        key="association",
        name="Association or NGO",
        description="An organisation with regional chapters.",
        types=[
            _t("organisation", "Organisation", "Organisations", [], root=True, order=0),
            _t("region", "Region", "Regions", ["organisation"], order=1),
            _t("chapter", "Chapter", "Chapters", ["organisation", "region"], order=2),
        ],
    ),
]

PRESETS_BY_KEY = {p.key: p for p in PRESETS}
