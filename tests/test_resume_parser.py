import os
from pathlib import Path

import pytest

from src.resume_parser import extract_text, parse_resume

FIXTURE = Path(__file__).parent / "fixtures" / "sample_resume.txt"


def test_extract_text_reads_txt_resume():
    text = extract_text(str(FIXTURE))
    assert "Isaac Benny" in text
    assert "isaacbennyv@gmail.com" in text


def test_extract_text_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        extract_text("does/not/exist.pdf")


def test_extract_text_unsupported_format_raises(tmp_path):
    bad_file = tmp_path / "resume.xyz"
    bad_file.write_text("hello")
    with pytest.raises(ValueError):
        extract_text(str(bad_file))


def test_parse_resume_regex_fallback_without_llm(monkeypatch):
    # Ensure no API key is present so this exercises the pure-regex path,
    # not a real network call.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    profile = parse_resume(str(FIXTURE))

    assert profile.email == "isaacbennyv@gmail.com"
    assert profile.linkedin_url.startswith("http")
    assert "linkedin.com/in/isaacbennyv" in profile.linkedin_url
    assert profile.full_name  # first-line heuristic should find something
    assert profile.raw_resume_text  # full text retained for the LLM pass / matcher


def test_parse_resume_merges_extra_details():
    profile = parse_resume(str(FIXTURE), extra_details={"notice_period_days": 30})
    assert profile.extra_details["notice_period_days"] == 30


def test_to_prompt_context_includes_key_fields():
    profile = parse_resume(str(FIXTURE))
    context = profile.to_prompt_context()
    assert profile.full_name in context
    assert "Skills:" in context
    assert "Experience:" in context
