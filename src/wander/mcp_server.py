from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from wander.mathoms import HippoClient
from wander.store import THREAD_LINK_PATTERN, ValidationError, WanderStore


store: WanderStore | None = None
hippo: HippoClient | None = None
mcp = MCPServer(
    "wander",
    version="0.8.0",
    instructions=(
        "Wander is the authoritative multi-thread work ledger. Read resume context before Journey work. "
        "Pass the last-read aggregate version to every mutation after creation. "
        "Record evidence before Journey completion. "
        "Mutation results are compact deltas; chain writes with their returned version. "
        "Jaunts are standalone, user-controlled lightweight tasks, not miniature Journeys. "
        "Create a Jaunt only when the user explicitly asks, and use its independent version for edits. "
        "Never emulate promotion by creating or labeling a Journey. "
        "Mathoms are request-only: never save one unless the user explicitly asks to "
        "preserve the specific knowledge in the current conversation."
    ),
)


def configure_mcp_server(
    application_store: WanderStore,
    hippo_client: HippoClient | None = None,
) -> MCPServer:
    """Inject the dependencies owned by the running Wander application."""
    global store, hippo
    if store is not None and store is not application_store:
        raise RuntimeError("Wander MCP is already attached to another application store")
    store = application_store
    hippo = hippo_client or HippoClient.from_environment()
    return mcp


def release_mcp_server(application_store: WanderStore) -> None:
    """Release an application store after its transports have stopped."""
    global store, hippo
    if store is application_store:
        store = None
        hippo = None


@mcp.tool()
def list_journeys(status: str | None = None) -> list[dict[str, Any]]:
    """List journeys, optionally filtered by active, paused, completed, or archived."""
    return store.list_journeys(status)


@mcp.tool()
def get_journey(journey: str) -> dict[str, Any]:
    """Get the full journey ledger, including work items, threads, checkpoint, and events."""
    return store.get_journey(journey)


@mcp.tool()
def get_resume_context(journey: str) -> dict[str, Any]:
    """Get compact state needed to resume a journey without reanalyzing its entire history."""
    return store.get_resume_context(journey)


@mcp.tool()
def list_jaunts(status: str | None = None) -> list[dict[str, Any]]:
    """List standalone Jaunts, optionally filtered by open, completed, or promoted."""
    return store.list_jaunts(status)


@mcp.tool()
def get_jaunt(jaunt_id: str) -> dict[str, Any]:
    """Get one standalone Jaunt with its retained event history."""
    return store.get_jaunt(jaunt_id)


@mcp.tool()
def create_jaunt(title: str, notes: str = "") -> dict[str, Any]:
    """Create a standalone Jaunt only when the user explicitly asks for one."""
    return store.create_jaunt(title, notes)


@mcp.tool()
def update_jaunt(
    jaunt_id: str,
    expected_version: int,
    title: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Edit an open Jaunt using the version returned by the last Jaunt read or mutation."""
    return store.update_jaunt(jaunt_id, expected_version, title, notes)


@mcp.tool()
def complete_jaunt(
    jaunt_id: str,
    expected_version: int,
    summary: str = "Completed jaunt",
) -> dict[str, Any]:
    """Complete an open Jaunt while retaining its notes and event history."""
    return store.complete_jaunt(jaunt_id, expected_version, summary)


@mcp.tool()
def reopen_jaunt(
    jaunt_id: str,
    expected_version: int,
    summary: str = "Reopened jaunt",
) -> dict[str, Any]:
    """Reopen a completed Jaunt so that it can be edited or completed again."""
    return store.reopen_jaunt(jaunt_id, expected_version, summary)


@mcp.tool()
async def save_mathom(
    source_thread_link: str,
    kind: str,
    predicate: str,
    statement: str,
    confidence: float,
    value: dict[str, Any] | None = None,
    entities: list[dict[str, Any]] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    effective_from: str | None = None,
    effective_to: str | None = None,
    agent_model: str | None = None,
) -> dict[str, Any]:
    """Save one user-requested Mathom in Hippo.

    Call this only after the user explicitly asks to save or remember the
    specific knowledge represented by these arguments. Never infer permission
    from usefulness, novelty, a completed Journey, or an earlier standing
    request. Pass the source chat deep link explicitly as ordinary provenance
    data. Clarify the claim or evidence with the user before calling when its
    meaning is ambiguous.
    """
    source_thread_link = source_thread_link.strip()
    if not THREAD_LINK_PATTERN.fullmatch(source_thread_link):
        raise ValidationError(
            "source_thread_link must have the form codex://threads/<thread-id>"
        )
    return await hippo.record_mathom_async(
        source_thread_link,
        kind,
        predicate,
        statement,
        confidence,
        value,
        entities,
        evidence,
        effective_from,
        effective_to,
        agent_model,
    )


@mcp.tool()
async def search_mathoms(
    query: str = "",
    entity_identifiers: list[str] | None = None,
    maximum_results: int = 12,
) -> dict[str, Any]:
    """Search knowledge saved by Wander as Mathoms in Hippo.

    An empty query lists recent Wander-sourced Mathoms. Entity identifiers may
    optionally constrain results. This read does not authorize a later write.
    """
    return await hippo.search_mathoms_async(query, entity_identifiers, maximum_results)


@mcp.tool()
def create_journey(slug: str, title: str, objective: str) -> dict[str, Any]:
    """Create an active journey. Slugs use lowercase letters, digits, and hyphens."""
    return store.create_journey(slug, title, objective)


@mcp.tool()
def add_journey_link(
    journey: str,
    expected_version: int,
    label: str,
    url: str,
    sequence: int | None = None,
) -> dict[str, Any]:
    """Add an ordered http(s) reference such as an issue, pull request, or build."""
    return store.add_journey_link(journey, expected_version, label, url, sequence)


@mcp.tool()
def remove_journey_link(
    journey: str,
    link_id: str,
    expected_version: int,
    reason: str,
) -> dict[str, Any]:
    """Remove an obsolete journey reference while preserving the reason in history."""
    return store.remove_journey_link(journey, link_id, expected_version, reason)


@mcp.tool()
def prepare_github_issue_projection(
    journey: str,
    workboard_url: str = "",
) -> dict[str, Any]:
    """Render the authoritative one-way GitHub issue-comment projection plan."""
    return store.prepare_github_issue_projection(journey, workboard_url)


@mcp.tool()
def register_github_projection_comment(
    journey: str,
    expected_version: int,
    comment_url: str,
) -> dict[str, Any]:
    """Record the one GitHub issue comment owned by this Wander journey."""
    return store.register_github_projection_comment(
        journey,
        expected_version,
        comment_url,
    )


@mcp.tool()
def add_work_item(
    journey: str,
    expected_version: int,
    title: str,
    completion_criteria: str = "",
    notes: str = "",
    parent_id: str | None = None,
    sequence: int | None = None,
) -> dict[str, Any]:
    """Add a pending work item while rejecting stale journey state."""
    return store.add_work_item(
        journey,
        expected_version,
        title,
        completion_criteria,
        notes,
        parent_id,
        sequence,
    )


@mcp.tool()
def start_work_item(
    journey: str,
    item_id: str,
    expected_version: int,
    summary: str,
) -> dict[str, Any]:
    """Move a pending or blocked work item to active."""
    return store.start_work_item(journey, item_id, expected_version, summary)


@mcp.tool()
def complete_work_item(
    journey: str,
    item_id: str,
    expected_version: int,
    evidence: str,
    summary: str,
    commit_urls: list[str] | None = None,
) -> dict[str, Any]:
    """Complete an active item and atomically attach any relevant GitHub commits."""
    return store.complete_work_item(
        journey,
        item_id,
        expected_version,
        evidence,
        summary,
        commit_urls,
    )


@mcp.tool()
def add_work_item_link(
    journey: str,
    item_id: str,
    expected_version: int,
    commit_url: str,
) -> dict[str, Any]:
    """Attach a GitHub commit discovered after a work item was completed."""
    return store.add_work_item_link(journey, item_id, expected_version, commit_url)


@mcp.tool()
def block_work_item(
    journey: str,
    item_id: str,
    expected_version: int,
    reason: str,
) -> dict[str, Any]:
    """Mark an active item blocked and preserve the reason."""
    return store.block_work_item(journey, item_id, expected_version, reason)


@mcp.tool()
def abandon_work_item(
    journey: str,
    item_id: str,
    expected_version: int,
    reason: str,
) -> dict[str, Any]:
    """Abandon a pending, active, or blocked item without deleting its history."""
    return store.abandon_work_item(journey, item_id, expected_version, reason)


@mcp.tool()
def record_checkpoint(
    journey: str,
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
        journey,
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
    journey: str,
    expected_version: int,
    deep_link: str,
    title: str,
    role: str,
) -> dict[str, Any]:
    """Attach or refresh a supported codex://threads/<thread-id> reference."""
    return store.attach_thread(journey, expected_version, deep_link, title, role)


@mcp.tool()
def set_journey_status(
    journey: str,
    expected_version: int,
    status: str,
    summary: str,
    evidence: str = "",
) -> dict[str, Any]:
    """Set journey status. Completing a journey requires evidence."""
    return store.set_journey_status(
        journey,
        expected_version,
        status,
        summary,
        evidence,
    )


def main() -> None:
    raise RuntimeError(
        "Wander MCP is hosted by the Wander desktop application. Start Wander instead."
    )


if __name__ == "__main__":
    main()

