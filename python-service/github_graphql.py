from __future__ import annotations

from datetime import datetime
from typing import Iterable

import httpx
from pydantic import BaseModel, Field

from config import config


class RepositoryTarget(BaseModel):
    owner: str
    name: str
    description: str = ""
    primary_domain: str = "other"
    popularity: str = "high"

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"


class GitHubIssue(BaseModel):
    number: int
    title: str
    body: str = ""
    url: str
    author: str = "unknown"
    labels: list[str] = Field(default_factory=list)
    comments: int = 0
    created_at: datetime
    updated_at: datetime
    repository: RepositoryTarget


class GitHubGraphQLClient:
    def __init__(self, token: str, api_url: str | None = None, timeout: float = 20.0):
        self.token = token
        self.api_url = api_url or config.GITHUB_API_URL
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def fetch_issues(
        self,
        owner: str,
        repo: str,
        since: datetime | None = None,
        first: int = 20,
        description: str = "",
        primary_domain: str = "other",
        popularity: str = "high",
    ) -> list[GitHubIssue]:
        repository = RepositoryTarget(
            owner=owner,
            name=repo,
            description=description,
            primary_domain=primary_domain,
            popularity=popularity,
        )
        variables = {
            "owner": owner,
            "repo": repo,
            "first": first,
            "since": since.isoformat() if since else None,
        }
        query = """
        query RepoIssues($owner: String!, $repo: String!, $first: Int!, $since: DateTime) {
          repository(owner: $owner, name: $repo) {
            issues(
              first: $first
              states: OPEN
              orderBy: {field: UPDATED_AT, direction: DESC}
              filterBy: {since: $since}
            ) {
              nodes {
                number
                title
                body
                url
                createdAt
                updatedAt
                comments {
                  totalCount
                }
                author {
                  login
                }
                assignees(first: 5) {
                  totalCount
                }
                timelineItems(itemTypes: CROSS_REFERENCED_EVENT, last: 20) {
                  nodes {
                    ... on CrossReferencedEvent {
                      source {
                        __typename
                        ... on PullRequest {
                          state
                          isDraft
                          url
                        }
                      }
                    }
                  }
                }
                labels(first: 10) {
                  nodes {
                    name
                  }
                }
              }
            }
          }
        }
        """

        response_data = await self._post_graphql(query=query, variables=variables)
        issue_nodes = (
            response_data.get("data", {})
            .get("repository", {})
            .get("issues", {})
            .get("nodes", [])
        )

        issues: list[GitHubIssue] = []
        for node in issue_nodes:
            if node.get("assignees", {}).get("totalCount", 0) > 0:
                continue
            if self._has_linked_open_pull_request(node):
                continue

            issues.append(
                GitHubIssue(
                    number=node["number"],
                    title=node.get("title", ""),
                    body=node.get("body") or "",
                    url=node["url"],
                    author=(node.get("author") or {}).get("login", "unknown"),
                    labels=[
                        label["name"]
                        for label in (node.get("labels", {}).get("nodes") or [])
                        if label and label.get("name")
                    ],
                    comments=node.get("comments", {}).get("totalCount", 0),
                    created_at=datetime.fromisoformat(node["createdAt"].replace("Z", "+00:00")),
                    updated_at=datetime.fromisoformat(node["updatedAt"].replace("Z", "+00:00")),
                    repository=repository,
                )
            )

        return issues

    def _has_linked_open_pull_request(self, node: dict) -> bool:
        timeline_nodes = node.get("timelineItems", {}).get("nodes") or []
        for timeline_node in timeline_nodes:
            source = (timeline_node or {}).get("source") or {}
            if source.get("__typename") == "PullRequest" and source.get("state") == "OPEN":
                return True
        return False

    async def fetch_multiple_repos(
        self,
        repositories: Iterable[RepositoryTarget] | None = None,
        per_repo_limit: int = 12,
        since: datetime | None = None,
    ) -> list[GitHubIssue]:
        repo_targets = list(repositories or config.repo_targets())
        issues_by_repo = await self._gather_repo_issues(
            repo_targets=repo_targets,
            per_repo_limit=per_repo_limit,
            since=since,
        )
        return [issue for repo_issues in issues_by_repo for issue in repo_issues]

    async def _gather_repo_issues(
        self,
        repo_targets: list[RepositoryTarget],
        per_repo_limit: int,
        since: datetime | None,
    ) -> list[list[GitHubIssue]]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            self._client = client
            try:
                return await self._fetch_all(repo_targets, per_repo_limit, since)
            finally:
                self._client = None

    async def _fetch_all(
        self,
        repo_targets: list[RepositoryTarget],
        per_repo_limit: int,
        since: datetime | None,
    ) -> list[list[GitHubIssue]]:
        tasks = [
            self.fetch_issues(
                owner=repo.owner,
                repo=repo.name,
                since=since,
                first=per_repo_limit,
                description=repo.description,
                primary_domain=repo.primary_domain,
                popularity=repo.popularity,
            )
            for repo in repo_targets
        ]
        return await _gather(tasks)

    async def _post_graphql(self, query: str, variables: dict) -> dict:
        if self._client is None or self._client.is_closed:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                return await self._post_graphql_with_client(client, query, variables)

        return await self._post_graphql_with_client(self._client, query, variables)

    async def _post_graphql_with_client(
        self,
        client: httpx.AsyncClient,
        query: str,
        variables: dict,
    ) -> dict:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
        }
        response = await client.post(
            self.api_url,
            headers=headers,
            json={"query": query, "variables": variables},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(f"GitHub GraphQL returned errors: {payload['errors']}")
        return payload


async def _gather(tasks):
    import asyncio

    return await asyncio.gather(*tasks)
