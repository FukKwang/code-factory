from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TicketStatus(str, Enum):
    NEW = "new"
    SEARCHING = "searching"
    GATHERING = "gathering_requirements"
    SPEC_DRAFTED = "spec_drafted"
    TESTS_DRAFTED = "tests_drafted"
    CODE_DRAFTED = "code_drafted"
    VALIDATING = "validating"
    ITERATING = "iterating"
    APPROVED = "approved"
    CLOSED = "closed"
    REUSED = "reused"


class Ticket(BaseModel):
    id: str
    title: str
    requirements: str
    tags: list[str] = []
    status: TicketStatus = TicketStatus.NEW
    host_function_allowlist: list[str] = []
    input_schema: dict[str, str] = {}
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class TicketSummary(BaseModel):
    id: str
    title: str
    tags: list[str] = []
    summary: str = ""
    status: TicketStatus = TicketStatus.NEW
    host_functions: list[str] = []
    input_keys: list[str] = []


class StructuralMatch(BaseModel):
    ticket: TicketSummary
    fn_overlap: float = 0.0
    input_overlap: float = 0.0
    match_type: str = "none"


class RunRecord(BaseModel):
    inputs: dict[str, Any] = {}
    output: Any = None
    success: bool = False
    error: str | None = None
    at: datetime = Field(default_factory=datetime.now)
