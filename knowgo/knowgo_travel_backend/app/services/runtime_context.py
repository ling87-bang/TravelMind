"""Task-local identifiers shared by chat persistence and capability updates."""
from contextvars import ContextVar

current_run: ContextVar[str | None] = ContextVar("current_run", default=None)
current_submission: ContextVar[str | None] = ContextVar("current_submission", default=None)
