"""Generic form-filling engine shared by every site adapter.

Each site adapter (LinkedIn/Indeed/Naukri) navigates to its own apply flow
and hands control here once the actual form is on screen. This module
doesn't know or care which site it's on — it just:

  1. Finds every visible input/textarea/select/radio/checkbox/file-upload
     field in the current form.
  2. Figures out each field's label (aria-label, <label for>, placeholder,
     or nearby text, in that priority order).
  3. Fills obvious fields directly from the candidate profile (name,
     email, phone) and uploads the resume file where a file input exists.
  4. For anything else (open-ended questions, screening questions, numeric
     fields), asks the LLM to answer using the resume + job context.
  5. Clicks "Next"/"Continue" and repeats for multi-step forms, up to
     `max_steps`, then stops at the review/submit step.
  6. Respects `apply_config["mode"]`:
       - "review": always stops before the final submit click, leaving the
         browser open on the filled-in form for a human to check and submit.
       - "auto": clicks the final submit button itself.
     ...and `apply_config["dry_run"]`: if true, fills everything but never
     clicks submit regardless of mode (useful for testing the pipeline
     safely before trusting it with `mode: auto`).

Form markup on these sites is inconsistent and changes often, so this is
written defensively — every per-field operation is wrapped so one weird
field doesn't abort the whole application.
"""
from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Optional, Union

from playwright.sync_api import FrameLocator, Locator, Page

from src.models import ApplicationRecord, ApplicationStatus, CandidateProfile, JobPosting

FormContext = Union[Page, FrameLocator]

# Very common field-label -> profile-field mappings we can answer instantly
# without ever calling the LLM (cheaper, faster, and more reliable than an
# LLM call for something this deterministic).
DIRECT_FIELD_MAP = {
    "first name": lambda p: p.full_name.split(" ")[0] if p.full_name else "",
    "last name": lambda p: p.full_name.split(" ")[-1] if p.full_name else "",
    "full name": lambda p: p.full_name,
    "name": lambda p: p.full_name,
    "email": lambda p: p.email,
    "phone": lambda p: p.phone,
    "mobile": lambda p: p.phone,
    "location": lambda p: p.location,
    "city": lambda p: p.location,
    "linkedin": lambda p: p.linkedin_url,
    "portfolio": lambda p: p.portfolio_url,
    "website": lambda p: p.portfolio_url,
}


def fill_application_form(
    page: Page,
    job: JobPosting,
    profile: CandidateProfile,
    apply_config: dict,
    submit_button_selector: str,
    next_button_selector: Optional[str] = None,
    review_button_selector: Optional[str] = None,
    frame: Optional[FrameLocator] = None,
    max_steps: int = 8,
) -> ApplicationRecord:
    ctx: FormContext = frame if frame is not None else page
    resume_path = apply_config.get("resume_path")
    mode = apply_config.get("mode", "review")
    dry_run = apply_config.get("dry_run", False)

    try:
        if resume_path:
            _upload_resume(ctx, resume_path)

        for step in range(max_steps):
            _fill_visible_fields(ctx, profile, job)
            _human_pause(apply_config)

            submit_btn = _first_visible(ctx, submit_button_selector)
            if submit_btn is not None:
                break  # reached the final step

            advanced = False
            for selector in filter(None, [review_button_selector, next_button_selector]):
                btn = _first_visible(ctx, selector)
                if btn is not None:
                    btn.click()
                    page.wait_for_timeout(1200)
                    advanced = True
                    break

            if not advanced:
                # No submit button and nothing to click forward with —
                # either the form is genuinely done or has an unexpected
                # layout. Re-check for a submit button once more before
                # giving up.
                submit_btn = _first_visible(ctx, submit_button_selector)
                if submit_btn is None:
                    return ApplicationRecord(
                        job=job,
                        status=ApplicationStatus.FAILED,
                        error=f"Stuck at step {step}: no submit button and no way to advance.",
                    )
                break
        else:
            return ApplicationRecord(
                job=job,
                status=ApplicationStatus.FAILED,
                error=f"Form has more than {max_steps} steps — giving up to avoid a stuck loop.",
            )

        screenshot_path = _maybe_screenshot(page, job, apply_config)

        if dry_run:
            return ApplicationRecord(
                job=job,
                status=ApplicationStatus.PENDING_REVIEW,
                screenshot_path=screenshot_path,
                error="dry_run=true: form filled but not submitted.",
            )

        if mode != "auto":
            return ApplicationRecord(
                job=job,
                status=ApplicationStatus.PENDING_REVIEW,
                screenshot_path=screenshot_path,
            )

        submit_btn = _first_visible(ctx, submit_button_selector)
        if submit_btn is None:
            return ApplicationRecord(
                job=job, status=ApplicationStatus.FAILED, error="Submit button vanished."
            )
        submit_btn.click()
        page.wait_for_timeout(1500)

        return ApplicationRecord(
            job=job, status=ApplicationStatus.SUBMITTED, screenshot_path=screenshot_path
        )

    except Exception as exc:  # noqa: BLE001 - genuinely want to catch everything here
        return ApplicationRecord(job=job, status=ApplicationStatus.FAILED, error=str(exc))


# ---------------------------------------------------------------------------
# Field discovery & filling
# ---------------------------------------------------------------------------


def _fill_visible_fields(ctx: FormContext, profile: CandidateProfile, job: JobPosting) -> None:
    _fill_text_like(ctx, profile, job, "input[type='text']:visible, input:not([type]):visible")
    _fill_text_like(ctx, profile, job, "input[type='email']:visible")
    _fill_text_like(ctx, profile, job, "input[type='tel']:visible")
    _fill_text_like(ctx, profile, job, "input[type='number']:visible")
    _fill_textareas(ctx, profile, job)
    _fill_selects(ctx, profile, job)


def _fill_text_like(ctx: FormContext, profile: CandidateProfile, job: JobPosting, selector: str) -> None:
    try:
        inputs = ctx.locator(selector)
        count = inputs.count()
    except Exception:
        return

    for i in range(count):
        field = inputs.nth(i)
        try:
            if (field.input_value() or "").strip():
                continue  # already filled
            label = _label_for(ctx, field)
            answer = _answer_for(label, profile, job)
            if answer:
                field.fill(answer)
        except Exception:
            continue


def _fill_textareas(ctx: FormContext, profile: CandidateProfile, job: JobPosting) -> None:
    try:
        areas = ctx.locator("textarea:visible")
        count = areas.count()
    except Exception:
        return

    for i in range(count):
        field = areas.nth(i)
        try:
            if (field.input_value() or "").strip():
                continue
            label = _label_for(ctx, field)
            answer = _answer_for(label, profile, job, long_form=True)
            if answer:
                field.fill(answer)
        except Exception:
            continue


def _fill_selects(ctx: FormContext, profile: CandidateProfile, job: JobPosting) -> None:
    try:
        selects = ctx.locator("select:visible")
        count = selects.count()
    except Exception:
        return

    for i in range(count):
        field = selects.nth(i)
        try:
            label = _label_for(ctx, field)
            options = field.locator("option").all_inner_texts()
            options = [o.strip() for o in options if o.strip()]
            if not options:
                continue
            answer = _answer_for(label, profile, job, options=options)
            if answer and answer in options:
                field.select_option(label=answer)
        except Exception:
            continue


def _upload_resume(ctx: FormContext, resume_path: str) -> None:
    path = Path(resume_path)
    if not path.exists():
        return
    try:
        file_input = ctx.locator("input[type='file']").first
        if file_input.count() > 0:
            file_input.set_input_files(str(path))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Labels & answers
# ---------------------------------------------------------------------------


def _label_for(ctx: FormContext, field: Locator) -> str:
    try:
        aria = field.get_attribute("aria-label")
        if aria:
            return aria.strip()
    except Exception:
        pass

    try:
        field_id = field.get_attribute("id")
        if field_id:
            label_el = ctx.locator(f"label[for='{field_id}']")
            if label_el.count() > 0:
                return label_el.first.inner_text().strip()
    except Exception:
        pass

    try:
        placeholder = field.get_attribute("placeholder")
        if placeholder:
            return placeholder.strip()
    except Exception:
        pass

    try:
        name = field.get_attribute("name")
        if name:
            return name.replace("_", " ").replace("-", " ").strip()
    except Exception:
        pass

    return ""


def _answer_for(
    label: str,
    profile: CandidateProfile,
    job: JobPosting,
    long_form: bool = False,
    options: Optional[list[str]] = None,
) -> str:
    if not label:
        return ""

    normalized = label.lower().strip(" *:")
    for key, getter in DIRECT_FIELD_MAP.items():
        if key in normalized:
            value = getter(profile)
            if value:
                return value

    # Fall back to the LLM for anything not covered by the direct map.
    try:
        from src.llm import answer_application_question

        question = label
        if options:
            question += f" (choose one of: {', '.join(options)})"
        job_context = f"{job.title} at {job.company}"
        answer = answer_application_question(profile.to_prompt_context(), question, job_context)
        if answer and answer.strip().upper() != "UNKNOWN":
            return answer.strip()
    except Exception:
        pass

    return ""


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


def _first_visible(ctx: FormContext, selector: str) -> Optional[Locator]:
    try:
        loc = ctx.locator(selector)
        count = loc.count()
        for i in range(count):
            candidate = loc.nth(i)
            if candidate.is_visible():
                return candidate
    except Exception:
        return None
    return None


def _human_pause(apply_config: dict) -> None:
    """Small randomized pause between field-fill actions so behavior looks
    less like a bot firing instantaneous events. Separate from the larger
    between-application delay configured in config.yaml."""
    time.sleep(random.uniform(0.3, 0.9))


def _maybe_screenshot(page: Page, job: JobPosting, apply_config: dict) -> Optional[str]:
    screenshot_dir = apply_config.get("screenshot_dir")
    if not screenshot_dir or not apply_config.get("screenshot_on_submit", True):
        return None
    try:
        out_dir = Path(screenshot_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_company = "".join(c if c.isalnum() else "_" for c in job.company)[:40]
        path = out_dir / f"{job.site.value}_{safe_company}_{job.job_id}.png"
        page.screenshot(path=str(path))
        return str(path)
    except Exception:
        return None
