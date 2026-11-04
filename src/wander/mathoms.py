from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

import anyio
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


WANDER_SOURCE_SYSTEM = "wander"


class HippoError(RuntimeError):
    """Raised when Wander cannot persist or retrieve Mathoms through Hippo."""


class HippoClient:
    def __init__(self, url: str, token: str):
        self.url = url.strip()
        self.token = token.strip()

    @classmethod
    def from_environment(cls) -> HippoClient:
        return cls(
            os.environ.get("WANDER_HIPPO_URL", "http://localhost:8787/mcp"),
            os.environ.get("WANDER_HIPPO_TOKEN", ""),
        )

    def record_mathom(
        self,
        source_session_id: str,
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
        finding: dict[str, Any] = {
            "kind": kind,
            "predicate": predicate,
            "statement": statement,
            "entities": entities or [],
            "evidence": evidence or [],
            "discoveredAt": datetime.now(UTC).isoformat(),
            "confidence": confidence,
        }
        if value is not None:
            finding["value"] = value
        if effective_from:
            finding["effectiveFrom"] = effective_from
        if effective_to:
            finding["effectiveTo"] = effective_to

        arguments: dict[str, Any] = {
            "request": {
                "sourceSessionId": source_session_id,
                "findings": [finding],
            }
        }
        if agent_model:
            arguments["request"]["agentModel"] = agent_model

        return self._call_tool("record_operational_findings", arguments)

    def search_mathoms(
        self,
        query: str = "",
        entity_identifiers: list[str] | None = None,
        maximum_results: int = 12,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "sourceSystems": [WANDER_SOURCE_SYSTEM],
            "entityIdentifiers": entity_identifiers or [],
            "concepts": [query] if query.strip() else [],
            "maximumResults": maximum_results,
        }
        return self._call_tool(
            "recall_operational_context",
            {"request": request},
        )

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.token:
            raise HippoError(
                "WANDER_HIPPO_TOKEN is required before using Mathom tools"
            )
        if not self.url:
            raise HippoError(
                "WANDER_HIPPO_URL is required before using Mathom tools"
            )

        try:
            return anyio.run(self._call_tool_async, name, arguments)
        except HippoError:
            raise
        except Exception as exception:
            raise HippoError(
                f"Hippo tool {name} could not be reached or completed"
            ) from exception

    async def _call_tool_async(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token}"}
        async with httpx2.AsyncClient(headers=headers) as http_client:
            async with streamable_http_client(
                self.url,
                http_client=http_client,
            ) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(name, arguments)

        if result.is_error:
            messages = [
                block.text
                for block in result.content
                if getattr(block, "type", None) == "text"
            ]
            raise HippoError("; ".join(messages) or f"Hippo tool {name} failed")

        if result.structured_content is not None:
            return result.structured_content

        messages = [
            block.text
            for block in result.content
            if getattr(block, "type", None) == "text"
        ]
        for message in messages:
            try:
                parsed = json.loads(message)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed

        raise HippoError(f"Hippo tool {name} returned no object result")
