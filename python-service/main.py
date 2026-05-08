from __future__ import annotations

import asyncio
import sys
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

import db
from config import config
from github_graphql import GitHubGraphQLClient
from issue_intelligence import IssueCard, IssueIntelligenceEngine
from sync_service import IssueSyncService


class DiscoverResponse(BaseModel):
    repositories: list[str]
    issues: list[IssueCard]
    intelligence_mode: str


class SyncResponse(BaseModel):
    repositories: int
    issues_seen: int
    issues_classified: int
    issues_archived: int
    intelligence_mode: str


app = FastAPI(title="ContriPlace Python Service", version="0.1.0")


def build_client() -> GitHubGraphQLClient:
    if not config.GITHUB_TOKEN:
        raise HTTPException(status_code=500, detail="GITHUB_TOKEN is not configured")
    return GitHubGraphQLClient(token=config.GITHUB_TOKEN)


def build_sync_service() -> IssueSyncService:
    return IssueSyncService(
        client=build_client(),
        intelligence=IssueIntelligenceEngine(),
    )


def intelligence_mode() -> str:
    return "gemini" if config.GEMINI_API_KEY else "unconfigured"


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/admin/sync", response_model=SyncResponse)
async def sync_issues(limit_per_repo: int = Query(default=25, ge=1, le=100)) -> SyncResponse:
    db.init_db()
    try:
        result = await build_sync_service().sync_repositories(
            repositories=config.repo_targets(),
            per_repo_limit=limit_per_repo,
        )
        return SyncResponse(
            **result,
            intelligence_mode=intelligence_mode(),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to sync issues from GitHub: {exc}") from exc


@app.get("/issues/discover", response_model=DiscoverResponse)
async def discover_issues(
    domain: Literal["all", "frontend", "backend", "security", "devops", "docs", "other"] = "all",
    difficulty: Literal["all", "easy", "medium", "hard"] = "all",
    limit: int = Query(default=24, ge=1, le=60),
) -> DiscoverResponse:
    db.init_db()
    cards = db.list_issue_cards(domain=domain, difficulty=difficulty, limit=limit)
    return DiscoverResponse(
        repositories=db.repository_slugs() or [repo.slug for repo in config.repo_targets()],
        issues=cards,
        intelligence_mode=intelligence_mode(),
    )


async def _run_cli() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "sync":
        db.init_db()
        result = await build_sync_service().sync_repositories(
            repositories=config.repo_targets(),
            per_repo_limit=25,
        )
        print(result)
        return

    db.init_db()
    cards = db.list_issue_cards(domain="all", difficulty="all", limit=12)
    print(f"Loaded {len(cards)} cached issues from the database.")
    print()
    for card in cards:
        print(f"[{card.domain}/{card.difficulty}] {card.repository} #{card.number}: {card.title}")
        print(f"  comments={card.comments} source={card.intelligence_source}")
        print(f"  {card.reasoning}")
        print(f"  {card.url}")
        print()


if __name__ == "__main__":
    asyncio.run(_run_cli())
