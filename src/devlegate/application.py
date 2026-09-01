"""Explicit application boundary for foreground Devlegate operations."""

from pathlib import Path

from devlegate.cli import (
    Devlegate,
    ExecutionPlan,
    StatusSnapshot,
)


class Application:
    """Invoke operational runtime actions without owning terminal presentation."""

    def __init__(self, env_file: Path, *, read_only: bool = False) -> None:
        self.runtime = Devlegate(env_file, read_only=read_only)

    def status(self) -> StatusSnapshot:
        """Return an immutable observation of the current runtime state."""
        return self.runtime.status_view()

    def plan(self) -> ExecutionPlan:
        """Return the read-only scheduling decision for the current state."""
        return self.runtime.plan_view()

    def run(self, once: bool = False) -> int:
        """Run the existing foreground polling/execution loop."""
        return self.runtime.run(once)

    def retry(self, ticket_id: str | None = None) -> int:
        """Authorize and run the existing explicit retry operation."""
        return self.runtime.retry(ticket_id)


DevlegateApplication = Application
