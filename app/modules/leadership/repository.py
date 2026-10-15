from prisma import Prisma


async def list_positions(db: Prisma) -> list[dict]:
    return await db.query_raw(
        """
        select p.id, p.parent_id, p.title, p.description, p.role_id, r.name as role_name,
               p.unit_id, hu.name as unit_name, p.sort_order
        from leadership_positions p
        left join roles r on r.id = p.role_id
        left join hierarchy_units hu on hu.id = p.unit_id
        order by p.sort_order, p.title, p.id
        """
    )


async def list_holders(db: Prisma) -> list[dict]:
    return await db.query_raw(
        """
        select a.position_id, a.membership_id, a.assigned_at, u.id as user_id, u.first_name, u.last_name,
               u.email, u.photo_url
        from leadership_assignments a
        join tenant_memberships tm on tm.id = a.membership_id
        join users u on u.id = tm.user_id
        order by u.last_name, u.first_name
        """
    )


async def get_position(db: Prisma, position_id: str):
    return await db.leadershipposition.find_unique(where={"id": position_id})


async def create_position(db: Prisma, data: dict):
    return await db.leadershipposition.create(data=data)


async def update_position(db: Prisma, position_id: str, data: dict) -> None:
    await db.leadershipposition.update(where={"id": position_id}, data=data)


async def delete_position(db: Prisma, position_id: str) -> None:
    await db.leadershipposition.delete(where={"id": position_id})


async def count_children(db: Prisma, position_id: str) -> int:
    return await db.leadershipposition.count(where={"parent_id": position_id})


async def list_assignments(db: Prisma, position_id: str) -> list:
    return await db.leadershipassignment.find_many(where={"position_id": position_id})


async def get_assignment_for_membership(db: Prisma, membership_id: str):
    return await db.leadershipassignment.find_unique(where={"membership_id": membership_id})


async def find_assignment(db: Prisma, position_id: str, membership_id: str):
    return await db.leadershipassignment.find_first(where={"position_id": position_id, "membership_id": membership_id})


async def create_assignment(db: Prisma, data: dict) -> None:
    await db.leadershipassignment.create(data=data)


async def delete_assignment(db: Prisma, assignment_id: str) -> None:
    await db.leadershipassignment.delete(where={"id": assignment_id})
