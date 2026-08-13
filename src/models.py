"""Shared data models used across the whole agent.

Keeping these in one place means the resume parser, matcher, site adapters,
autofill engine, and tracker are all speaking the same language.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


@dataclass
class Experience:
    title: str
    company: str
    start_date: str = ""
    end_date: str = ""          # "" or "Present"
    description: str = ""


@dataclass
class Education:
    degree: str
    institution: str
    start_date: str = ""
    end_date: str = ""
    field_of_study: str = ""


@dataclass
class CandidateProfile:
    """Structured version of the user's resume + any extra details they gave.

    This is the single object every other module reads from — the resume
    parser builds it, and the matcher / autofill engine consume it.
    """
    full_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin_url: str = ""
    portfolio_url: str = ""
    summary: str = ""
    total_years_experience: float = 0.0
    skills: list[str] = field(default_factory=list)
    experience: list[Experience] = field(default_factory=list)
    education: list[Education] = field(default_factory=list)
    raw_resume_text: str = ""

    # Extra free-form details the user supplies directly (not in the resume)
    # e.g. desired salary, notice period, work authorization status.
    extra_details: dict = field(default_factory=dict)

    def to_prompt_context(self) -> str:
        """Render a compact text summary suitable for feeding to an LLM."""
        lines = [
            f"Name: {self.full_name}",
            f"Location: {self.location}",
            f"Total experience: {self.total_years_experience} years",
            f"Skills: {', '.join(self.skills)}",
            "",
            "Experience:",
        ]
        for exp in self.experience:
            lines.append(
                f"- {exp.title} at {exp.company} "
                f"({exp.start_date} – {exp.end_date or 'Present'}): {exp.description}"
            )
        lines.append("")
        lines.append("Education:")
        for edu in self.education:
            lines.append(f"- {edu.degree} in {edu.field_of_study}, {edu.institution}")
        if self.extra_details:
            lines.append("")
            lines.append("Additional details provided by candidate:")
            for k, v in self.extra_details.items():
                lines.append(f"- {k}: {v}")
        return "\n".join(lines)


class Site(str, Enum):
    LINKEDIN = "linkedin"
    INDEED = "indeed"
    NAUKRI = "naukri"


@dataclass
class JobPosting:
    site: Site
    job_id: str                 # site-specific unique id, used for dedupe
    title: str
    company: str
    location: str
    url: str
    description: str = ""
    posted_date: Optional[str] = None
    salary_text: Optional[str] = None
    easy_apply: bool = False


class ApplicationStatus(str, Enum):
    SKIPPED_LOW_MATCH = "skipped_low_match"
    PENDING_REVIEW = "pending_review"     # filled, waiting on human submit
    SUBMITTED = "submitted"
    FAILED = "failed"
    DUPLICATE = "duplicate"


@dataclass
class ApplicationRecord:
    job: JobPosting
    status: ApplicationStatus
    match_score: Optional[int] = None
    match_reasoning: str = ""
    applied_at: datetime = field(default_factory=datetime.utcnow)
    screenshot_path: Optional[str] = None
    error: Optional[str] = None
