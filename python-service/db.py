from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Protocol, cast

from config import config
from issue_intelligence import IssueCard


class DbRow(Protocol):
    def __getitem__(self, key: str) -> Any: ...


def _fetchone_row(cursor: sqlite3.Cursor) -> DbRow | None:
    return cast(DbRow | None, cursor.fetchone())


def _fetchall_rows(cursor: sqlite3.Cursor) -> list[DbRow]:
    return cast(list[DbRow], cursor.fetchall())


def _last_insert_id(cursor: sqlite3.Cursor) -> int:
    if cursor.lastrowid is None:
        raise RuntimeError("Expected SQLite to return a row id for the insert.")
    return cursor.lastrowid


def _sqlite_path() -> str:
    prefix = "sqlite:///"
    if not config.DATABASE_URL.startswith(prefix):
        raise ValueError("Only sqlite DATABASE_URL values are supported in this first version.")
    raw_path = config.DATABASE_URL[len(prefix):]
    path = Path(raw_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(_sqlite_path())
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS repositories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                primary_domain TEXT NOT NULL DEFAULT 'other',
                popularity TEXT NOT NULL DEFAULT 'high',
                last_synced_at TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                UNIQUE(owner, name)
            );

            CREATE TABLE IF NOT EXISTS issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repository_id INTEGER NOT NULL,
                github_issue_id TEXT NOT NULL,
                issue_number INTEGER NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL,
                author TEXT NOT NULL DEFAULT 'unknown',
                labels_json TEXT NOT NULL DEFAULT '[]',
                comments INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'open',
                is_assigned INTEGER NOT NULL DEFAULT 0,
                eligibility_status TEXT NOT NULL DEFAULT 'eligible',
                github_created_at TEXT NOT NULL,
                github_updated_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                UNIQUE(repository_id, github_issue_id),
                FOREIGN KEY (repository_id) REFERENCES repositories(id)
            );

            CREATE TABLE IF NOT EXISTS issue_classifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_id INTEGER NOT NULL UNIQUE,
                domain TEXT NOT NULL,
                difficulty TEXT NOT NULL,
                difficulty_score INTEGER NOT NULL,
                reasoning TEXT NOT NULL,
                model TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                classified_at TEXT NOT NULL,
                FOREIGN KEY (issue_id) REFERENCES issues(id)
            );
            """
        )


def get_or_create_repository(
    owner: str,
    name: str,
    description: str,
    primary_domain: str,
    popularity: str,
) -> int:
    with get_connection() as connection:
        row = _fetchone_row(
            connection.execute(
                """
                SELECT id FROM repositories WHERE owner = ? AND name = ?
                """,
                (owner, name),
            )
        )
        if row:
            connection.execute(
                """
                UPDATE repositories
                SET description = ?, primary_domain = ?, popularity = ?, active = 1
                WHERE id = ?
                """,
                (description, primary_domain, popularity, row["id"]),
            )
            return int(row["id"])

        cursor = connection.execute(
            """
            INSERT INTO repositories (owner, name, description, primary_domain, popularity)
            VALUES (?, ?, ?, ?, ?)
            """,
            (owner, name, description, primary_domain, popularity),
        )
        return _last_insert_id(cursor)


def touch_repository_sync(repository_id: int, synced_at: str) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE repositories
            SET last_synced_at = ?
            WHERE id = ?
            """,
            (synced_at, repository_id),
        )


def deactivate_repositories_except(repository_slugs: list[str]) -> int:
    with get_connection() as connection:
        if repository_slugs:
            placeholders = ",".join("?" for _ in repository_slugs)
            cursor = connection.execute(
                f"""
                UPDATE repositories
                SET active = 0
                WHERE owner || '/' || name NOT IN ({placeholders})
                """,
                repository_slugs,
            )
        else:
            cursor = connection.execute(
                """
                UPDATE repositories
                SET active = 0
                """
            )
        return cursor.rowcount


def upsert_issue_record(
    repository_id: int,
    github_issue_id: str,
    issue_number: int,
    title: str,
    body: str,
    url: str,
    author: str,
    labels: list[str],
    comments: int,
    github_created_at: str,
    github_updated_at: str,
    last_seen_at: str,
    source_hash: str,
) -> tuple[int, bool]:
    labels_json = json.dumps(labels)
    with get_connection() as connection:
        row = _fetchone_row(
            connection.execute(
                """
                SELECT id, source_hash FROM issues
                WHERE repository_id = ? AND github_issue_id = ?
                """,
                (repository_id, github_issue_id),
            )
        )

        if row:
            connection.execute(
                """
                UPDATE issues
                SET issue_number = ?,
                    title = ?,
                    body = ?,
                    url = ?,
                    author = ?,
                    labels_json = ?,
                    comments = ?,
                    state = 'open',
                    is_assigned = 0,
                    eligibility_status = 'eligible',
                    github_created_at = ?,
                    github_updated_at = ?,
                    last_seen_at = ?,
                    source_hash = ?
                WHERE id = ?
                """,
                (
                    issue_number,
                    title,
                    body,
                    url,
                    author,
                    labels_json,
                    comments,
                    github_created_at,
                    github_updated_at,
                    last_seen_at,
                    source_hash,
                    row["id"],
                ),
            )
            return int(row["id"]), row["source_hash"] != source_hash

        cursor = connection.execute(
            """
            INSERT INTO issues (
                repository_id,
                github_issue_id,
                issue_number,
                title,
                body,
                url,
                author,
                labels_json,
                comments,
                state,
                is_assigned,
                eligibility_status,
                github_created_at,
                github_updated_at,
                last_seen_at,
                source_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', 0, 'eligible', ?, ?, ?, ?)
            """,
            (
                repository_id,
                github_issue_id,
                issue_number,
                title,
                body,
                url,
                author,
                labels_json,
                comments,
                github_created_at,
                github_updated_at,
                last_seen_at,
                source_hash,
            ),
        )
        return _last_insert_id(cursor), True


def upsert_issue_classification(
    issue_id: int,
    domain: str,
    difficulty: str,
    difficulty_score: int,
    reasoning: str,
    model: str,
    source_hash: str,
    classified_at: str,
) -> None:
    with get_connection() as connection:
        row = _fetchone_row(
            connection.execute(
                "SELECT id FROM issue_classifications WHERE issue_id = ?",
                (issue_id,),
            )
        )
        if row:
            connection.execute(
                """
                UPDATE issue_classifications
                SET domain = ?,
                    difficulty = ?,
                    difficulty_score = ?,
                    reasoning = ?,
                    model = ?,
                    source_hash = ?,
                    classified_at = ?
                WHERE issue_id = ?
                """,
                (domain, difficulty, difficulty_score, reasoning, model, source_hash, classified_at, issue_id),
            )
            return

        connection.execute(
            """
            INSERT INTO issue_classifications (
                issue_id, domain, difficulty, difficulty_score, reasoning, model, source_hash, classified_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (issue_id, domain, difficulty, difficulty_score, reasoning, model, source_hash, classified_at),
        )


def issue_classification_source(issue_id: int) -> str | None:
    with get_connection() as connection:
        row = _fetchone_row(
            connection.execute(
                "SELECT model FROM issue_classifications WHERE issue_id = ?",
                (issue_id,),
            )
        )
    return str(row["model"]) if row else None


def delete_non_gemini_classifications() -> int:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            DELETE FROM issue_classifications
            WHERE model != 'gemini'
            """
        )
        return cursor.rowcount


def mark_missing_issues(repository_id: int, seen_github_issue_ids: list[str], seen_at: str) -> int:
    with get_connection() as connection:
        if seen_github_issue_ids:
            placeholders = ",".join("?" for _ in seen_github_issue_ids)
            cursor = connection.execute(
                f"""
                UPDATE issues
                SET eligibility_status = 'closed_or_assigned',
                    state = 'unknown',
                    is_assigned = 1,
                    last_seen_at = ?
                WHERE repository_id = ?
                  AND github_issue_id NOT IN ({placeholders})
                  AND eligibility_status = 'eligible'
                """,
                [seen_at, repository_id, *seen_github_issue_ids],
            )
        else:
            cursor = connection.execute(
                """
                UPDATE issues
                SET eligibility_status = 'closed_or_assigned',
                    state = 'unknown',
                    is_assigned = 1,
                    last_seen_at = ?
                WHERE repository_id = ?
                  AND eligibility_status = 'eligible'
                """,
                (seen_at, repository_id),
            )
        return cursor.rowcount


def list_issue_cards(domain: str, difficulty: str, limit: int) -> list[IssueCard]:
    clauses = [
        "repositories.active = 1",
        "issues.eligibility_status = 'eligible'",
        "issue_classifications.model = 'gemini'",
    ]
    params: list[object] = []
    if domain != "all":
        clauses.append("issue_classifications.domain = ?")
        params.append(domain)
    if difficulty != "all":
        clauses.append("issue_classifications.difficulty = ?")
        params.append(difficulty)

    where_clause = " AND ".join(clauses)
    query = f"""
        SELECT
            issues.issue_number,
            issues.title,
            issues.body,
            issues.url,
            issues.author,
            issues.labels_json,
            issues.comments,
            issues.github_created_at,
            issues.github_updated_at,
            repositories.owner,
            repositories.name,
            repositories.description,
            issue_classifications.domain,
            issue_classifications.difficulty,
            issue_classifications.difficulty_score,
            issue_classifications.reasoning,
            issue_classifications.model
        FROM issues
        JOIN repositories ON repositories.id = issues.repository_id
        JOIN issue_classifications ON issue_classifications.issue_id = issues.id
        WHERE {where_clause}
        ORDER BY issue_classifications.difficulty_score DESC, issues.github_updated_at DESC
        LIMIT ?
    """
    params.append(limit)

    with get_connection() as connection:
        rows = _fetchall_rows(connection.execute(query, params))

    return [
        IssueCard(
            number=row["issue_number"],
            title=row["title"],
            body=row["body"],
            url=row["url"],
            author=row["author"],
            labels=json.loads(row["labels_json"]),
            comments=row["comments"],
            created_at=row["github_created_at"],
            updated_at=row["github_updated_at"],
            repository=f"{row['owner']}/{row['name']}",
            repository_description=row["description"],
            domain=row["domain"],
            difficulty=row["difficulty"],
            difficulty_score=row["difficulty_score"],
            reasoning=row["reasoning"],
            intelligence_source=row["model"],
        )
        for row in rows
    ]


def repository_slugs() -> list[str]:
    with get_connection() as connection:
        rows = _fetchall_rows(
            connection.execute(
                "SELECT owner, name FROM repositories WHERE active = 1 ORDER BY owner, name"
            )
        )
    return [f"{row['owner']}/{row['name']}" for row in rows]
