"""Thin wrapper around the Gemini API (Google AI Studio's free tier) for the
three LLM tasks this agent needs:

1. extract_resume_structure() - turn raw resume text into structured JSON.
2. score_job_match()          - rate how well a job posting fits the profile.
3. answer_application_question() - answer an open-ended screening question.

Why Gemini: Google AI Studio (https://aistudio.google.com/apikey) gives out
API keys with a genuinely free tier — no credit card, no trial period — which
is exactly what you want for a project like this that makes a lot of small
LLM calls (one match-score call per job posting, plus a handful of
question-answering calls per application). Get a key, drop it in `.env` as
GEMINI_API_KEY, and you're done.

This talks to the plain REST API (`generateContent`) with the `requests`
library rather than Google's SDK, specifically so this file has exactly one
lightweight dependency. Centralizing all Gemini calls here also means the
rest of the codebase never touches the API directly, and it's the only file
you'd need to change to swap in a different provider later.
"""
from __future__ import annotations

import json
import os

import requests

# gemini-2.0-flash was deprecated June 2026 — gemini-2.5-flash is the current
# stable, free-tier-eligible default as of this writing. Swap this (or set
# matching.model in config.yaml) if Google ships a newer default later;
# see https://ai.google.dev/gemini-api/docs/pricing for what's currently free.
DEFAULT_MODEL = "gemini-2.5-flash"

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
REQUEST_TIMEOUT_SECONDS = 60


class LLMNotConfigured(RuntimeError):
    """Raised when no GEMINI_API_KEY is available."""


def _api_key() -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise LLMNotConfigured(
            "GEMINI_API_KEY is not set. Get a free key at https://aistudio.google.com/apikey "
            "and add it to your .env file — see .env.example."
        )
    return api_key


def _call(
    system: str, user: str, model: str = DEFAULT_MODEL, max_tokens: int = 1500, temperature: float = 0.2
) -> str:
    api_key = _api_key()
    url = f"{API_BASE}/{model}:generateContent"

    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
        },
    }

    response = requests.post(
        url,
        params={"key": api_key},
        json=payload,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    if response.status_code == 429:
        raise RuntimeError(
            "Gemini free-tier rate limit hit. Slow down (increase "
            "apply.delay_between_applications_seconds) or wait and retry."
        )
    response.raise_for_status()
    data = response.json()

    try:
        candidate = data["candidates"][0]
    except (KeyError, IndexError) as exc:
        block_reason = data.get("promptFeedback", {}).get("blockReason")
        if block_reason:
            raise RuntimeError(f"Gemini blocked this request: {block_reason}") from exc
        raise RuntimeError(f"Unexpected Gemini response shape: {data}") from exc

    finish_reason = candidate.get("finishReason")
    if finish_reason not in (None, "STOP", "MAX_TOKENS"):
        raise RuntimeError(f"Gemini did not return a normal completion: {finish_reason}")

    parts = candidate.get("content", {}).get("parts", [])
    return "".join(part.get("text", "") for part in parts)


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


def extract_resume_structure(resume_text: str, model: str = DEFAULT_MODEL) -> dict:
    raw = _call(RESUME_EXTRACTION_SYSTEM, resume_text, model=model, max_tokens=2000)
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


def score_job_match(
    profile_context: str, job_title: str, company: str, job_description: str, model: str = DEFAULT_MODEL
) -> dict:
    user = (
        f"CANDIDATE PROFILE:\n{profile_context}\n\n"
        f"JOB POSTING:\nTitle: {job_title}\nCompany: {company}\n\n{job_description}"
    )
    raw = _call(MATCH_SCORE_SYSTEM, user, model=model, max_tokens=500)
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


def answer_application_question(
    profile_context: str, question: str, job_context: str = "", model: str = DEFAULT_MODEL
) -> str:
    user = f"CANDIDATE PROFILE:\n{profile_context}\n\n"
    if job_context:
        user += f"JOB CONTEXT:\n{job_context}\n\n"
    user += f"APPLICATION QUESTION:\n{question}"
    return _call(ANSWER_QUESTION_SYSTEM, user, model=model, max_tokens=300).strip()
