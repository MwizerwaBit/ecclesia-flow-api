"""Static catalogues for platform-admin — mirrors the
app/modules/rbac/models.py pattern (constants, not a database table, since
every org shares the same catalogue; only overrides are per-tenant rows).

Tier/feature matrix transcribed from the Subscription Tiers table in the
product architecture brief (Seed/Parish/Diocese/Enterprise) — the one part
of that document that didn't conflict with anything already built, see
docs/architecture-doc-review.md.
"""

TIER_ORDER = ["free", "seed", "parish", "growth", "diocese", "enterprise"]

FEATURE_CATALOGUE = [
    {
        "code": "finance_gl",
        "label": "Finance / General Ledger",
        "description": "Budgets, payroll export, GL reporting",
        "min_tier": "diocese",
    },
    {
        "code": "choir_module",
        "label": "Choir Module",
        "description": "Rosters, rehearsals, voice parts",
        "min_tier": "parish",
    },
    {
        "code": "volunteer_module",
        "label": "Volunteer Module",
        "description": "Sign-ups, skills match, scheduling",
        "min_tier": "parish",
    },
    {
        "code": "background_checks",
        "label": "Volunteer Background Checks",
        "description": "Integrated background check requests",
        "min_tier": "enterprise",
    },
    {
        "code": "white_label_full",
        "label": "Full White-Label Branding",
        "description": "Full theming beyond logo-only",
        "min_tier": "diocese",
    },
    {
        "code": "custom_domain",
        "label": "Custom Domain",
        "description": "app.yourchurch.org instead of the shared domain",
        "min_tier": "diocese",
    },
    {
        "code": "api_access",
        "label": "Full REST API Access",
        "description": "Read/write API access plus webhooks",
        "min_tier": "diocese",
    },
    {
        "code": "advanced_analytics",
        "label": "Advanced Analytics",
        "description": "Hierarchy rollups and export",
        "min_tier": "diocese",
    },
]


def tier_default_enabled(min_tier: str, org_tier: str) -> bool:
    try:
        return TIER_ORDER.index(org_tier) >= TIER_ORDER.index(min_tier)
    except ValueError:
        return False
