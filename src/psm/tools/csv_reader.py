from __future__ import annotations
"""Load problems from a Google Sheet CSV export."""

import csv
from pathlib import Path

from psm.schemas.problem import RawProblem
from psm.config import settings


def load_problems(path: Path | None = None) -> list[RawProblem]:
    """Parse CSV into RawProblem objects.

    Expected columns: id, title, description, reported_by, date_reported, domain, tags
    """
    csv_path = path or settings.problems_csv_path
    if not csv_path.exists():
        raise FileNotFoundError(f"Problems CSV not found: {csv_path}")

    problems = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Normalize column names: strip whitespace, lowercase
            cleaned = {k.strip().lower().replace(" ", "_"): v.strip() for k, v in row.items()}
            problems.append(
                RawProblem(
                    id=cleaned.get("id", ""),
                    title=cleaned.get("title", ""),
                    description=cleaned.get("description", ""),
                    reported_by=cleaned.get("reported_by", ""),
                    date_reported=cleaned.get("date_reported", ""),
                    domain=cleaned.get("domain") or None,
                    tags=cleaned.get("tags") or None,
                )
            )
    return problems
