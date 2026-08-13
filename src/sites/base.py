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
        self.credentials = credentials  # {"email": ..., "password": ...} — optional, see login()
        self.site_config = site_config  # this site's block from config.yaml

    def _pause_for_manual_login(self, hint: str = "") -> None:
        """Block until the human confirms they've finished logging in, in the
        real browser window this adapter is driving. Shared by every
        adapter so the message and behavior are consistent across sites.
        """
        extra = f" {hint}" if hint else ""
        print(
            f"\n[{self.site_name}] A browser window is open on the login page. Please log in "
            f"there yourself — Google sign-in, email/password, SSO, whatever you normally "
            f"use — and complete any 2FA or CAPTCHA it asks for.{extra}\n"
            f"[{self.site_name}] Once you're logged in, come back to this terminal and press "
            f"Enter to continue..."
        )
        input()

    @abstractmethod
    def login(self) -> bool:
        """Get to a logged-in state on this site. Returns True on success.

        Login is manual-first by design: we navigate to the site, check
        whether a reused persistent browser profile already has an active
        session, and if not, pause and ask the human to log in themselves
        in the visible browser window — with whichever method they
        actually use (Google/Microsoft SSO, email+password, 2FA, a
        CAPTCHA, whatever) — then continue once they confirm.

        This is deliberate, not a shortcut: typing a saved password into a
        Google/Microsoft sign-in page from an automated script is exactly
        the pattern those providers' bot-detection is built to catch, and
        getting flagged risks the underlying Google/Microsoft account, not
        just the job site account. Because each adapter uses a persistent
        browser profile (see main.py), this manual step is normally only
        needed once ever per site — every later run reuses the saved
        session automatically.
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
