"""Load team onboarding context and solver playbooks."""


from psm.config import settings


def load_onboarding() -> str:
    """Load the team onboarding document. This becomes the orchestrator's system context."""
    path = settings.context_dir / "onboarding.md"
    if not path.exists():
        raise FileNotFoundError(
            f"Onboarding doc not found: {path}\n"
            "Create context/onboarding.md with your team structure, roles, and domains."
        )
    return path.read_text()


def load_job_description(agent_name: str) -> str:
    """Load a specific agent's job description."""
    path = settings.job_descriptions_dir / f"{agent_name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Job description not found: {path}")
    return path.read_text()


def load_playbook(solver_type: str) -> str:
    """Load a solver playbook by type name."""
    path = settings.playbooks_dir / f"{solver_type}.md"
    if not path.exists():
        raise FileNotFoundError(f"Playbook not found: {path}")
    return path.read_text()


def load_all_job_descriptions() -> dict[str, str]:
    """Load all job descriptions as a dict keyed by agent name."""
    result = {}
    if settings.job_descriptions_dir.exists():
        for path in settings.job_descriptions_dir.glob("*.md"):
            result[path.stem] = path.read_text()
    return result
