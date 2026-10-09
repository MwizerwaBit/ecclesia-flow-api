"""Plans and modules — the product catalogue, in one place.

Mirrors the frontend's module registry (src/modules/registry.ts) and tier
order (src/modules/types.ts). Both sides must agree on module ids and the tier
each one starts at; this file is the backend's half of that contract.
"""

from dataclasses import dataclass

TIER_ORDER = ["free", "seed", "parish", "growth", "diocese", "enterprise"]


def tier_at_least(tier: str, required: str) -> bool:
    return TIER_ORDER.index(tier) >= TIER_ORDER.index(required)


@dataclass(frozen=True)
class Module:
    id: str
    name: str
    min_tier: str
    #: Core modules are the product itself and can never be switched off.
    core: bool = False


MODULES: list[Module] = [
    Module("people", "People", "free", core=True),
    Module("attendance", "Attendance", "free", core=True),
    Module("gatherings", "Gatherings", "free", core=True),
    Module("giving", "Giving", "seed"),
    Module("communications", "Communications", "seed"),
    Module("certificates", "Certificates", "parish"),
    Module("hierarchy", "Structure", "parish"),
    Module("media", "Documents", "parish"),
    Module("analytics", "Analytics", "growth"),
    Module("administration", "Administration", "free", core=True),
]
MODULE_BY_ID = {m.id: m for m in MODULES}


@dataclass(frozen=True)
class Plan:
    tier: str
    name: str
    #: Price in the smallest currency unit (cents). None = talk to sales.
    monthly_cents: int | None
    tagline: str
    max_members: int | None

    @property
    def yearly_cents(self) -> int | None:
        # Two months free on annual billing.
        return None if self.monthly_cents is None else self.monthly_cents * 10

    def price(self, interval: str) -> int | None:
        return self.monthly_cents if interval == "month" else self.yearly_cents


PLANS: list[Plan] = [
    Plan("seed", "Seed", 1900, "For a single congregation getting organised", 250),
    Plan("parish", "Parish", 4900, "Certificates, branches and documents", 1000),
    Plan("growth", "Growth", 9900, "Analytics and multi-site growth", 5000),
    Plan("diocese", "Diocese", 24900, "Many churches under one oversight", None),
    Plan("enterprise", "Enterprise", None, "Custom terms, dedicated support", None),
]
PLAN_BY_TIER = {p.tier: p for p in PLANS}
#: Tiers that can be bought through self-serve checkout.
PURCHASABLE_TIERS = [p.tier for p in PLANS if p.monthly_cents is not None]
