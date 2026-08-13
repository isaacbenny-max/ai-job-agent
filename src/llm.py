"""Thin wrapper around the Anthropic API for the three LLM tasks this agent
needs:

1. extract_resume_structure() - turn raw resume text into structured JSON.
2. score_job_match()          - rate how well a job posting fits the profile.
3. answer_application_question() - answer an open-ended screening question.

Centralizing all Claude calls here means the rest of the codebase never
touches the SDK directly, and it's the only file you'd need to change to
swap in a different provider (OpenAI, local model, etc).
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

DEFAULT_MODEL = "claude-sonnet-4-5"


class LLMNotConfigured(RuntimeError):
    """Raised when no ANTHROPIC_API_KEY is available."""


@lru_cache(maxsize=1)
def _client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMNotConfigured(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file — see .env.example."
        )
    import anthropic

    return anthropic.Anthropic(api_key=api_key)


def _call(system: str, user: str, model: str = DEFAULT_MODEL, max_tokens: int = 1500) -> str:
    client = _client()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(block.text for block in response.content if block.type == "text")


def _extract_json(raw: str) -> dict:
    """LLMs sometimes wrap JSON in prose or markdown fences — pull it out."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in LLM response: {raw[:200]!r}")
    return json.loads(text[start : end + 1])


RESUME_EXTRACTION_SYSTEM = """You extract structured data from resumes.
Respond with ONLY a JSON object (no prose, no markdown fences) matching this shape:

{
  "full_name": str,
  "email": str,
  "phone": str,
  "location": str,
  "summary": str,               // 1-2 sentence professional summary
  "total_years_experience": number,
  "skills": [str, ...],
  "experience": [
    {"title": str, "company": str, "start_date": str, "end_date": str, "description": str}
  ],
  "education": [
    {"degree": str, "institution": str, "field_of_study": str, "start_date": str, "end_date": str}
  ]
}

If a field is unknown, use an empty string, empty list, or 0 as appropriate. Never invent facts
not supported by the resume text."""


def extract_resume_structure(resume_text: str) -> dict:
    raw = _call(RESUME_EXTRACTION_SYSTEM, resume_text, max_tokens=2000)
    return _extract_json(raw)


MATCH_SCORE_SYSTEM = """You are an expert technical recruiter. Given a candidate's profile and a
job posting, judge how good a fit the candidate is for the role.

Respond with ONLY a JSON object (no prose, no markdown fences):
{
  "score": number,       // 0-100, how strong a match this is
  "reasoning": str,      // 1-3 sentences explaining the score
  "missing_requirements": [str, ...]   // key requirements the candidate doesn't clearly meet
}

Be honest and calibrated — most candidates are NOT a 90+ match for most jobs. Reserve 80+ for
genuinely strong matches on both skills and seniority level."""


def score_job_match(profile_context: str, job_title: str, company: str, job_description: str) -> dict:
    user = (
        f"CANDIDATE PROFILE:\n{profile_context}\n\n"
        f"JOB POSTING:\nTitle: {job_title}\nCompany: {company}\n\n{job_description}"
    )
    raw = _call(MATCH_SCORE_SYSTEM, user, max_tokens=500)
    return _extract_json(raw)


ANSWER_QUESTION_SYSTEM = """You are filling out a job application AS the candidate, in first
person, using ONLY the facts in their profile below. Be truthful, concise, and professional.

Rules:
- If the question asks for a number (years of experience, expected salary, notice period) and
  the profile has a clear answer or a stated default, give just that number/value.
- If the question is open-ended ("Why do you want to work here?"), write 2-4 genuine-sounding
  sentences grounded in the candidate's actual background — no generic filler.
- If you truly cannot answer from the given information, respond with exactly: UNKNOWN

Respond with ONLY the answer text — no labels, no quotes, no explanation."""


def answer_application_question(profile_context: str, question: str, job_context: str = "") -> str:
    user = f"CANDIDATE PROFILE:\n{profile_context}\n\n"
    if job_context:
        user += f"JOB CONTEXT:\n{job_context}\n\n"
    user += f"APPLICATION QUESTION:\n{question}"
    return _call(ANSWER_QUESTION_SYSTEM, user, max_tokens=300).strip()
