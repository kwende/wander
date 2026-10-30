from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from wander.store import THREAD_LINK_PATTERN, ValidationError, WanderStore


def default_database_path() -> Path:
    configured = os.environ.get("WANDER_DB")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "data" / "wander.db"


def current_thread_id() -> str | None:
    host_thread_id = os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SESSION_ID")
    supplied_link = os.environ.get("WANDER_THREAD_LINK", "").strip()
    supplied_thread_id = None
    if supplied_link:
        if not THREAD_LINK_PATTERN.fullmatch(supplied_link):
            raise ValidationError(
                "WANDER_THREAD_LINK must have the form codex://threads/<thread-id>"
            )
        supplied_thread_id = supplied_link.removeprefix("codex://threads/")
    if host_thread_id and supplied_thread_id and host_thread_id != supplied_thread_id:
        raise ValidationError(
            "WANDER_THREAD_LINK does not match the host-provided CODEX_THREAD_ID"
        )
    return host_thread_id or supplied_thread_id


store = WanderStore(default_database_path(), current_thread_id())
mcp = MCPServer(
    "wander",
    version="0.1.1",
    instructions=(
        "Wander is the authoritative multi-thread work ledger. Read resume context before work. "
        "Identify the current Codex thread before the first mutation. "
        "Pass the last-read project version to every mutation. Record evidence before completion. "
        "If writer_warning is non-empty, surface it prominently; it is a soft ownership bumper."
    ),
)


@mcp.tool()
def list_projects(status: str | None = None) -> list[dict[str, Any]]:
    """List projects, optionally filtered by active, paused, completed, or archived."""
    return store.list_projects(status)


@mcp.tool()
def get_project(project: str) -> dict[str, Any]:
    """Get the full project ledger, including work items, threads, checkpoint, and events."""
    return store.get_project(project)


@mcp.tool()
def get_resume_context(project: str) -> dict[str, Any]:
    """Get compact state needed to resume a project without reanalyzing its entire history."""
    return store.get_resume_context(project)


@mcp.tool()
def identify_thread(deep_link: str) -> dict[str, str]:
    """Bind this MCP session to one supported codex://threads/<thread-id> identity."""
    return store.identify_writer_thread(deep_link)


@mcp.tool()
def create_project(slug: str, title: str, objective: str) -> dict[str, Any]:
    """Create an active project. Slugs use lowercase letters, digits, and hyphens."""
    return store.create_project(slug, title, objective)


@mcp.tool()
def claim_project(project: str, expected_version: int) -> dict[str, Any]:
    """Designate this Codex thread as the project's center. Existing ownership is preserved."""
    return store.claim_project(project, expected_version)


@mcp.tool()
def release_project(project: str, expected_version: int, reason: str) -> dict[str, Any]:
    """Clear the writer-thread indicator so another Codex thread may claim the project."""
    return store.release_project(project, expected_version, reason)


@mcp.tool()
def add_project_link(
    project: str,
    expected_version: int,
    label: str,
    url: str,
    sequence: int | None = None,
) -> dict[str, Any]:
    """Add an ordered http(s) reference such as an issue, pull request, or build."""
    return store.add_project_link(project, expected_version, label, url, sequence)


@mcp.tool()
def remove_project_link(
    project: str,
    link_id: str,
    expected_version: int,
    reason: str,
) -> dict[str, Any]:
    """Remove an obsolete project reference while preserving the reason in history."""
    return store.remove_project_link(project, link_id, expected_version, reason)


@mcp.tool()
def add_work_item(
    project: str,
    expected_version: int,
    title: str,
    completion_criteria: str = "",
    notes: str = "",
    parent_id: str | None = None,
    sequence: int | None = None,
) -> dict[str, Any]:
    """Add a pending work item while rejecting stale project state."""
    return store.add_work_item(
        project,
        expected_version,
        title,
        completion_criteria,
        notes,
        parent_id,
        sequence,
    )


@mcp.tool()
def start_work_item(
    project: str,
    item_id: str,
    expected_version: int,
    summary: str,
) -> dict[str, Any]:
    """Move a pending or blocked work item to active."""
    return store.start_work_item(project, item_id, expected_version, summary)


@mcp.tool()
def complete_work_item(
    project: str,
    item_id: str,
    expected_version: int,
    evidence: str,
    summary: str,
) -> dict[str, Any]:
    """Complete an active work item. Concrete completion evidence is mandatory."""
    return store.complete_work_item(
        project,
        item_id,
        expected_version,
        evidence,
        summary,
    )


@mcp.tool()
def block_work_item(
    project: str,
    item_id: str,
    expected_version: int,
    reason: str,
) -> dict[str, Any]:
    """Mark an active item blocked and preserve the reason."""
    return store.block_work_item(project, item_id, expected_version, reason)


@mcp.tool()
def abandon_work_item(
    project: str,
    item_id: str,
    expected_version: int,
    reason: str,
) -> dict[str, Any]:
    """Abandon a pending, active, or blocked item without deleting its history."""
    return store.abandon_work_item(project, item_id, expected_version, reason)


@mcp.tool()
def record_checkpoint(
    project: str,
    expected_version: int,
    current_summary: str,
    current_phase: str,
    current_model: str,
    last_decision: str,
    invariants: str,
    open_questions: str,
    resume_here: str,
) -> dict[str, Any]:
    """Record a compact context-rehydration checkpoint after a meaningful work batch."""
    return store.record_checkpoint(
        project,
        expected_version,
        current_summary,
        current_phase,
        current_model,
        last_decision,
        invariants,
        open_questions,
        resume_here,
    )


@mcp.tool()
def attach_thread(
    project: str,
    expected_version: int,
    deep_link: str,
    title: str,
    role: str,
) -> dict[str, Any]:
    """Attach or refresh a supported codex://threads/<thread-id> reference."""
    return store.attach_thread(project, expected_version, deep_link, title, role)


@mcp.tool()
def attach_current_thread(
    project: str,
    expected_version: int,
    title: str,
    role: str,
) -> dict[str, Any]:
    """Attach the current Codex conversation using its host-provided thread identity."""
    if not store.writer_thread_link:
        raise ValidationError(
            "The current thread identity is unavailable. Ask the user to supply a "
            "codex://threads/<thread-id> deep link and use attach_thread instead."
        )
    return store.attach_thread(
        project,
        expected_version,
        store.writer_thread_link,
        title,
        role,
    )


@mcp.tool()
def set_project_status(
    project: str,
    expected_version: int,
    status: str,
    summary: str,
    evidence: str = "",
) -> dict[str, Any]:
    """Set project status. Completing a project requires evidence."""
    return store.set_project_status(
        project,
        expected_version,
        status,
        summary,
        evidence,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

