"""How long a signed-in session may sit idle before the app signs it out.

Leader-side sessions (leader, administrators, staff, custom roles) use the
church's own setting, because they can see and change everyone's records.
Members always get an hour. Platform admins, who can reach every church, get
fifteen minutes and no setting to loosen it.
"""

DEFAULT_LEADER_IDLE_MINUTES = 15
MIN_IDLE_MINUTES = 5
MAX_IDLE_MINUTES = 120
MEMBER_IDLE_MINUTES = 60
PLATFORM_IDLE_MINUTES = 15


def idle_minutes(*, is_platform_admin: bool, is_member_role: bool, church_minutes: int | None) -> int:
    if is_platform_admin:
        return PLATFORM_IDLE_MINUTES
    if is_member_role:
        return MEMBER_IDLE_MINUTES
    minutes = church_minutes if church_minutes is not None else DEFAULT_LEADER_IDLE_MINUTES
    return min(max(minutes, MIN_IDLE_MINUTES), MAX_IDLE_MINUTES)
