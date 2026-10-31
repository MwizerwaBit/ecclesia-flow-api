"""The module catalogue exists twice: here (app/modules/org/catalogue.py) and in
the app (src/modules/registry.ts + types.ts). The two must agree on module ids,
the tier each starts at, which are core, and the tier order. This fails when
they drift. It skips when the app checkout is not next to the API.
"""

import re
from pathlib import Path

import pytest

from app.modules.org.catalogue import MODULES, TIER_ORDER

APP_MODULES = Path(__file__).resolve().parents[2] / "ecclesia-flow-app" / "src" / "modules"

pytestmark = pytest.mark.skipif(not APP_MODULES.exists(), reason="app checkout not found next to the API")


def _registry() -> dict[str, dict]:
    """id -> {min_tier, core} for each top-level entry in registry.ts."""
    text = (APP_MODULES / "registry.ts").read_text(encoding="utf-8")
    entries: dict[str, dict] = {}
    # Top-level entries are indented two spaces; nav/routes items are deeper.
    for block in re.split(r"\n  \{\n    id: '", text)[1:]:
        module_id = block.split("'", 1)[0]
        tier = re.search(r"^    minTier: '(\w+)'", block, re.M)
        entries[module_id] = {
            "min_tier": tier.group(1) if tier else None,
            "core": re.search(r"^    core: true", block, re.M) is not None,
        }
    return entries


def test_tier_order_matches():
    text = (APP_MODULES / "types.ts").read_text(encoding="utf-8")
    match = re.search(r"TIER_ORDER: OrgTier\[\] = \[(.*?)\]", text, re.S)
    assert match, "TIER_ORDER not found in types.ts"
    assert re.findall(r"'(\w+)'", match.group(1)) == TIER_ORDER


def test_module_id_type_matches():
    text = (APP_MODULES / "types.ts").read_text(encoding="utf-8")
    match = re.search(r"type ModuleId =(.*?);", text, re.S)
    assert match, "ModuleId not found in types.ts"
    assert set(re.findall(r"'(\w+)'", match.group(1))) == {m.id for m in MODULES}


def test_registry_matches_catalogue():
    registry = _registry()
    assert set(registry) == {m.id for m in MODULES}
    for m in MODULES:
        assert registry[m.id]["min_tier"] == m.min_tier, f"{m.id}: minTier differs"
        assert registry[m.id]["core"] == m.core, f"{m.id}: core flag differs"
