import pytest

from src.llm import LLMNotConfigured, _extract_json, extract_resume_structure


def test_extract_json_plain():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_with_markdown_fence():
    raw = "```json\n{\"score\": 80, \"reasoning\": \"solid match\"}\n```"
    assert _extract_json(raw) == {"score": 80, "reasoning": "solid match"}


def test_extract_json_with_surrounding_prose():
    raw = "Sure, here's the result:\n{\"score\": 42}\nHope that helps!"
    assert _extract_json(raw) == {"score": 42}


def test_extract_json_raises_when_no_json_present():
    with pytest.raises(ValueError):
        _extract_json("no json here at all")


def test_llm_not_configured_without_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMNotConfigured):
        extract_resume_structure("some resume text")
