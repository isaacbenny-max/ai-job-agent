"""Common interface every job site adapter implements.

Why an adapter pattern: LinkedIn, Indeed, and Naukri each have completely
different DOM structures, login flows, and "apply" mechanics. Isolating
that per-site mess behind one small interface means:
  - main.py / the orchestrator never needs to know which site it's talking to
  - adding a new site (Glassdoor, Wellfound, ...) means writing one new
    adapter file, nothing else changes
  - a selector breaking on one site (they change their HTML often) can't
    silently break the others

NOTE ON SELECTORS: LinkedIn/Indeed/Naukri change their markup frequently
and run active anti-bot detection. The CSS selectors in these adapters are
best-effort starting points, not guaranteed-stable — expect to need to
open the page, inspect the DOM, and adjust selectors after any site
redesign. This is normal maintenance for scraping-based tools, not a bug.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from playwright.sync_api import Page

from src.models import ApplicationRecord, CandidateProfile, JobPosting


class SiteAdapter(ABC):
    """One adapter instance = one site, one Playwright page/session."""

    site_name: str

    def __init__(self, page: Page, credentials: dict, site_config: dict):
        self.page = page
        self.credentials = credentials  # {"email": ..., "password": ...}
        self.site_config = site_config  # this site's block from config.yaml

    @abstractmethod
    def login(self) -> bool:
        """Log into the site. Returns True on success.

        Should be resilient to already-being-logged-in (e.g. via a reused
        persistent browser profile) and should NOT attempt to solve
        CAPTCHAs or 2FA itself — if one appears, pause and let the human
        operator handle it (the browser runs headed by default for this
        reason), then continue.
        """
        raise NotImplementedError

    @abstractmethod
    def search_jobs(self, search_config: dict) -> Iterator[JobPosting]:
        """Yield JobPosting objects matching the given search criteria.

        Should apply the site's own search/filter UI where possible
        (title, location, date posted) so we don't paginate through
        thousands of irrelevant results client-side.
        """
        raise NotImplementedError

    @abstractmethod
    def apply(
        self, job: JobPosting, profile: CandidateProfile, apply_config: dict
    ) -> ApplicationRecord:
        """Navigate to the job's application flow and fill it out.

        Respects apply_config["mode"] ("auto" vs "review") and
        apply_config["dry_run"] — see src/autofill.py for the shared
        form-filling engine every adapter should delegate to.
        """
        raise NotImplementedError
