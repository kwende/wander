from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse


PROJECTION_LINK_LABEL = "Wander GitHub projection"
ISSUE_PATH_PATTERN = re.compile(
    r"^/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/issues/(?P<number>[1-9][0-9]*)/?$"
)
COMMENT_FRAGMENT_PATTERN = re.compile(r"^issuecomment-(?P<comment_id>[1-9][0-9]*)$")


class GitHubProjectionError(ValueError):
    """Raised when a journey cannot produce one unambiguous GitHub projection."""


def parse_github_issue_url(url: str) -> dict[str, Any] | None:
    parsed = urlparse(url.strip())
    match = ISSUE_PATH_PATTERN.fullmatch(parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.params
        or parsed.query
        or parsed.fragment
        or match is None
    ):
        return None
    return {
        "owner": match.group("owner"),
        "repo": match.group("repo"),
        "number": int(match.group("number")),
        "url": url.strip(),
    }


def parse_github_issue_comment_url(url: str) -> dict[str, Any] | None:
    parsed = urlparse(url.strip())
    issue_match = ISSUE_PATH_PATTERN.fullmatch(parsed.path)
    comment_match = COMMENT_FRAGMENT_PATTERN.fullmatch(parsed.fragment)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.params
        or parsed.query
        or issue_match is None
        or comment_match is None
    ):
        return None
    return {
        "owner": issue_match.group("owner"),
        "repo": issue_match.group("repo"),
        "number": int(issue_match.group("number")),
        "comment_id": int(comment_match.group("comment_id")),
        "url": url.strip(),
    }


def projection_marker(journey_slug: str) -> str:
    # This is a stable protocol marker, not user-facing domain language. Keep the
    # legacy key so an interrupted pre-migration registration is recovered rather
    # than mistaken for a missing comment and duplicated.
    return f"<!-- wander:projection project={journey_slug} -->"


def _inline_markdown(value: str) -> str:
    normalized = " ".join(value.split())
    for character in ("\\", "[", "]", "*", "_", "`", "~"):
        normalized = normalized.replace(character, f"\\{character}")
    return normalized


def _append_commit_links(item: dict[str, Any], text: str) -> str:
    links = item.get("links", [])
    if not links:
        return text
    rendered = ", ".join(
        f"[{_inline_markdown(link['label'])}]({link['url']})" for link in links
    )
    return f"{text} ({rendered})"


def _render_item(item: dict[str, Any], depth: int) -> str:
    indent = "  " * depth
    title = _inline_markdown(item["title"])
    status = item["status"]
    if status == "complete":
        line = f"{indent}- [x] {title}"
        line = _append_commit_links(item, line)
    elif status == "active":
        line = f"{indent}- [ ] **{title}** — **active**"
    elif status == "blocked":
        line = f"{indent}- [ ] {title} — **blocked**"
    elif status == "abandoned":
        line = f"{indent}- ~~{title}~~ — *deferred / not planned*"
    else:
        line = f"{indent}- [ ] {title}"
    return f"{line} <!-- wander:item={item['id']} -->"


def _render_work_items(work_items: list[dict[str, Any]]) -> list[str]:
    children: dict[str | None, list[dict[str, Any]]] = {}
    for item in work_items:
        children.setdefault(item["parent_id"], []).append(item)

    lines: list[str] = []

    def render(parent_id: str | None, depth: int) -> None:
        for item in children.get(parent_id, []):
            lines.append(_render_item(item, depth))
            render(item["id"], depth + 1)

    render(None, 0)
    return lines


def _validate_workboard_url(workboard_url: str) -> str:
    normalized = workboard_url.strip()
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise GitHubProjectionError("workboard_url must be an absolute http:// or https:// URL")
    return normalized


def build_github_issue_projection(
    journey: dict[str, Any],
    workboard_url: str = "",
) -> dict[str, Any]:
    issue_targets = [
        parsed
        for link in journey.get("links", [])
        if (parsed := parse_github_issue_url(link["url"])) is not None
    ]
    if len(issue_targets) != 1:
        raise GitHubProjectionError(
            "journey must contain exactly one GitHub issue link before it can be projected"
        )
    issue = issue_targets[0]

    comment_targets = [
        parsed
        for link in journey.get("links", [])
        if link["label"] == PROJECTION_LINK_LABEL
        and (parsed := parse_github_issue_comment_url(link["url"])) is not None
    ]
    if len(comment_targets) > 1:
        raise GitHubProjectionError("journey contains more than one Wander projection comment")
    comment = comment_targets[0] if comment_targets else None
    if comment and (
        comment["owner"].casefold() != issue["owner"].casefold()
        or comment["repo"].casefold() != issue["repo"].casefold()
        or comment["number"] != issue["number"]
    ):
        raise GitHubProjectionError(
            "Wander projection comment must belong to the configured GitHub issue"
        )

    items = journey.get("work_items", [])
    actionable_items = [item for item in items if item["status"] != "abandoned"]
    completed_items = [item for item in actionable_items if item["status"] == "complete"]
    safe_workboard_url = _validate_workboard_url(workboard_url)
    marker = projection_marker(journey["slug"])

    body_lines = [
        marker,
        "",
        f"## Wander progress: {_inline_markdown(journey['title'])}",
        "",
        "> This comment is generated from Wander, which is the source of truth. "
        "GitHub checkbox edits are display-only and never update Wander.",
        "",
        f"**Journey status:** {_inline_markdown(journey['status'])}",
    ]
    if journey.get("current_phase"):
        body_lines.append(
            f"**Current phase:** {_inline_markdown(journey['current_phase'])}"
        )
    body_lines.extend(
        [
            f"**Progress:** {len(completed_items)} of {len(actionable_items)} work items complete",
            "",
        ]
    )
    rendered_items = _render_work_items(items)
    body_lines.extend(rendered_items or ["_No work items recorded._"])
    if safe_workboard_url:
        body_lines.extend(["", f"[Open the live Wander workboard]({safe_workboard_url})"])
    body_lines.extend(
        [
            "",
            f"_Last synchronized from Wander journey version `{journey['version']}`._",
            "",
            "<!-- wander:projection:end -->",
        ]
    )

    return {
        "journey": journey["slug"],
        "journey_version": journey["version"],
        "issue": issue,
        "comment": comment,
        "operation": "update" if comment else "create",
        "marker": marker,
        "comment_body": "\n".join(body_lines),
    }
