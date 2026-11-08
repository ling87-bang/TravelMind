"""Idempotent additive migration for databases created before durable chat runs."""
from sqlalchemy import text


async def migrate_chat_runtime(connection):
    await connection.execute(text("ALTER TABLE message ADD COLUMN IF NOT EXISTS run_id UUID"))
    await connection.execute(text("ALTER TABLE itinerary_plans ADD COLUMN IF NOT EXISTS run_id UUID"))
    await connection.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_message_run_role ON message(run_id, role) "
        "WHERE run_id IS NOT NULL"
    ))
    await connection.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_itinerary_plan_run ON itinerary_plans(run_id) "
        "WHERE run_id IS NOT NULL"
    ))
