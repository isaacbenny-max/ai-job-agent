"""Naukri.com adapter.

Naukri's most common flow is a one-click "Apply" that uses the resume/
profile already stored on your Naukri account rather than a multi-step
form — so this adapter's `apply()` is simpler than LinkedIn/Indeed's.
Some listings ("Company Site" applies) redirect off-platform and are
skipped, same policy as the other two adapters.
"""
from __future__ import annotations

from typing import Iterator
from urllib.parse import quote

from src.autofill import fill_application_form
from src.models import ApplicationRecord, ApplicationStatus, CandidateProfile, JobPosting, Site
from src.sites.base import SiteAdapter

BASE_URL = "https://www.naukri.com"
LOGIN_URL = "https://www.naukri.com/nlogin/login"
HOMEPAGE_URL = "https://www.naukri.com/mnjuser/homepage"


class NaukriAdapter(SiteAdapter):
    site_name = "naukri"

    def login(self) -> bool:
        self.page.goto(HOMEPAGE_URL, wait_until="domcontentloaded")
        self.page.wait_for_timeout(1000)

        if self._is_logged_in():
            return True  # persistent browser profile already has an active session

        self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
        self._pause_for_manual_login("You should land on your Naukri homepage when done.")
        self.page.wait_for_timeout(1500)

        return self._is_logged_in()

    def _is_logged_in(self) -> bool:
        return "homepage" in self.page.url

    def search_jobs(self, search_config: dict) -> Iterator[JobPosting]:
        titles = search_config.get("titles", [])
        locations = search_config.get("locations", [""])

        for title in titles:
            for location in locations:
                slug_title = quote(title.replace(" ", "-").lower())
                slug_location = quote(location.replace(" ", "-").lower()) if location else ""
                url = f"{BASE_URL}/{slug_title}-jobs"
                if slug_location:
                    url += f"-in-{slug_location}"

                self.page.goto(url, wait_until="domcontentloaded")
                self.page.wait_for_timeout(2000)

                cards = self.page.locator("div.cust-job-tuple")
                count = cards.count()
                for i in range(count):
                    card = cards.nth(i)
                    try:
                        job_url = card.locator("a.title").get_attribute("href") or ""
                        job_id = job_url.rstrip("/").split("-")[-1] or f"nk-{title}-{location}-{i}"
                        job_title = card.locator("a.title").inner_text().strip()
                        company = card.locator("a.comp-name").inner_text().strip()
                        job_location = card.locator("span.locWdth").inner_text().strip()

                        yield JobPosting(
                            site=Site.NAUKRI,
                            job_id=job_id,
                            title=job_title,
                            company=company,
                            location=job_location,
                            url=job_url,
                            easy_apply=True,  # Naukri's native apply is the common case
                        )
                    except Exception:
                        continue

    def apply(
        self, job: JobPosting, profile: CandidateProfile, apply_config: dict
    ) -> ApplicationRecord:
        self.page.goto(job.url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(1500)

        try:
            job.description = self.page.locator("section.job-desc").inner_text()
        except Exception:
            pass

        apply_btn = self.page.locator("button#apply-button, button:has-text('Apply')")
        if apply_btn.count() == 0:
            return ApplicationRecord(
                job=job,
                status=ApplicationStatus.FAILED,
                error="No Apply button found on Naukri job page.",
            )
        apply_btn.first.click()
        self.page.wait_for_timeout(1500)

        # Naukri sometimes applies instantly with no extra form (uses your
        # stored profile) — in that case there won't be extra questions and
        # fill_application_form will just find the submit/confirmation state.
        return fill_application_form(
            page=self.page,
            job=job,
            profile=profile,
            apply_config=apply_config,
            submit_button_selector="button:has-text('Save & Continue'), button:has-text('Submit')",
            next_button_selector="button:has-text('Save & Continue')",
            review_button_selector="button:has-text('Continue')",
        )
