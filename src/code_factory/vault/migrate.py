from __future__ import annotations

from .models import RunRecord
from .storage import VaultStorage


def migrate(source: VaultStorage, dest: VaultStorage) -> list[str]:
    """Copy all tickets, stage files, and runs from source to dest. Returns migrated ticket IDs."""
    migrated = []
    for ticket_id in source.list_tickets():
        ticket = source.load_ticket(ticket_id)
        if not ticket:
            continue

        new_ticket = dest.create_ticket(
            ticket.title, ticket.requirements,
            tags=ticket.tags, host_functions=ticket.host_function_allowlist,
        )
        new_ticket.status = ticket.status
        new_ticket.input_schema = ticket.input_schema
        new_ticket.created_at = ticket.created_at
        new_ticket.updated_at = ticket.updated_at
        dest.save_ticket(new_ticket)

        for filename in source.list_stage_files(ticket_id):
            content = source.read_stage_file(ticket_id, filename)
            if content is not None:
                dest.write_stage_file(new_ticket.id, filename, content)

        for run_data in source.list_runs(ticket_id):
            dest.save_run(new_ticket.id, RunRecord.model_validate(run_data))

        dest.commit(new_ticket.id, f"migrated from {ticket_id}")
        migrated.append(new_ticket.id)
    return migrated
