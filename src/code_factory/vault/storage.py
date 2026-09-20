from __future__ import annotations

from abc import ABC, abstractmethod

from .models import RunRecord, StructuralMatch, Ticket, TicketSummary


class VaultStorage(ABC):
    """Abstract vault storage backend."""

    @abstractmethod
    def next_id(self) -> str: ...

    @abstractmethod
    def create_ticket(self, title: str, requirements: str, tags: list[str] | None = None,
                      host_functions: list[str] | None = None) -> Ticket: ...

    @abstractmethod
    def save_ticket(self, ticket: Ticket) -> None: ...

    @abstractmethod
    def load_ticket(self, ticket_id: str) -> Ticket | None: ...

    @abstractmethod
    def write_stage_file(self, ticket_id: str, filename: str, content: str) -> None: ...

    @abstractmethod
    def read_stage_file(self, ticket_id: str, filename: str) -> str | None: ...

    @abstractmethod
    def save_run(self, ticket_id: str, record: RunRecord) -> None: ...

    @abstractmethod
    def get_latest_run(self, ticket_id: str) -> dict | None:
        """Return latest run record as dict, or None."""
        ...

    @abstractmethod
    def commit(self, ticket_id: str, message: str) -> None: ...

    @abstractmethod
    def search(self, query: str, top_n: int = 5) -> list[TicketSummary]: ...

    @abstractmethod
    def search_structural(self, host_functions: list[str], input_keys: list[str],
                          top_n: int = 5) -> list[StructuralMatch]: ...

    @abstractmethod
    def list_tickets(self) -> list[str]:
        """Return all ticket IDs, sorted."""
        ...

    @abstractmethod
    def list_stage_files(self, ticket_id: str) -> list[str]:
        """Return filenames of stage files for a ticket."""
        ...

    @abstractmethod
    def list_runs(self, ticket_id: str) -> list[dict]:
        """Return all run records as dicts, oldest first."""
        ...
