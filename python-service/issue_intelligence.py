from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Iterable

import httpx
from pydantic import BaseModel

from config import config
from github_graphql import GitHubIssue


class IssueAnalysis(BaseModel):
    domain: str
    difficulty: str
    difficulty_score: int
    reasoning: str
    source: str


class IssueCard(BaseModel):
    number: int
    title: str
    body: str
    url: str
    author: str
    labels: list[str]
    comments: int
    created_at: str
    updated_at: str
    repository: str
    repository_description: str
    domain: str
    difficulty: str
    difficulty_score: int
    reasoning: str
    intelligence_source: str


class IssueIntelligenceEngine:
    def __init__(self, gemini_api_key: str | None = None, model: str | None = None):
        self.gemini_api_key = gemini_api_key or config.GEMINI_API_KEY
        self.models = self._configured_models(model)
        self.model = self.models[0]
        self.requests_per_minute = max(1, config.GEMINI_REQUESTS_PER_MINUTE)
        self.model_rpms = self._configured_model_rpms()
        self.batch_size = max(1, config.GEMINI_BATCH_SIZE)
        self._last_gemini_request_at: dict[str, float] = {}
        self._disabled_models: set[str] = set()

    @property
    def source_name(self) -> str:
        return "gemini"

    def _difficulty_label(self, difficulty_score: int) -> str:
        if difficulty_score <= 3:
            return "easy"
        if difficulty_score <= 7:
            return "medium"
        return "hard"

    async def analyze_issue(self, issue: GitHubIssue) -> IssueAnalysis | None:
        analyses = await self.analyze_issues([issue])
        return analyses[0] if analyses else None

    async def analyze_issues(self, issues: list[GitHubIssue]) -> list[IssueAnalysis | None]:
        if not issues:
            return []

        if self.gemini_api_key:
            analyses: list[IssueAnalysis | None] = []
            for index in range(0, len(issues), self.batch_size):
                batch = issues[index:index + self.batch_size]
                analyses.extend(await self._analyze_batch_with_gemini(batch))
            return analyses

        return [None for _ in issues]

    async def build_issue_cards(self, issues: Iterable[GitHubIssue]) -> list[IssueCard]:
        cards: list[IssueCard] = []
        for issue in issues:
            analysis = await self.analyze_issue(issue)
            if analysis is None:
                continue
            cards.append(
                IssueCard(
                    number=issue.number,
                    title=issue.title,
                    body=issue.body,
                    url=issue.url,
                    author=issue.author,
                    labels=issue.labels,
                    comments=issue.comments,
                    created_at=issue.created_at.isoformat(),
                    updated_at=issue.updated_at.isoformat(),
                    repository=issue.repository.slug,
                    repository_description=issue.repository.description,
                    domain=analysis.domain,
                    difficulty=analysis.difficulty,
                    difficulty_score=analysis.difficulty_score,
                    reasoning=analysis.reasoning,
                    intelligence_source=analysis.source,
                )
            )
        return cards

    async def _analyze_batch_with_gemini(self, issues: list[GitHubIssue]) -> list[IssueAnalysis | None]:
        if not self.gemini_api_key:
            return [None for _ in issues]
        api_key = self.gemini_api_key

        issue_payload = [
            {
                "id": str(index),
                "repository": issue.repository.slug,
                "title": issue.title,
                "labels": issue.labels,
                "comments": issue.comments,
                "body": issue.body[:2000],
            }
            for index, issue in enumerate(issues)
        ]
        prompt = (
            "You classify GitHub issues for an open-source practice board. Return strict JSON only. "
            "Return an array with one object for each input issue, preserving each issue id. "
            "Each object must have keys id, domain, difficulty, difficulty_score, reasoning. "
            "domain must be one of frontend, backend, security, devops, docs, other. "
            "difficulty_score must be an integer from 1 to 10, where 1 is tiny and beginner-friendly and 10 is very complex. "
            "difficulty must match difficulty_score: 1-3 is easy, 4-7 is medium, and 8-10 is hard. "
            "Keep reasoning to one short sentence.\n\n"
            f"Issues:\n{json.dumps(issue_payload)}"
        )
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                    ]
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
            },
        }
        headers = {
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        }

        data = None
        async with httpx.AsyncClient(timeout=30.0) as client:
            for model in self.models:
                if model in self._disabled_models:
                    continue
                try:
                    await self._wait_for_gemini_rate_limit(model)
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                    response = await client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()
                    self.model = model
                    break
                except httpx.HTTPStatusError as exc:
                    if self._should_skip_model(exc):
                        self._disabled_models.add(model)
                        continue
                    raise
                except httpx.RequestError:
                    continue

        if data is None:
            return [None for _ in issues]

        text = ""
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts:
                text = parts[0].get("text", "")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return [None for _ in issues]

        parsed_items = self._parsed_gemini_items(parsed)
        analyses_by_id = {
            str(item.get("id")): self._analysis_from_gemini_item(item)
            for item in parsed_items
            if isinstance(item, dict)
        }
        return [
            analyses_by_id.get(str(index))
            for index, _ in enumerate(issues)
        ]

    def _parsed_gemini_items(self, parsed: Any) -> list[Any]:
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            items = parsed.get("issues") or parsed.get("classifications") or parsed.get("results")
            if isinstance(items, list):
                return items
        return []

    def _analysis_from_gemini_item(self, item: dict[str, Any]) -> IssueAnalysis:
        try:
            difficulty_score = int(item.get("difficulty_score", 5))
        except (TypeError, ValueError):
            difficulty_score = 5
        difficulty_score = max(1, min(difficulty_score, 10))

        return IssueAnalysis(
            domain=item.get("domain", "other"),
            difficulty=self._difficulty_label(difficulty_score),
            difficulty_score=difficulty_score,
            reasoning=item.get("reasoning", "Model-generated classification."),
            source="gemini",
        )

    def _configured_models(self, model: str | None) -> list[str]:
        configured_models = [model] if model else config.GEMINI_MODELS
        models = [candidate.strip() for candidate in configured_models if candidate.strip()]
        return models or [config.GEMINI_MODEL]

    def _configured_model_rpms(self) -> dict[str, int]:
        rpms: dict[str, int] = {}
        for item in config.GEMINI_MODEL_RPMS.split(","):
            if ":" not in item:
                continue
            model, rpm = item.split(":", 1)
            try:
                rpms[model.strip()] = max(1, int(rpm.strip()))
            except ValueError:
                continue
        return rpms

    def _should_skip_model(self, exc: httpx.HTTPStatusError) -> bool:
        return exc.response.status_code in {400, 404, 429, 500, 502, 503, 504}

    async def _wait_for_gemini_rate_limit(self, model: str) -> None:
        rpm = self.model_rpms.get(model, self.requests_per_minute)
        minimum_request_interval = 60.0 / rpm
        elapsed = time.monotonic() - self._last_gemini_request_at.get(model, 0.0)
        wait_seconds = minimum_request_interval - elapsed
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
        self._last_gemini_request_at[model] = time.monotonic()
