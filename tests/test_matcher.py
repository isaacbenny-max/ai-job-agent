from src.matcher import evaluate, passes_keyword_filters
from src.models import CandidateProfile, JobPosting, Site


def make_job(**overrides) -> JobPosting:
    defaults = dict(
        site=Site.LINKEDIN,
        job_id="123",
        title="Backend Engineer",
        company="Acme",
        location="Remote",
        url="https://example.com/job/123",
        description="We need a Python backend engineer with Django experience.",
    )
    defaults.update(overrides)
    return JobPosting(**defaults)


def test_passes_keyword_filters_rejects_excluded_keyword():
    job = make_job(description="This is a commission only sales role.")
    search_config = {"keywords_exclude": ["commission only"]}
    passed, reason = passes_keyword_filters(job, search_config)
    assert not passed
    assert "commission only" in reason


def test_passes_keyword_filters_requires_include_keyword():
    job = make_job(title="Frontend Engineer", description="React and CSS work.")
    search_config = {"keywords_include": ["python"]}
    passed, _ = passes_keyword_filters(job, search_config)
    assert not passed


def test_passes_keyword_filters_passes_clean_job():
    job = make_job()
    passed, reason = passes_keyword_filters(job, {})
    assert passed
    assert reason == ""


def test_passes_keyword_filters_remote_only():
    job = make_job(location="New York, NY (On-site)")
    passed, reason = passes_keyword_filters(job, {"remote_only": True})
    assert not passed
    assert "remote" in reason


def test_evaluate_short_circuits_on_keyword_filter_without_calling_llm(monkeypatch):
    """evaluate() should never reach the LLM call if the keyword filter
    already rejects the job — this keeps evaluate() usable/testable even
    with zero API credentials configured."""
    job = make_job(description="unpaid internship, commission only")
    config = {
        "search": {"keywords_exclude": ["unpaid"]},
        "matching": {"min_match_score": 70},
    }
    profile = CandidateProfile(full_name="Test Candidate")

    result = evaluate(profile, job, config)

    assert result.passed is False
    assert result.score == 0
    assert "unpaid" in result.reasoning


def test_evaluate_handles_llm_failure_gracefully(monkeypatch):
    """If the LLM call fails (e.g. no API key configured), evaluate() should
    fail closed (never apply) rather than raising."""
    job = make_job()
    config = {"search": {}, "matching": {"min_match_score": 70}}
    profile = CandidateProfile(full_name="Test Candidate")

    result = evaluate(profile, job, config)

    assert result.passed is False
    assert "could not score match" in result.reasoning
