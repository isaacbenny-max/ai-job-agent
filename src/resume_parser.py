"""Parse a resume (PDF or DOCX) into a structured CandidateProfile.

Strategy:
1. Extract raw text with pdfplumber / python-docx.
2. Run cheap regex heuristics for the easy, high-precision fields
   (email, phone, LinkedIn URL).
3. Hand the raw text to the LLM (see src/llm.py) to pull out the harder
   structured fields (skills, experience, education, total years of
   experience) as JSON. This is far more robust than regex against the
   huge variety of resume layouts people use.

The LLM step is optional — if no ANTHROPIC_API_KEY is configured, we fall
back to a purely regex/heuristic parse so the rest of the pipeline still
works (with a less complete profile).
"""
from __future__ import annotations

import re
from pathlib import Path

from src.models import CandidateProfile, Education, Experience

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"(\+?\d{1,3}[-.\s]?)?\(?\d{3,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}")
LINKEDIN_RE = re.compile(r"(https?://)?(www\.)?linkedin\.com/in/[A-Za-z0-9\-_%/]+", re.I)
URL_RE = re.compile(r"https?://[^\s,]+")


def extract_text(resume_path: str) -> str:
    """Extract raw text from a PDF or DOCX resume file."""
    path = Path(resume_path)
    if not path.exists():
        raise FileNotFoundError(f"Resume not found at {resume_path}")

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf_text(path)
    elif suffix in (".docx", ".doc"):
        return _extract_docx_text(path)
    elif suffix == ".txt":
        return path.read_text(encoding="utf-8", errors="ignore")
    else:
        raise ValueError(f"Unsupported resume format: {suffix} (use .pdf, .docx, or .txt)")


def _extract_pdf_text(path: Path) -> str:
    import pdfplumber

    text_parts = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text_parts.append(page_text)
    return "\n".join(text_parts)


def _extract_docx_text(path: Path) -> str:
    import docx

    document = docx.Document(str(path))
    return "\n".join(p.text for p in document.paragraphs)


def _regex_pass(text: str, profile: CandidateProfile) -> None:
    """Fill in the fields regex can reliably extract."""
    email_match = EMAIL_RE.search(text)
    if email_match:
        profile.email = email_match.group(0)

    phone_match = PHONE_RE.search(text)
    if phone_match:
        candidate = phone_match.group(0).strip()
        digits = re.sub(r"\D", "", candidate)
        if len(digits) >= 7:  # avoid matching stray short numbers
            profile.phone = candidate

    linkedin_match = LINKEDIN_RE.search(text)
    if linkedin_match:
        url = linkedin_match.group(0)
        profile.linkedin_url = url if url.startswith("http") else f"https://{url}"

    # First non-empty line is very often the candidate's name.
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not EMAIL_RE.search(stripped) and len(stripped.split()) <= 5:
            profile.full_name = stripped
            break


def _llm_pass(text: str, profile: CandidateProfile) -> None:
    """Use the LLM to extract skills / experience / education as structured JSON.

    Falls back silently (leaving regex-only results) if no API key is set
    or the call fails for any reason — this keeps the pipeline usable
    offline / without credentials.
    """
    try:
        from src.llm import extract_resume_structure
    except Exception:
        return

    try:
        structured = extract_resume_structure(text)
    except Exception:
        return

    if not structured:
        return

    profile.summary = structured.get("summary", profile.summary)
    profile.location = structured.get("location", profile.location)
    profile.total_years_experience = structured.get(
        "total_years_experience", profile.total_years_experience
    )
    profile.skills = structured.get("skills", profile.skills) or profile.skills

    profile.experience = [
        Experience(
            title=e.get("title", ""),
            company=e.get("company", ""),
            start_date=e.get("start_date", ""),
            end_date=e.get("end_date", ""),
            description=e.get("description", ""),
        )
        for e in structured.get("experience", [])
    ] or profile.experience

    profile.education = [
        Education(
            degree=e.get("degree", ""),
            institution=e.get("institution", ""),
            start_date=e.get("start_date", ""),
            end_date=e.get("end_date", ""),
            field_of_study=e.get("field_of_study", ""),
        )
        for e in structured.get("education", [])
    ] or profile.education

    # Prefer an explicit name from the LLM if it found one (regex first-line
    # heuristic is often wrong for resumes with a header/logo line first).
    if structured.get("full_name"):
        profile.full_name = structured["full_name"]
    if structured.get("email"):
        profile.email = structured["email"]
    if structured.get("phone"):
        profile.phone = structured["phone"]


def parse_resume(resume_path: str, extra_details: dict | None = None) -> CandidateProfile:
    """Parse a resume file into a CandidateProfile.

    `extra_details` lets the caller merge in anything not present in the
    resume itself (e.g. desired salary, visa status, notice period) that
    the user typed in directly.
    """
    text = extract_text(resume_path)
    profile = CandidateProfile(raw_resume_text=text)

    _regex_pass(text, profile)
    _llm_pass(text, profile)

    if extra_details:
        profile.extra_details.update(extra_details)

    return profile
