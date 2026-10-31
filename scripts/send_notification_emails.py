"""Sends the email copy of pending notifications once. For a cron job or
scheduled task when the API's own background loop is switched off
(RUN_BACKGROUND_JOBS=false); safe to run alongside the loop.

    python -m scripts.send_notification_emails
"""

import asyncio

from app.core.database import connect_clients, disconnect_clients
from app.modules.notifications.delivery import BATCH_SIZE, deliver_pending


async def main() -> None:
    await connect_clients()
    try:
        while True:
            stats = await deliver_pending()
            print(stats)
            if stats.claimed < BATCH_SIZE:
                break
    finally:
        await disconnect_clients()


if __name__ == "__main__":
    asyncio.run(main())
