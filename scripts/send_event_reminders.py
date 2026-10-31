"""Sends the "starting soon" notifications for gatherings once. For a cron job or
scheduled task when the API's own background loop is switched off
(RUN_BACKGROUND_JOBS=false); safe to run alongside the loop. Run it every minute or two:
the hour-before reminder is only as punctual as the schedule.

    python -m scripts.send_event_reminders
"""

import asyncio

from app.core.database import connect_clients, disconnect_clients
from app.modules.activity.reminders import send_due_event_reminders


async def main() -> None:
    await connect_clients()
    try:
        print(f"created {await send_due_event_reminders()} reminder notification(s)")
    finally:
        await disconnect_clients()


if __name__ == "__main__":
    asyncio.run(main())
