import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def _default_database_url() -> str:
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        database_path = Path(local_app_data) / "contriplace" / "issues.db"
    else:
        database_path = Path.cwd() / "data" / "issues.db"
    return f"sqlite:///{database_path.as_posix()}"

class Config:
    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
    GITHUB_API_URL = "https://api.github.com/graphql"
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_AI_API_KEY")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    GEMINI_MODELS = os.getenv(
        "GEMINI_MODELS",
        "gemini-3.1-flash-lite,gemini-2.5-flash-lite,gemini-3-flash-preview,gemini-2.5-flash",
    ).split(",")
    GEMINI_MODEL_RPMS = os.getenv(
        "GEMINI_MODEL_RPMS",
        "gemini-3.1-flash-lite:15,gemini-2.5-flash-lite:10,gemini-3-flash-preview:5,gemini-2.5-flash:5",
    )
    GEMINI_REQUESTS_PER_MINUTE = int(os.getenv("GEMINI_REQUESTS_PER_MINUTE", "5"))
    GEMINI_BATCH_SIZE = int(os.getenv("GEMINI_BATCH_SIZE", "10"))
    
    # Optional: database config (for storing issues later)
    DATABASE_URL = os.getenv("DATABASE_URL", _default_database_url())
    
    # Polling interval (minutes)
    POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))
    
    # Repositories to track (comma-separated owner/repo format)
    REPOS = os.getenv("REPOS", "").split(",") if os.getenv("REPOS") else []

    POPULAR_REPOS = [
        {
            "owner": "microsoft",
            "name": "vscode",
            "description": "Editor platform and desktop UX",
            "primary_domain": "frontend",
            "popularity": "very-high",
        },
        {
            "owner": "apache",
            "name": "superset",
            "description": "Data visualization, dashboards, SQL editor, and BI product UI",
            "primary_domain": "frontend",
            "popularity": "very-high",
        },
        {
            "owner": "grafana",
            "name": "grafana",
            "description": "Observability dashboards, data visualization, and admin UI",
            "primary_domain": "frontend",
            "popularity": "very-high",
        },
        {
            "owner": "storybookjs",
            "name": "storybook",
            "description": "Frontend component workshop, docs, and design-system tooling",
            "primary_domain": "frontend",
            "popularity": "high",
        },
        {
            "owner": "streamlit",
            "name": "streamlit",
            "description": "Python-powered data apps with an interactive web UI",
            "primary_domain": "frontend",
            "popularity": "high",
        },
        {
            "owner": "plotly",
            "name": "dash",
            "description": "Analytical web apps and data visualization UI",
            "primary_domain": "frontend",
            "popularity": "high",
        },
        {
            "owner": "metabase",
            "name": "metabase",
            "description": "Business intelligence dashboards and data exploration UI",
            "primary_domain": "frontend",
            "popularity": "high",
        },
        {
            "owner": "refinedev",
            "name": "refine",
            "description": "React framework for admin panels, dashboards, and internal tools",
            "primary_domain": "frontend",
            "popularity": "high",
        },
        {
            "owner": "twentyhq",
            "name": "twenty",
            "description": "Modern CRM with React product UI and full-stack workflows",
            "primary_domain": "frontend",
            "popularity": "high",
        },
    ]

    def repo_targets(self):
        from github_graphql import RepositoryTarget

        if self.REPOS:
            targets = []
            popular_by_slug = {
                f"{repo['owner']}/{repo['name']}": repo
                for repo in self.POPULAR_REPOS
            }
            for repo_slug in self.REPOS:
                owner, name = repo_slug.split("/", 1)
                repo_data = popular_by_slug.get(repo_slug, {})
                targets.append(
                    RepositoryTarget(
                        owner=owner,
                        name=name,
                        description=repo_data.get("description", ""),
                        primary_domain=repo_data.get("primary_domain", "other"),
                        popularity=repo_data.get("popularity", "high"),
                    )
                )
            return targets
        return [RepositoryTarget(**repo) for repo in self.POPULAR_REPOS]

config = Config()
