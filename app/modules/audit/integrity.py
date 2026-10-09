"""Audit-log integrity checks (DIF-08).

The database keeps one hash chain per organisation (plus one for
account-level events) and refuses updates and deletes. That stops the app
roles, and makes tampering by the database owner visible: they would have
to disable a trigger and rewrite every later hash.

A **checkpoint** closes the last gap. It records each chain's head
(sequence number and hash) and signs it with ``AUDIT_CHECKPOINT_KEY``, a key
that is never stored in the database or the app's ``.env``. Later,
``verify_checkpoint`` checks that every chain is still internally
consistent **and** still contains each checkpointed entry with the same
hash. Even someone who rewrites a whole chain with owner access can't
produce a matching chain without the key, and can't change history that
was already checkpointed.

Run through ``scripts/audit_checkpoint.py``; keep the checkpoint files
somewhere the database owner can't edit (FORYOU.md).
"""

import hashlib
import hmac
import json
from datetime import UTC, datetime

from prisma import Prisma


def _canonical(heads: list[dict]) -> bytes:
    return json.dumps(heads, sort_keys=True, separators=(",", ":")).encode()


def _sign(key: str, heads: list[dict]) -> str:
    return hmac.new(key.encode(), _canonical(heads), hashlib.sha256).hexdigest()


async def chain_breaks(db: Prisma, tenant_id: str | None) -> list[dict]:
    """[] when the chain is intact, else the first break."""
    return await db.query_raw("select * from verify_audit_chain($1::uuid)", tenant_id)


async def create_checkpoint(db: Prisma, key: str, tenant_ids: list[str] | None = None) -> dict:
    """All chains by default; ``tenant_ids`` limits it to some organisations.
    Run on a plain client, not inside a short interactive transaction: with
    many organisations, verification takes a while."""
    rows = await db.query_raw("select tenant_id, chain_seq, row_hash from audit_chain_heads()")
    if tenant_ids is not None:
        rows = [r for r in rows if r["tenant_id"] in tenant_ids]
    heads = sorted(
        ({"tenant_id": r["tenant_id"], "chain_seq": int(r["chain_seq"]), "row_hash": r["row_hash"]} for r in rows),
        key=lambda h: h["tenant_id"] or "",
    )
    return {"created_at": datetime.now(UTC).isoformat(), "heads": heads, "signature": _sign(key, heads)}


async def verify_checkpoint(db: Prisma, key: str, checkpoint: dict) -> list[str]:
    """Problems found ([] means the log is intact up to and since the checkpoint)."""
    problems: list[str] = []
    if not hmac.compare_digest(_sign(key, checkpoint["heads"]), checkpoint.get("signature", "")):
        return ["checkpoint signature is invalid — the checkpoint file itself was altered or the key is wrong"]
    for head in checkpoint["heads"]:
        label = head["tenant_id"] or "account events"
        for brk in await chain_breaks(db, head["tenant_id"]):
            problems.append(f"{label}: chain broken at #{brk['broken_at_seq']} ({brk['reason']})")
        rows = await db.query_raw(
            "select row_hash from audit_logs where tenant_id is not distinct from $1::uuid and chain_seq = $2",
            head["tenant_id"],
            head["chain_seq"],
        )
        if not rows:
            problems.append(f"{label}: checkpointed entry #{head['chain_seq']} is missing")
        elif rows[0]["row_hash"] != head["row_hash"]:
            problems.append(f"{label}: checkpointed entry #{head['chain_seq']} has a different hash")
    return problems
