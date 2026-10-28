"""Creates (or promotes) a platform administrator: the system-wide login.

There is deliberately no sign-up page or API for this. Run it once on the
server, with the production environment loaded:

    python -m scripts.create_platform_admin --email you@example.com

The password is asked for on the terminal (never on the command line, so it
doesn't land in shell history). If the email already belongs to a user, that
user is promoted and their password is left alone.

Afterwards sign in and set up two-step sign-in: approving payments needs it.
"""

import argparse
import asyncio
import getpass
import sys

from app.core.database import connect_clients, disconnect_clients, platform_session
from app.core.security import hash_password
from app.modules.audit.service import write_audit

MIN_PASSWORD_LENGTH = 12


async def main(email: str, first_name: str, last_name: str) -> int:
    email = email.strip().lower()
    await connect_clients()
    try:
        async with platform_session() as db:
            rows = await db.query_raw("select id::text from users where email = $1", email)
            if rows:
                user_id = rows[0]["id"]
                await db.execute_raw(
                    "update users set is_platform_admin = true, platform_admin_level = 'full' where id = $1::uuid",
                    user_id,
                )
                print(f"{email} already had a login: promoted to platform admin (password unchanged).")
            else:
                password = getpass.getpass("Password: ")
                if len(password) < MIN_PASSWORD_LENGTH:
                    print(f"Use at least {MIN_PASSWORD_LENGTH} characters.", file=sys.stderr)
                    return 1
                if getpass.getpass("Repeat password: ") != password:
                    print("The two passwords differ.", file=sys.stderr)
                    return 1
                user = await db.user.create(
                    data={
                        "email": email,
                        "password_hash": hash_password(password),
                        "first_name": first_name,
                        "last_name": last_name,
                        "is_platform_admin": True,
                        "platform_admin_level": "full",
                    }
                )
                user_id = user.id
                print(f"Created platform admin {email}.")
            await write_audit(
                db,
                tenant_id=None,
                actor_user_id=user_id,
                action="platform.admin_granted",
                resource_type="user",
                resource_id=user_id,
                metadata={"via": "scripts.create_platform_admin"},
            )
        print("Next: sign in, then turn on two-step sign-in in your account.")
        return 0
    finally:
        await disconnect_clients()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--email", required=True)
    parser.add_argument("--first-name", default="Platform")
    parser.add_argument("--last-name", default="Admin")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.email, args.first_name, args.last_name)))
