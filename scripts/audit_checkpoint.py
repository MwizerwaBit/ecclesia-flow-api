"""Sign and verify audit-log checkpoints (see app/modules/audit/integrity.py).

    AUDIT_CHECKPOINT_KEY=... python scripts/audit_checkpoint.py create > checkpoint-2026-10-09.json
    AUDIT_CHECKPOINT_KEY=... python scripts/audit_checkpoint.py verify checkpoint-2026-10-09.json

Connects as app_platform (DATABASE_URL_PLATFORM), which may run the
verification functions but, like every app role, can't modify audit_logs.

The key must NOT live in the database or in the API's .env: whoever can
change the database must not also hold the key. Store checkpoint files
somewhere append-only or outside the database host (FORYOU.md).

Exit code 0 = intact, 1 = problems found, 2 = usage error.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from prisma import Prisma

from app.core.config import get_settings
from app.modules.audit.integrity import create_checkpoint, verify_checkpoint


async def main(argv: list[str]) -> int:
    key = os.environ.get("AUDIT_CHECKPOINT_KEY", "")
    if len(key) < 32 or len(argv) < 2 or argv[1] not in ("create", "verify"):
        print(__doc__, file=sys.stderr)
        print("AUDIT_CHECKPOINT_KEY must be set (32+ characters).", file=sys.stderr)
        return 2
    db = Prisma(datasource={"url": get_settings().database_url_platform})
    await db.connect()
    try:
        if argv[1] == "create":
            print(json.dumps(await create_checkpoint(db, key), indent=2))
            return 0
        if len(argv) < 3:
            print("verify needs a checkpoint file", file=sys.stderr)
            return 2
        checkpoint = json.loads(await asyncio.to_thread(Path(argv[2]).read_text, encoding="utf-8"))
        problems = await verify_checkpoint(db, key, checkpoint)
        for problem in problems:
            print("TAMPERING:", problem)
        if not problems:
            print(f"OK — {len(checkpoint['heads'])} chains intact since {checkpoint['created_at']}")
        return 1 if problems else 0
    finally:
        await db.disconnect()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv)))
