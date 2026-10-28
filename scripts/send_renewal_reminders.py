"""Sends the "plan ends soon" emails once. For a cron job or scheduled task
when the API's own background loop is switched off (RUN_BACKGROUND_JOBS=false).

    python -m scripts.send_renewal_reminders
"""

import asyncio

from app.core.database import connect_clients, disconnect_clients
from app.modules.billing.reminders import send_due_reminders


async def main() -> None:
    await connect_clients()
    try:
        print(f"sent {await send_due_reminders()} reminder(s)")
    finally:
        await disconnect_clients()


if __name__ == "__main__":
    asyncio.run(main())
