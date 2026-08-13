"""Indeed adapter.

Indeed's own "Indeed Apply" flow is the analogue of LinkedIn's Easy Apply —
a multi-step form hosted on Indeed itself. Postings that redirect to an
external company ATS are skipped for the same reason LinkedIn's are: those
forms are too varied to fill generically and safely.
"""
from __future__ import annotations

import re
from typing import Iterator
from urllib.parse import quote

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from src.autofill import fill_application_form
from src.models import ApplicationRecord, ApplicationStatus, CandidateProfile, JobPosting, Site
from src.sites.base import SiteAdapter

BASE_URL = "https://www.indeed.com"
LOGIN_URL = "https://secure.indeed.com/auth"


class IndeedAdapter(SiteAdapter):
    site_name = "indeed"

    def login(self) -> bool:
        self.page.goto(LOGIN_URL, wait_until="domcontentloaded")

        if self.page.locator("input[type='email']").count() == 0:
            return True  # already authenticated via persistent profile

        self.page.fill("input[type='email']", self.credentials.get("email", ""))
        self.page.click("button[type='submit']")
        self.page.wait_for_timeout(1500)

        if self.page.locator("input[type='password']").count() > 0:
            self.page.fill("input[type='password']", self.credentials.get("password", ""))
            self.page.click("button[type='submit']")

        try:
            self.page.wait_for_url(re.compile(r".*indeed\.com/(?!auth).*"), timeout=15000)
        except PlaywrightTimeoutError:
            pass

        if self.page.locator("text=/verify|captcha/i").count() > 0:
            print(
                "[indeed] Verification / CAPTCHA detected. Please complete it manually "
                "in the browser window, then press Enter here..."
            )
            input()

        return "auth" not in self.page.url

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
