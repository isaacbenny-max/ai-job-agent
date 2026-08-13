"""Indeed adapter.

Indeed's own "Indeed Apply" flow is the analogue of LinkedIn's Easy Apply —
a multi-step form hosted on Indeed itself. Postings that redirect to an
external company ATS are skipped for the same reason LinkedIn's are: those
forms are too varied to fill generically and safely.
"""
from __future__ import annotations

from typing import Iterator
from urllib.parse import quote

from src.autofill import fill_application_form
from src.models import ApplicationRecord, ApplicationStatus, CandidateProfile, JobPosting, Site
from src.sites.base import SiteAdapter

BASE_URL = "https://www.indeed.com"
LOGIN_URL = "https://secure.indeed.com/auth"


class IndeedAdapter(SiteAdapter):
    site_name = "indeed"

    def login(self) -> bool:
        self.page.goto(BASE_URL, wait_until="domcontentloaded")
        self.page.wait_for_timeout(1000)

        if self._is_logged_in():
            return True  # persistent browser profile already has an active session

        self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
        self._pause_for_manual_login("You should land back on indeed.com signed in when done.")
        self.page.wait_for_timeout(1500)

        return self._is_logged_in()

    def _is_logged_in(self) -> bool:
        if "secure.indeed.com" in self.page.url or "/auth" in self.page.url:
            return False
        # Logged-out Indeed shows a "Sign in" link in the header; logged-in
        # shows an account menu instead. Absence of "Sign in" is our signal.
        return self.page.locator("text=/^Sign in$/i").count() == 0

    def search_jobs(self, search_config: dict) -> Iterator[JobPosting]:
        titles = search_config.get("titles", [])
        locations = search_config.get("locations", [""])
        days = search_config.get("posted_within_days", 14)
        fromage = min(days, 30)  # Indeed's `fromage` param caps at 30 days

        for title in titles:
            for location in locations:
                url = (
                    f"{BASE_URL}/jobs?q={quote(title)}&l={quote(location)}"
                    f"&fromage={fromage}&sort=date"
                )
                self.page.goto(url, wait_until="domcontentloaded")
                self.page.wait_for_timeout(2000)

                cards = self.page.locator("div.job_seen_beacon")
                count = cards.count()
                for i in range(count):
                    card = cards.nth(i)
                    try:
                        job_id = card.get_attribute("data-jk") or f"in-{title}-{location}-{i}"
                        job_title = card.locator("h2.jobTitle span").first.inner_text().strip()
                        company = card.locator("span[data-testid='company-name']").inner_text().strip()
                        job_location = card.locator(
                            "div[data-testid='text-location']"
                        ).inner_text().strip()
                        href = card.locator("h2.jobTitle a").get_attribute("href") or ""
                        job_url = f"{BASE_URL}{href}" if href.startswith("/") else href
                        easy_apply = card.locator("text=/Easily apply|Indeed Apply/i").count() > 0

                        yield JobPosting(
                            site=Site.INDEED,
                            job_id=job_id,
                            title=job_title,
                            company=company,
                            location=job_location,
                            url=job_url,
                            easy_apply=easy_apply,
                        )
                    except Exception:
                        continue

    def apply(
        self, job: JobPosting, profile: CandidateProfile, apply_config: dict
    ) -> ApplicationRecord:
        self.page.goto(job.url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(1500)

        try:
            job.description = self.page.locator("#jobDescriptionText").inner_text()
        except Exception:
            pass

        apply_btn = self.page.locator("button#indeedApplyButton, button:has-text('Apply now')")
        if apply_btn.count() == 0:
            return ApplicationRecord(
                job=job,
                status=ApplicationStatus.FAILED,
                error="No Indeed Apply button found (external application form).",
            )
        apply_btn.first.click()
        self.page.wait_for_timeout(1500)

        # Indeed Apply opens in an iframe overlay.
        frame = self.page.frame_locator("iframe#indeedapply-modal-iframe")

        return fill_application_form(
            page=self.page,
            job=job,
            profile=profile,
            apply_config=apply_config,
            submit_button_selector="button:has-text('Submit your application')",
            next_button_selector="button:has-text('Continue')",
            review_button_selector="button:has-text('Review your application')",
            frame=frame,
        )
