from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import db
from github_graphql import GitHubGraphQLClient, GitHubIssue, RepositoryTarget
from issue_intelligence import IssueIntelligenceEngine


class IssueSyncService:
    def __init__(self, client: GitHubGraphQLClient, intelligence: IssueIntelligenceEngine):
        self.client = client
        self.intelligence = intelligence

    async def sync_repositories(
        self,
        repositories: list[RepositoryTarget],
        per_repo_limit: int = 25,
    ) -> dict[str, int]:
        db.init_db()
        db.delete_non_gemini_classifications()
        db.deactivate_repositories_except([repository.slug for repository in repositories])
        synced = 0
        classified = 0
        archived = 0

        for repository in repositories:
            repo_id = db.get_or_create_repository(
                owner=repository.owner,
                name=repository.name,
                description=repository.description,
                primary_domain=repository.primary_domain,
                popularity=repository.popularity,
            )
            issues = await self.client.fetch_issues(
                owner=repository.owner,
                repo=repository.name,
                first=per_repo_limit,
                description=repository.description,
                primary_domain=repository.primary_domain,
                popularity=repository.popularity,
            )
            sync_result = await self._sync_repository_issues(repo_id, issues)
            synced += sync_result["seen"]
            classified += sync_result["classified"]
            archived += sync_result["archived"]

        return {
            "repositories": len(repositories),
            "issues_seen": synced,
            "issues_classified": classified,
            "issues_archived": archived,
        }

    async def _sync_repository_issues(self, repository_id: int, issues: list[GitHubIssue]) -> dict[str, int]:
        seen_at = datetime.now(timezone.utc).isoformat()
        classified = 0
        seen_ids: list[str] = []
        pending_classifications: list[tuple[int, GitHubIssue, str]] = []

        for issue in issues:
            github_issue_id = f"{issue.repository.slug}#{issue.number}"
            source_hash = self._source_hash(issue)
            issue_id, changed = db.upsert_issue_record(
                repository_id=repository_id,
                github_issue_id=github_issue_id,
                issue_number=issue.number,
                title=issue.title,
                body=issue.body,
                url=issue.url,
                author=issue.author,
                labels=issue.labels,
                comments=issue.comments,
                github_created_at=issue.created_at.isoformat(),
                github_updated_at=issue.updated_at.isoformat(),
                last_seen_at=seen_at,
                source_hash=source_hash,
            )
            seen_ids.append(github_issue_id)

            existing_source = db.issue_classification_source(issue_id)
            if changed or existing_source != self.intelligence.source_name:
                pending_classifications.append((issue_id, issue, source_hash))

        if pending_classifications:
            analyses = await self.intelligence.analyze_issues(
                [issue for _, issue, _ in pending_classifications]
            )
            for (issue_id, _, source_hash), analysis in zip(pending_classifications, analyses):
                if analysis is None:
                    continue
                db.upsert_issue_classification(
                    issue_id=issue_id,
                    domain=analysis.domain,
                    difficulty=analysis.difficulty,
                    difficulty_score=analysis.difficulty_score,
                    reasoning=analysis.reasoning,
                    model=analysis.source,
                    source_hash=source_hash,
                    classified_at=seen_at,
                )
                classified += 1

        archived = db.mark_missing_issues(repository_id, seen_ids, seen_at)
        db.touch_repository_sync(repository_id, seen_at)
        return {"seen": len(issues), "classified": classified, "archived": archived}

    def _source_hash(self, issue: GitHubIssue) -> str:
        digest = hashlib.sha256()
        digest.update(issue.title.encode("utf-8"))
        digest.update(b"\n")
        digest.update(issue.body.encode("utf-8"))
        digest.update(b"\n")
        digest.update("|".join(sorted(issue.labels)).encode("utf-8"))
        return digest.hexdigest()
