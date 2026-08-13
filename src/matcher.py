"""Decide whether a job posting is worth applying to.

Two layers of filtering, cheapest first:
1. Cheap deterministic rules from config.yaml (keyword excludes, salary
   floor, location/remote filters) — no API calls, filters out obvious
   non-matches instantly.
2. LLM-based semantic match score against the candidate's actual resume,
   which catches "technically matches keywords but wrong seniority /
   wrong stack" cases that keyword rules can't.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.models import CandidateProfile, JobPosting


@dataclass
class MatchResult:
    passed: bool
    score: int
    reasoning: str
    missing_requirements: list[str]


def passes_keyword_filters(job: JobPosting, search_config: dict) -> tuple[bool, str]:
    """Fast, free, deterministic pre-filter. Returns (passed, reason_if_rejected)."""
    text = f"{job.title} {job.description}".lower()

    for excluded in search_config.get("keywords_exclude", []):
        if excluded.lower() in text:
            return False, f"contains excluded keyword '{excluded}'"

    required = search_config.get("keywords_include", [])
    if required and not any(kw.lower() in text for kw in required):
        return False, f"missing all required keywords {required}"

    if search_config.get("remote_only") and "remote" not in f"{job.location} {text}".lower():
        return False, "not remote"

    return True, ""


def score_match(profile: CandidateProfile, job: JobPosting, min_score: int) -> MatchResult:
    """Run the LLM match scorer. Raises LLMNotConfigured if no API key is set —
    callers should catch that and either skip scoring or treat everything as
    passing, depending on how the agent is configured to run without an LLM.
    """
    from src.llm import score_job_match

    result = score_job_match(
        profile_context=profile.to_prompt_context(),
        job_title=job.title,
        company=job.company,
        job_description=job.description,
    )
    score = int(result.get("score", 0))
    return MatchResult(
        passed=score >= min_score,
        score=score,
        reasoning=result.get("reasoning", ""),
        missing_requirements=result.get("missing_requirements", []),
    )


def evaluate(
    profile: CandidateProfile, job: JobPosting, config: dict
) -> MatchResult:
    """Full pipeline: keyword filter, then LLM score. Always returns a MatchResult."""
    keyword_ok, reason = passes_keyword_filters(job, config.get("search", {}))
    if not keyword_ok:
        return MatchResult(passed=False, score=0, reasoning=reason, missing_requirements=[])

    min_score = config.get("matching", {}).get("min_match_score", 70)
    try:
        return score_match(profile, job, min_score)
    except Exception as exc:  # LLM not configured, rate-limited, etc.
        return MatchResult(
            passed=False,
            score=0,
            reasoning=f"could not score match: {exc}",
            missing_requirements=[],
        )
