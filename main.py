#!/usr/bin/env python3
"""AI Job Agent — CLI entrypoint.

Usage:
    python main.py apply --config config.yaml
    python main.py apply --config config.yaml --dry-run
    python main.py show-profile --resume resumes/resume.pdf

See README.md for full setup instructions. Short version:
    1. cp .env.example .env   and fill in your API key + site credentials
    2. put your resume at the path set in config.yaml (files.resume_path)
    3. edit config.yaml (titles, locations, apply.mode, etc.)
    4. python main.py apply
"""
from __future__ import annotations

import random
import time
from pathlib import Path

import click
import yaml
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from rich.console import Console
from rich.table import Table

from src.matcher import evaluate
from src.models import ApplicationStatus, Site
from src.resume_parser import parse_resume
from src.tracker import ApplicationTracker

console = Console()

SITE_ADAPTERS = {}  # populated lazily in _load_adapters() to avoid importing
                     # playwright-dependent modules until they're needed


def _load_adapters():
    from src.sites.linkedin import LinkedInAdapter
    from src.sites.indeed import IndeedAdapter
    from src.sites.naukri import NaukriAdapter

    SITE_ADAPTERS.update(
        {
            Site.LINKEDIN: LinkedInAdapter,
            Site.INDEED: IndeedAdapter,
            Site.NAUKRI: NaukriAdapter,
        }
    )


def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


@click.group()
def cli():
    """AI Job Agent — an agent that applies to jobs on your behalf."""
    load_dotenv()


@cli.command()
@click.option("--resume", default=None, help="Path to your resume (overrides config.yaml).")
def show_profile(resume: str | None):
    """Parse your resume and print the structured profile the agent will use.

    Run this first to sanity-check that resume parsing worked before
    trusting the agent to apply to jobs with it.
    """
    config = load_config("config.yaml")
    resume_path = resume or config["files"]["resume_path"]

    console.print(f"[bold]Parsing resume:[/bold] {resume_path}")
    profile = parse_resume(resume_path)

    table = Table(title="Candidate Profile", show_lines=True)
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Name", profile.full_name)
    table.add_row("Email", profile.email)
    table.add_row("Phone", profile.phone)
    table.add_row("Location", profile.location)
    table.add_row("LinkedIn", profile.linkedin_url)
    table.add_row("Years experience", str(profile.total_years_experience))
    table.add_row("Skills", ", ".join(profile.skills))
    table.add_row("Experience entries", str(len(profile.experience)))
    table.add_row("Education entries", str(len(profile.education)))
    console.print(table)

    if not profile.email or not profile.skills:
        console.print(
            "\n[yellow]Heads up: some key fields are empty. If GEMINI_API_KEY isn't set "
            "in .env, parsing falls back to regex-only extraction, which misses skills and "
            "experience. Get a free key at https://aistudio.google.com/apikey and add it "
            "for a much more complete profile.[/yellow]"
        )


@cli.command()
@click.option("--config", "config_path", default="config.yaml", help="Path to config.yaml.")
@click.option("--dry-run", is_flag=True, default=False, help="Fill forms but never submit.")
@click.option(
    "--site",
    "only_sites",
    multiple=True,
    type=click.Choice([s.value for s in Site]),
    help="Restrict this run to specific site(s). Repeatable.",
)
def apply(config_path: str, dry_run: bool, only_sites: tuple[str, ...]):
    """Search configured job sites, match against your resume, and apply."""
    import os

    _load_adapters()
    config = load_config(config_path)

    if dry_run:
        config["apply"]["dry_run"] = True

    resume_path = config["files"]["resume_path"]
    if not Path(resume_path).exists():
        console.print(
            f"[red]Resume not found at {resume_path}. Put your resume there or update "
            f"files.resume_path in {config_path}.[/red]"
        )
        raise SystemExit(1)

    console.print(f"[bold]Parsing resume:[/bold] {resume_path}")
    profile = parse_resume(resume_path)
    console.print(f"Parsed profile for [bold]{profile.full_name or 'candidate'}[/bold].\n")

    tracker = ApplicationTracker(config["tracking"]["db_path"])

    already_today = tracker.count_applications_today()
    daily_cap = config["apply"]["max_applications_per_day"]
    if already_today >= daily_cap:
        console.print(
            f"[yellow]Already submitted {already_today} applications today "
            f"(cap: {daily_cap}). Nothing more to do — try again tomorrow.[/yellow]"
        )
        return

    run_cap = config["apply"]["max_applications_per_run"]
    remaining_budget = min(run_cap, daily_cap - already_today)

    sites_to_run = [
        site
        for site in Site
        if config["sites"].get(site.value, {}).get("enabled", False)
        and (not only_sites or site.value in only_sites)
    ]
    if not sites_to_run:
        console.print("[yellow]No sites enabled in config (or --site filter excluded all).[/yellow]")
        return

    credentials = {
        site: {
            "email": os.environ.get(f"{site.value.upper()}_EMAIL", ""),
            "password": os.environ.get(f"{site.value.upper()}_PASSWORD", ""),
        }
        for site in sites_to_run
    }

    apply_config = dict(config["apply"])
    apply_config["resume_path"] = resume_path
    apply_config["screenshot_dir"] = config["tracking"].get("screenshot_dir")
    apply_config["screenshot_on_submit"] = config["tracking"].get("screenshot_on_submit", True)

    submitted_count = 0
    profile_root = os.environ.get("BROWSER_PROFILE_DIR", "./data/browser_profile")

    with sync_playwright() as p:
        browser_conf = config.get("browser", {})

        for site in sites_to_run:
            if submitted_count >= remaining_budget:
                break

            console.rule(f"[bold blue]{site.value}")

            # Each site gets its own persistent browser profile (its own
            # cookies/local-storage on disk), so once you log in manually
            # the first time — Google sign-in, email/password, whatever —
            # every later run reuses that session automatically.
            site_profile_dir = str(Path(profile_root) / site.value)
            context = p.chromium.launch_persistent_context(
                site_profile_dir,
                headless=browser_conf.get("headless", False),
                slow_mo=browser_conf.get("slow_mo_ms", 0),
            )
            page = context.pages[0] if context.pages else context.new_page()

            adapter_cls = SITE_ADAPTERS[site]
            adapter = adapter_cls(page, credentials[site], config["sites"][site.value])

            if not adapter.login():
                console.print(f"[red]Login failed for {site.value} — skipping this site.[/red]")
                context.close()
                continue

            for job in adapter.search_jobs(config["search"]):
                if submitted_count >= remaining_budget:
                    break

                if tracker.already_seen(job):
                    continue

                console.print(f"Evaluating: [bold]{job.title}[/bold] @ {job.company} ({job.location})")
                match = evaluate(profile, job, config)

                if not match.passed:
                    console.print(f"  [dim]-> skipped (score {match.score}): {match.reasoning}[/dim]")
                    from src.models import ApplicationRecord

                    tracker.record(
                        ApplicationRecord(
                            job=job,
                            status=ApplicationStatus.SKIPPED_LOW_MATCH,
                            match_score=match.score,
                            match_reasoning=match.reasoning,
                        )
                    )
                    continue

                console.print(f"  [green]-> match score {match.score}, applying...[/green]")
                record = adapter.apply(job, profile, apply_config)
                record.match_score = match.score
                record.match_reasoning = match.reasoning
                tracker.record(record)

                if record.status == ApplicationStatus.SUBMITTED:
                    submitted_count += 1
                    console.print("  [bold green]Submitted![/bold green]")
                elif record.status == ApplicationStatus.PENDING_REVIEW:
                    console.print(
                        "  [yellow]Filled and waiting for manual review/submit "
                        "(apply.mode is 'review' or dry_run is true).[/yellow]"
                    )
                else:
                    console.print(f"  [red]Failed: {record.error}[/red]")

                lo, hi = apply_config.get("delay_between_applications_seconds", [30, 90])
                time.sleep(random.uniform(lo, hi))

            context.close()

    console.rule("[bold]Run summary")
    console.print(tracker.summary())


if __name__ == "__main__":
    cli()
