from __future__ import annotations

import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

from wander.github_projection import (
    PROJECTION_LINK_LABEL,
    GitHubProjectionError,
    build_github_issue_projection,
    parse_github_issue_comment_url,
)


PROJECT_STATUSES = {"active", "paused", "completed", "archived"}
PROJECT_TRANSITIONS = {
    "active": {"paused", "completed", "archived"},
    "paused": {"active", "completed", "archived"},
    "completed": {"archived"},
    "archived": set(),
}
ITEM_STATUSES = {"pending", "active", "blocked", "complete", "abandoned"}
ITEM_TRANSITIONS = {
    "pending": {"active", "abandoned"},
    "active": {"blocked", "complete", "abandoned"},
    "blocked": {"active", "abandoned"},
    "complete": set(),
    "abandoned": set(),
}
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
THREAD_LINK_PATTERN = re.compile(r"^codex://threads/[0-9a-f-]+$", re.IGNORECASE)
COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{7,64}$", re.IGNORECASE)


class WanderError(Exception):
    """Base error for expected Wander failures."""


class NotFoundError(WanderError):
    pass


class ConflictError(WanderError):
    pass


class OwnershipError(WanderError):
    pass


class ValidationError(WanderError):
    pass


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class WanderStore:
    def __init__(self, database_path: str | Path, writer_thread_id: str | None = None):
        self.database_path = Path(database_path).resolve()
        self.writer_thread_id = writer_thread_id.strip() if writer_thread_id else None
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @property
    def writer_thread_link(self) -> str:
        return f"codex://threads/{self.writer_thread_id}" if self.writer_thread_id else ""

    def identify_writer_thread(self, deep_link: str) -> dict[str, str]:
        deep_link = deep_link.strip()
        if not THREAD_LINK_PATTERN.fullmatch(deep_link):
            raise ValidationError("deep_link must have the form codex://threads/<thread-id>")
        thread_id = deep_link.removeprefix("codex://threads/")
        if self.writer_thread_id and self.writer_thread_id.casefold() != thread_id.casefold():
            raise OwnershipError(
                "This Wander MCP session is already bound to a different Codex thread"
            )
        self.writer_thread_id = thread_id
        return {"thread_link": self.writer_thread_link, "status": "identified"}

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('active', 'paused', 'completed', 'archived')),
                    current_summary TEXT NOT NULL DEFAULT '',
                    current_phase TEXT NOT NULL DEFAULT '',
                    owner_thread_id TEXT,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS work_items (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    parent_id TEXT REFERENCES work_items(id),
                    sequence INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'blocked', 'complete', 'abandoned')),
                    completion_criteria TEXT NOT NULL DEFAULT '',
                    completion_evidence TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS ix_work_items_project_sequence
                    ON work_items(project_id, sequence);

                CREATE TABLE IF NOT EXISTS project_links (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    label TEXT NOT NULL,
                    url TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, url)
                );

                CREATE INDEX IF NOT EXISTS ix_project_links_project_sequence
                    ON project_links(project_id, sequence);

                CREATE TABLE IF NOT EXISTS work_item_links (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    work_item_id TEXT NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
                    label TEXT NOT NULL,
                    url TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(work_item_id, url)
                );

                CREATE INDEX IF NOT EXISTS ix_work_item_links_item_sequence
                    ON work_item_links(work_item_id, sequence);

                CREATE TABLE IF NOT EXISTS thread_references (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    deep_link TEXT NOT NULL,
                    title TEXT NOT NULL,
                    role TEXT NOT NULL,
                    last_activity TEXT NOT NULL,
                    UNIQUE(project_id, deep_link)
                );

                CREATE TABLE IF NOT EXISTS checkpoints (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    current_model TEXT NOT NULL,
                    last_decision TEXT NOT NULL,
                    invariants TEXT NOT NULL,
                    open_questions TEXT NOT NULL,
                    resume_here TEXT NOT NULL,
                    source_thread TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS ix_checkpoints_project_created
                    ON checkpoints(project_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    operation TEXT NOT NULL,
                    before_version INTEGER NOT NULL,
                    after_version INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    evidence TEXT NOT NULL DEFAULT '',
                    source_thread TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS ix_events_project_id
                    ON events(project_id, id DESC);
                """
            )
            project_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(projects)").fetchall()
            }
            if "owner_thread_id" not in project_columns:
                connection.execute("ALTER TABLE projects ADD COLUMN owner_thread_id TEXT")
            connection.commit()

    @staticmethod
    def _required(value: str, field: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValidationError(f"{field} is required")
        return normalized

    @staticmethod
    def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def _github_commit_link(url: str) -> tuple[str, str]:
        normalized = url.strip()
        parsed_url = urlparse(normalized)
        path_parts = [part for part in parsed_url.path.split("/") if part]
        if (
            parsed_url.scheme != "https"
            or parsed_url.hostname != "github.com"
            or len(path_parts) != 4
            or path_parts[2] != "commit"
            or not COMMIT_SHA_PATTERN.fullmatch(path_parts[3])
            or parsed_url.params
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValidationError(
                "commit URL must have the form "
                "https://github.com/<owner>/<repository>/commit/<sha>"
            )
        return normalized, path_parts[3][:12]

    def _project_row(self, connection: sqlite3.Connection, project: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM projects WHERE id = ? OR slug = ?",
            (project, project),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Project not found: {project}")
        return row

    def _locked_project(
        self,
        connection: sqlite3.Connection,
        project: str,
        expected_version: int,
    ) -> sqlite3.Row:
        row = self._project_row(connection, project)
        if row["version"] != expected_version:
            raise ConflictError(
                f"Stale project version for {row['slug']}: expected {expected_version}, "
                f"current {row['version']}. Reload and reconcile before writing."
            )
        return row

    def _writer_warning(self, project_row: sqlite3.Row) -> str:
        if not self.writer_thread_id:
            return ""
        if not project_row["owner_thread_id"]:
            return (
                f"Project '{project_row['slug']}' has no designated writer thread. "
                "The write succeeded, but consider claiming it to keep the work centered."
            )
        if project_row["owner_thread_id"] != self.writer_thread_id:
            owner_link = f"codex://threads/{project_row['owner_thread_id']}"
            return (
                f"Project '{project_row['slug']}' is centered in another Codex thread: "
                f"{owner_link}. This write succeeded, but review that conversation to avoid drift."
            )
        return ""

    def _advance(
        self,
        connection: sqlite3.Connection,
        project_row: sqlite3.Row,
        operation: str,
        summary: str,
        evidence: str = "",
        project_updates: dict[str, Any] | None = None,
    ) -> int:
        before_version = project_row["version"]
        after_version = before_version + 1
        now = utc_now()
        updates = {"version": after_version, "updated_at": now}
        updates.update(project_updates or {})
        set_clause = ", ".join(f"{name} = ?" for name in updates)
        connection.execute(
            f"UPDATE projects SET {set_clause} WHERE id = ?",  # noqa: S608 - column names are internal constants
            (*updates.values(), project_row["id"]),
        )
        connection.execute(
            """
            INSERT INTO events (
                project_id, operation, before_version, after_version,
                summary, evidence, source_thread, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_row["id"],
                operation,
                before_version,
                after_version,
                summary.strip(),
                evidence.strip(),
                self.writer_thread_link,
                now,
            ),
        )
        return after_version

    def _mutation_result(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        operation: str,
        changed: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        project_row = connection.execute(
            "SELECT * FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if project_row is None:
            raise NotFoundError(f"Project not found after mutation: {project_id}")
        return {
            "project": project_row["slug"],
            "version": project_row["version"],
            "operation": operation,
            "changed": {
                entity: identifiers
                for entity, identifiers in (changed or {}).items()
                if identifiers
            },
            "writer_warning": self._writer_warning(project_row),
        }

    def create_project(self, slug: str, title: str, objective: str) -> dict[str, Any]:
        if not self.writer_thread_id:
            raise OwnershipError("CODEX_THREAD_ID is required to create a project")
        slug = slug.strip().lower()
        if not SLUG_PATTERN.fullmatch(slug):
            raise ValidationError("slug must contain lowercase letters, digits, and single hyphens")
        title = self._required(title, "title")
        objective = self._required(objective, "objective")
        project_id = str(uuid.uuid4())
        now = utc_now()
        with self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO projects (
                        id, slug, title, objective, status, owner_thread_id,
                        version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'active', ?, 1, ?, ?)
                    """,
                    (project_id, slug, title, objective, self.writer_thread_id, now, now),
                )
            except sqlite3.IntegrityError as error:
                raise ConflictError(f"A project with slug '{slug}' already exists") from error
            connection.execute(
                """
                INSERT INTO events (
                    project_id, operation, before_version, after_version,
                    summary, source_thread, created_at
                ) VALUES (?, 'project.created', 0, 1, ?, ?, ?)
                """,
                (project_id, f"Created project: {title}", self.writer_thread_link, now),
            )
            result = self._mutation_result(
                connection,
                project_id,
                "project.created",
                {"project_ids": [project_id]},
            )
            connection.commit()
        return result

    def list_projects(self, status: str | None = None) -> list[dict[str, Any]]:
        if status is not None and status not in PROJECT_STATUSES:
            raise ValidationError(f"Unknown project status: {status}")
        query = "SELECT * FROM projects"
        parameters: tuple[Any, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            parameters = (status,)
        query += " ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END, updated_at DESC"
        with self._connection() as connection:
            return [dict(row) for row in connection.execute(query, parameters).fetchall()]

    def get_project(self, project: str) -> dict[str, Any]:
        with self._connection() as connection:
            project_row = self._project_row(connection, project)
            result = dict(project_row)
            result["writer_warning"] = self._writer_warning(project_row)
            result["work_items"] = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM work_items WHERE project_id = ? ORDER BY sequence, created_at",
                    (project_row["id"],),
                ).fetchall()
            ]
            links_by_item: dict[str, list[dict[str, Any]]] = {}
            for row in connection.execute(
                """
                SELECT * FROM work_item_links
                WHERE project_id = ?
                ORDER BY sequence, created_at
                """,
                (project_row["id"],),
            ).fetchall():
                link = dict(row)
                links_by_item.setdefault(link["work_item_id"], []).append(link)
            for item in result["work_items"]:
                item["links"] = links_by_item.get(item["id"], [])
            result["links"] = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM project_links WHERE project_id = ? ORDER BY sequence, created_at",
                    (project_row["id"],),
                ).fetchall()
            ]
            result["threads"] = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM thread_references WHERE project_id = ? ORDER BY last_activity DESC",
                    (project_row["id"],),
                ).fetchall()
            ]
            result["latest_checkpoint"] = self._dict(
                connection.execute(
                    """
                    SELECT * FROM checkpoints
                    WHERE project_id = ?
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT 1
                    """,
                    (project_row["id"],),
                ).fetchone()
            )
            result["recent_events"] = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM events WHERE project_id = ? ORDER BY id DESC LIMIT 20",
                    (project_row["id"],),
                ).fetchall()
            ]
            return result

    def get_resume_context(self, project: str) -> dict[str, Any]:
        detail = self.get_project(project)
        return {
            "project": {
                key: detail[key]
                for key in (
                    "id",
                    "slug",
                    "title",
                    "objective",
                    "status",
                    "current_summary",
                    "current_phase",
                    "owner_thread_id",
                    "writer_warning",
                    "version",
                    "updated_at",
                )
            },
            "active_items": [
                item for item in detail["work_items"] if item["status"] in {"active", "blocked"}
            ],
            "next_items": [item for item in detail["work_items"] if item["status"] == "pending"][:5],
            "links": detail["links"],
            "latest_checkpoint": detail["latest_checkpoint"],
            "threads": detail["threads"],
        }

    def claim_project(self, project: str, expected_version: int) -> dict[str, Any]:
        if not self.writer_thread_id:
            raise OwnershipError(
                "CODEX_THREAD_ID is unavailable. Supply a supported Codex deep link "
                "through the launcher before claiming this project."
            )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            project_row = self._project_row(connection, project)
            if project_row["version"] != expected_version:
                raise ConflictError(
                    f"Stale project version for {project_row['slug']}: expected "
                    f"{expected_version}, current {project_row['version']}."
                )
            if project_row["owner_thread_id"]:
                if project_row["owner_thread_id"] == self.writer_thread_id:
                    result = self._mutation_result(
                        connection,
                        project_row["id"],
                        "project.claimed",
                    )
                    connection.rollback()
                    return result
                owner_link = f"codex://threads/{project_row['owner_thread_id']}"
                raise OwnershipError(
                    f"Project '{project_row['slug']}' is already owned by {owner_link}."
                )
            self._advance(
                connection,
                project_row,
                "project.claimed",
                "Claimed project writer ownership",
                project_updates={"owner_thread_id": self.writer_thread_id},
            )
            result = self._mutation_result(
                connection,
                project_row["id"],
                "project.claimed",
                {"project_ids": [project_row["id"]]},
            )
            connection.commit()
        return result

    def release_project(
        self,
        project: str,
        expected_version: int,
        reason: str,
    ) -> dict[str, Any]:
        reason = self._required(reason, "reason")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            project_row = self._locked_project(connection, project, expected_version)
            self._advance(
                connection,
                project_row,
                "project.released",
                f"Released project writer ownership: {reason}",
                project_updates={"owner_thread_id": None},
            )
            result = self._mutation_result(
                connection,
                project_row["id"],
                "project.released",
                {"project_ids": [project_row["id"]]},
            )
            connection.commit()
        return result

    def add_project_link(
        self,
        project: str,
        expected_version: int,
        label: str,
        url: str,
        sequence: int | None = None,
    ) -> dict[str, Any]:
        label = self._required(label, "label")
        url = self._required(url, "url")
        parsed_url = urlparse(url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValidationError("url must be an absolute http:// or https:// URL")
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            project_row = self._locked_project(connection, project, expected_version)
            link_id = str(uuid.uuid4())
            if sequence is None:
                sequence = connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 10 FROM project_links WHERE project_id = ?",
                    (project_row["id"],),
                ).fetchone()[0]
            try:
                connection.execute(
                    """
                    INSERT INTO project_links (
                        id, project_id, label, url, sequence, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        link_id,
                        project_row["id"],
                        label,
                        url,
                        sequence,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ConflictError(f"Project link already exists: {url}") from error
            self._advance(
                connection,
                project_row,
                "project_link.added",
                f"Added project link: {label}",
                evidence=url,
            )
            result = self._mutation_result(
                connection,
                project_row["id"],
                "project_link.added",
                {"project_link_ids": [link_id]},
            )
            connection.commit()
        return result

    def remove_project_link(
        self,
        project: str,
        link_id: str,
        expected_version: int,
        reason: str,
    ) -> dict[str, Any]:
        reason = self._required(reason, "reason")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            project_row = self._locked_project(connection, project, expected_version)
            link = connection.execute(
                "SELECT * FROM project_links WHERE id = ? AND project_id = ?",
                (link_id, project_row["id"]),
            ).fetchone()
            if link is None:
                raise NotFoundError(f"Project link not found in project: {link_id}")
            connection.execute("DELETE FROM project_links WHERE id = ?", (link_id,))
            self._advance(
                connection,
                project_row,
                "project_link.removed",
                f"Removed project link '{link['label']}': {reason}",
                evidence=link["url"],
            )
            result = self._mutation_result(
                connection,
                project_row["id"],
                "project_link.removed",
                {"project_link_ids": [link_id]},
            )
            connection.commit()
        return result

    def prepare_github_issue_projection(
        self,
        project: str,
        workboard_url: str = "",
    ) -> dict[str, Any]:
        try:
            return build_github_issue_projection(self.get_project(project), workboard_url)
        except GitHubProjectionError as error:
            raise ValidationError(str(error)) from error

    def register_github_projection_comment(
        self,
        project: str,
        expected_version: int,
        comment_url: str,
    ) -> dict[str, Any]:
        comment = parse_github_issue_comment_url(comment_url)
        if comment is None:
            raise ValidationError(
                "comment_url must have the form "
                "https://github.com/<owner>/<repository>/issues/<number>#issuecomment-<id>"
            )
        projection = self.prepare_github_issue_projection(project)
        issue = projection["issue"]
        if projection["comment"] is not None:
            raise ConflictError("project already has a Wander projection comment")
        if (
            comment["owner"].casefold() != issue["owner"].casefold()
            or comment["repo"].casefold() != issue["repo"].casefold()
            or comment["number"] != issue["number"]
        ):
            raise ValidationError(
                "projection comment must belong to the configured GitHub issue"
            )
        return self.add_project_link(
            project,
            expected_version,
            PROJECTION_LINK_LABEL,
            comment_url,
        )

    def add_work_item(
        self,
        project: str,
        expected_version: int,
        title: str,
        completion_criteria: str = "",
        notes: str = "",
        parent_id: str | None = None,
        sequence: int | None = None,
    ) -> dict[str, Any]:
        title = self._required(title, "title")
        item_id = str(uuid.uuid4())
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            project_row = self._locked_project(connection, project, expected_version)
            if parent_id:
                parent = connection.execute(
                    "SELECT project_id FROM work_items WHERE id = ?",
                    (parent_id,),
                ).fetchone()
                if parent is None or parent["project_id"] != project_row["id"]:
                    raise ValidationError("parent_id must identify a work item in the same project")
            if sequence is None:
                sequence = connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 10 FROM work_items WHERE project_id = ?",
                    (project_row["id"],),
                ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO work_items (
                    id, project_id, parent_id, sequence, title, status,
                    completion_criteria, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    item_id,
                    project_row["id"],
                    parent_id,
                    sequence,
                    title,
                    completion_criteria.strip(),
                    notes.strip(),
                    now,
                    now,
                ),
            )
            self._advance(
                connection,
                project_row,
                "work_item.added",
                f"Added work item: {title}",
            )
            result = self._mutation_result(
                connection,
                project_row["id"],
                "work_item.added",
                {"work_item_ids": [item_id]},
            )
            connection.commit()
        return result

    def _transition_item(
        self,
        project: str,
        item_id: str,
        expected_version: int,
        target_status: str,
        summary: str,
        evidence: str = "",
        notes: str | None = None,
        commit_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        if target_status not in ITEM_STATUSES:
            raise ValidationError(f"Unknown work item status: {target_status}")
        if commit_urls and target_status != "complete":
            raise ValidationError("commit URLs may only be attached while completing an item")
        commit_links = [self._github_commit_link(url) for url in (commit_urls or [])]
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            project_row = self._locked_project(connection, project, expected_version)
            item = connection.execute(
                "SELECT * FROM work_items WHERE id = ? AND project_id = ?",
                (item_id, project_row["id"]),
            ).fetchone()
            if item is None:
                raise NotFoundError(f"Work item not found in project: {item_id}")
            if target_status not in ITEM_TRANSITIONS[item["status"]]:
                raise ValidationError(
                    f"Invalid work item transition: {item['status']} -> {target_status}"
                )
            if target_status == "complete" and not evidence.strip():
                raise ValidationError("completion evidence is required")
            if target_status == "complete":
                incomplete_children = connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM work_items
                    WHERE parent_id = ?
                      AND status NOT IN ('complete', 'abandoned')
                    """,
                    (item_id,),
                ).fetchone()[0]
                if incomplete_children:
                    raise ValidationError(
                        "all child work items must be complete or abandoned before the parent"
                    )
            next_notes = item["notes"] if notes is None else notes.strip()
            next_link_sequence = connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 10
                FROM work_item_links
                WHERE work_item_id = ?
                """,
                (item_id,),
            ).fetchone()[0]
            created_link_ids: list[str] = []
            for offset, (url, label) in enumerate(commit_links):
                link_id = str(uuid.uuid4())
                try:
                    connection.execute(
                        """
                        INSERT INTO work_item_links (
                            id, project_id, work_item_id, label, url,
                            sequence, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            link_id,
                            project_row["id"],
                            item_id,
                            label,
                            url,
                            next_link_sequence + (offset * 10),
                            utc_now(),
                        ),
                    )
                except sqlite3.IntegrityError as error:
                    raise ConflictError(f"Work item commit link already exists: {url}") from error
                created_link_ids.append(link_id)
            connection.execute(
                """
                UPDATE work_items
                SET status = ?, completion_evidence = ?, notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (target_status, evidence.strip(), next_notes, utc_now(), item_id),
            )
            self._advance(
                connection,
                project_row,
                f"work_item.{target_status}",
                summary,
                evidence=evidence,
            )
            changed = {"work_item_ids": [item_id]}
            if created_link_ids:
                changed["work_item_link_ids"] = created_link_ids
            result = self._mutation_result(
                connection,
                project_row["id"],
                f"work_item.{target_status}",
                changed,
            )
            connection.commit()
        return result

    def start_work_item(
        self,
        project: str,
        item_id: str,
        expected_version: int,
        summary: str,
    ) -> dict[str, Any]:
        return self._transition_item(
            project,
            item_id,
            expected_version,
            "active",
            self._required(summary, "summary"),
        )

    def complete_work_item(
        self,
        project: str,
        item_id: str,
        expected_version: int,
        evidence: str,
        summary: str,
        commit_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._transition_item(
            project,
            item_id,
            expected_version,
            "complete",
            self._required(summary, "summary"),
            evidence=self._required(evidence, "evidence"),
            commit_urls=commit_urls,
        )

    def add_work_item_link(
        self,
        project: str,
        item_id: str,
        expected_version: int,
        commit_url: str,
    ) -> dict[str, Any]:
        commit_url, label = self._github_commit_link(commit_url)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            project_row = self._locked_project(connection, project, expected_version)
            item = connection.execute(
                "SELECT * FROM work_items WHERE id = ? AND project_id = ?",
                (item_id, project_row["id"]),
            ).fetchone()
            if item is None:
                raise NotFoundError(f"Work item not found in project: {item_id}")
            if item["status"] != "complete":
                raise ValidationError("commit links may only be added to completed work items")
            sequence = connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 10
                FROM work_item_links
                WHERE work_item_id = ?
                """,
                (item_id,),
            ).fetchone()[0]
            link_id = str(uuid.uuid4())
            try:
                connection.execute(
                    """
                    INSERT INTO work_item_links (
                        id, project_id, work_item_id, label, url,
                        sequence, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        link_id,
                        project_row["id"],
                        item_id,
                        label,
                        commit_url,
                        sequence,
                        utc_now(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ConflictError(
                    f"Work item commit link already exists: {commit_url}"
                ) from error
            self._advance(
                connection,
                project_row,
                "work_item_link.added",
                f"Attached commit {label} to work item: {item['title']}",
                evidence=commit_url,
            )
            result = self._mutation_result(
                connection,
                project_row["id"],
                "work_item_link.added",
                {"work_item_link_ids": [link_id]},
            )
            connection.commit()
        return result

    def block_work_item(
        self,
        project: str,
        item_id: str,
        expected_version: int,
        reason: str,
    ) -> dict[str, Any]:
        reason = self._required(reason, "reason")
        return self._transition_item(
            project,
            item_id,
            expected_version,
            "blocked",
            f"Blocked work item: {reason}",
            notes=reason,
        )

    def abandon_work_item(
        self,
        project: str,
        item_id: str,
        expected_version: int,
        reason: str,
    ) -> dict[str, Any]:
        reason = self._required(reason, "reason")
        return self._transition_item(
            project,
            item_id,
            expected_version,
            "abandoned",
            f"Abandoned work item: {reason}",
            notes=reason,
        )

    def record_checkpoint(
        self,
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
        current_summary = self._required(current_summary, "current_summary")
        current_phase = self._required(current_phase, "current_phase")
        current_model = self._required(current_model, "current_model")
        resume_here = self._required(resume_here, "resume_here")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            project_row = self._locked_project(connection, project, expected_version)
            now = utc_now()
            checkpoint_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO checkpoints (
                    id, project_id, current_model, last_decision, invariants,
                    open_questions, resume_here, source_thread, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint_id,
                    project_row["id"],
                    current_model,
                    last_decision.strip(),
                    invariants.strip(),
                    open_questions.strip(),
                    resume_here,
                    self.writer_thread_link,
                    now,
                ),
            )
            self._advance(
                connection,
                project_row,
                "checkpoint.recorded",
                current_summary,
                project_updates={
                    "current_summary": current_summary,
                    "current_phase": current_phase,
                },
            )
            result = self._mutation_result(
                connection,
                project_row["id"],
                "checkpoint.recorded",
                {"checkpoint_ids": [checkpoint_id]},
            )
            connection.commit()
        return result

    def attach_thread(
        self,
        project: str,
        expected_version: int,
        deep_link: str,
        title: str,
        role: str,
    ) -> dict[str, Any]:
        deep_link = deep_link.strip()
        if not THREAD_LINK_PATTERN.fullmatch(deep_link):
            raise ValidationError("deep_link must have the form codex://threads/<thread-id>")
        title = self._required(title, "title")
        role = self._required(role, "role")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            project_row = self._locked_project(connection, project, expected_version)
            now = utc_now()
            candidate_thread_reference_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO thread_references (
                    id, project_id, deep_link, title, role, last_activity
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, deep_link) DO UPDATE SET
                    title = excluded.title,
                    role = excluded.role,
                    last_activity = excluded.last_activity
                """,
                (
                    candidate_thread_reference_id,
                    project_row["id"],
                    deep_link,
                    title,
                    role,
                    now,
                ),
            )
            thread_reference_row = connection.execute(
                """
                SELECT id FROM thread_references
                WHERE project_id = ? AND deep_link = ?
                """,
                (project_row["id"], deep_link),
            ).fetchone()
            self._advance(
                connection,
                project_row,
                "thread.attached",
                f"Attached Codex thread: {title}",
            )
            result = self._mutation_result(
                connection,
                project_row["id"],
                "thread.attached",
                {"thread_reference_ids": [thread_reference_row["id"]]},
            )
            connection.commit()
        return result

    def set_project_status(
        self,
        project: str,
        expected_version: int,
        status: str,
        summary: str,
        evidence: str = "",
    ) -> dict[str, Any]:
        if status not in PROJECT_STATUSES:
            raise ValidationError(f"Unknown project status: {status}")
        summary = self._required(summary, "summary")
        if status == "completed" and not evidence.strip():
            raise ValidationError("completion evidence is required")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            project_row = self._locked_project(connection, project, expected_version)
            if status not in PROJECT_TRANSITIONS[project_row["status"]]:
                raise ValidationError(
                    f"Invalid project transition: {project_row['status']} -> {status}"
                )
            self._advance(
                connection,
                project_row,
                f"project.{status}",
                summary,
                evidence=evidence,
                project_updates={"status": status, "current_summary": summary},
            )
            result = self._mutation_result(
                connection,
                project_row["id"],
                f"project.{status}",
                {"project_ids": [project_row["id"]]},
            )
            connection.commit()
        return result

