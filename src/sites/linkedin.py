"""LinkedIn adapter — uses LinkedIn's "Easy Apply" flow, which keeps the
whole application inside a modal on LinkedIn itself (no redirect to a
third-party ATS). Non-Easy-Apply postings redirect off-site to the
company's own application form, which `easy_apply_only: true` in
config.yaml skips by default since those forms are too varied to
automate generically.

IMPORTANT: LinkedIn's Terms of Service prohibit automated scraping /
bot-driven interaction with the platform. Running this adapter means
using Playwright to drive YOUR OWN logged-in session as if it were you
clicking around — LinkedIn can still detect and act on this (rate limits,
CAPTCHAs, temporary or permanent account restrictions). That risk is
borne entirely by the account owner. See the README's "Safety & ToS"
section before using this in `apply.mode: auto`.
"""
from __future__ import annotations

import re
from typing import Iterator

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from src.autofill import fill_application_form
from src.models import ApplicationRecord, ApplicationStatus, CandidateProfile, JobPosting, Site
from src.sites.base import SiteAdapter

LOGIN_URL = "https://www.linkedin.com/login"
JOBS_SEARCH_URL = "https://www.linkedin.com/jobs/search/"


class LinkedInAdapter(SiteAdapter):
    site_name = "linkedin"

    def login(self) -> bool:
        self.page.goto(LOGIN_URL, wait_until="domcontentloaded")

        # Already logged in via a persistent browser profile.
        if "/feed" in self.page.url or self.page.locator("input#session_key").count() == 0:
            if "login" not in self.page.url:
                return True

        self.page.fill("input#session_key", self.credentials.get("email", ""))
        self.page.fill("input#session_password", self.credentials.get("password", ""))
        self.page.click("button[type='submit']")

        try:
            self.page.wait_for_url(re.compile(r".*linkedin\.com/(feed|checkpoint).*"), timeout=15000)
        except PlaywrightTimeoutError:
            pass

        if "checkpoint" in self.page.url:
            print(
                "[linkedin] Security checkpoint / CAPTCHA / 2FA detected. "
                "Please complete it manually in the browser window, then press Enter here..."
            )
            input()

        return "feed" in self.page.url or self.page.locator("nav.global-nav").count() > 0

    def search_jobs(self, search_config: dict) -> Iterator[JobPosting]:
        titles = search_config.get("titles", [])
        locations = search_config.get("locations", [""])

        for title in titles:
            for location in locations:
                url = (
                    f"{JOBS_SEARCH_URL}?keywords={_url_encode(title)}"
                    f"&location={_url_encode(location)}"
                    f"&f_TPR=r{86400 * search_config.get('posted_within_days', 14)}"
                )
                self.page.goto(url, wait_until="domcontentloaded")
                self.page.wait_for_timeout(2000)

                cards = self.page.locator("div.job-card-container")
                count = cards.count()
                for i in range(count):
                    card = cards.nth(i)
                    try:
                        job_id = card.get_attribute("data-job-id") or f"li-{title}-{location}-{i}"
                        job_title = card.locator("a.job-card-list__title").inner_text().strip()
                        company = card.locator(".job-card-container__company-name").inner_text().strip()
                        job_location = card.locator(".job-card-container__metadata-item").first.inner_text().strip()
                        href = card.locator("a.job-card-list__title").get_attribute("href") or ""
                        job_url = f"https://www.linkedin.com{href.split('?')[0]}"
                        easy_apply = card.locator("text=Easy Apply").count() > 0

                        if self.site_config.get("easy_apply_only", True) and not easy_apply:
                            continue

                        yield JobPosting(
                            site=Site.LINKEDIN,
                            job_id=job_id,
                            title=job_title,
                            company=company,
                            location=job_location,
                            url=job_url,
                            easy_apply=easy_apply,
                        )
                    except Exception:
                        # A single malformed card shouldn't kill the whole search.
                        continue

    def apply(
        self, job: JobPosting, profile: CandidateProfile, apply_config: dict
    ) -> ApplicationRecord:
        self.page.goto(job.url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(1500)

        # Pull the full description now that we're on the job page (search
        # cards only show a snippet) so the matcher/LLM has full context.
        try:
            job.description = self.page.locator("div.jobs-description__content").inner_text()
        except Exception:
            pass

        easy_apply_btn = self.page.locator("button.jobs-apply-button")
        if easy_apply_btn.count() == 0:
            return ApplicationRecord(
                job=job,
                status=ApplicationStatus.FAILED,
                error="No Easy Apply button found (external application form).",
            )
        easy_apply_btn.first.click()
        self.page.wait_for_timeout(1000)

        return fill_application_form(
            page=self.page,
            job=job,
            profile=profile,
            apply_config=apply_config,
            submit_button_selector="button[aria-label='Submit application']",
            next_button_selector="button[aria-label='Continue to next step']",
            review_button_selector="button[aria-label='Review your application']",
        )


def _url_encode(s: str) -> str:
    from urllib.parse import quote

    return quote(s)
