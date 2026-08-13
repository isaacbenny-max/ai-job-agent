from src.models import ApplicationRecord, ApplicationStatus, JobPosting, Site
from src.tracker import ApplicationTracker


def make_job(job_id="1") -> JobPosting:
    return JobPosting(
        site=Site.INDEED,
        job_id=job_id,
        title="Backend Engineer",
        company="Acme",
        location="Remote",
        url="https://example.com/job/" + job_id,
    )


def test_new_job_not_seen(tmp_path):
    tracker = ApplicationTracker(str(tmp_path / "applications.db"))
    assert tracker.already_seen(make_job("1")) is False


def test_recorded_job_is_seen(tmp_path):
    tracker = ApplicationTracker(str(tmp_path / "applications.db"))
    job = make_job("1")
    tracker.record(ApplicationRecord(job=job, status=ApplicationStatus.SUBMITTED))
    assert tracker.already_seen(job) is True


def test_dedupe_is_per_site_and_job_id(tmp_path):
    tracker = ApplicationTracker(str(tmp_path / "applications.db"))
    job_indeed = JobPosting(
        site=Site.INDEED, job_id="1", title="A", company="B", location="C", url="u"
    )
    job_linkedin = JobPosting(
        site=Site.LINKEDIN, job_id="1", title="A", company="B", location="C", url="u"
    )
    tracker.record(ApplicationRecord(job=job_indeed, status=ApplicationStatus.SUBMITTED))

    assert tracker.already_seen(job_indeed) is True
    assert tracker.already_seen(job_linkedin) is False  # same job_id, different site


def test_count_applications_today_only_counts_submitted(tmp_path):
    tracker = ApplicationTracker(str(tmp_path / "applications.db"))
    tracker.record(ApplicationRecord(job=make_job("1"), status=ApplicationStatus.SUBMITTED))
    tracker.record(ApplicationRecord(job=make_job("2"), status=ApplicationStatus.SKIPPED_LOW_MATCH))
    tracker.record(ApplicationRecord(job=make_job("3"), status=ApplicationStatus.FAILED))

    assert tracker.count_applications_today() == 1


def test_re_recording_same_job_updates_status(tmp_path):
    tracker = ApplicationTracker(str(tmp_path / "applications.db"))
    job = make_job("1")
    tracker.record(ApplicationRecord(job=job, status=ApplicationStatus.PENDING_REVIEW))
    tracker.record(ApplicationRecord(job=job, status=ApplicationStatus.SUBMITTED))

    assert tracker.count_applications_today() == 1
    summary = tracker.summary()
    assert summary.get(ApplicationStatus.SUBMITTED.value) == 1


def test_summary_groups_by_status(tmp_path):
    tracker = ApplicationTracker(str(tmp_path / "applications.db"))
    tracker.record(ApplicationRecord(job=make_job("1"), status=ApplicationStatus.SUBMITTED))
    tracker.record(ApplicationRecord(job=make_job("2"), status=ApplicationStatus.SUBMITTED))
    tracker.record(ApplicationRecord(job=make_job("3"), status=ApplicationStatus.FAILED))

    summary = tracker.summary()
    assert summary[ApplicationStatus.SUBMITTED.value] == 2
    assert summary[ApplicationStatus.FAILED.value] == 1
