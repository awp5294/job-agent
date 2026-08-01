"""Job-board parsing, against payloads shaped like the real APIs.

The network is stubbed here on purpose — these tests are about turning each
board's response into the shape the rest of the app expects.
"""
import pytest

from sourcing import greenhouse, indeed, lever

GREENHOUSE_PAYLOAD = {
    "jobs": [
        {
            "id": 4567890,
            "title": "Staff Product Manager, Payments",
            "absolute_url": "https://boards.greenhouse.io/stripe/jobs/4567890",
            "location": {"name": "Remote - US"},
            "content": "<p>Own the payments platform roadmap.</p>" + "x" * 5000,
        },
        {
            "id": 4567891,
            "title": "Office Manager",
            "absolute_url": "https://boards.greenhouse.io/stripe/jobs/4567891",
            "location": {"name": "Dublin, Ireland"},
            "content": "<p>Keep the office running.</p>",
        },
        # No apply URL — nothing for the user to click, so it's dropped.
        {"id": 4567892, "title": "Ghost Role", "absolute_url": "", "location": {}},
    ]
}

LEVER_PAYLOAD = [
    {
        "id": "a1b2c3",
        "text": "Senior Product Manager, Growth",
        "hostedUrl": "https://jobs.lever.co/figma/a1b2c3",
        "categories": {"location": "Remote (US)", "commitment": "Full-time"},
        "descriptionPlain": "Own growth surface area end to end.",
    },
    {"id": "d4e5f6", "text": "No URL", "hostedUrl": "", "categories": {}},
]

INDEED_HTML = """
<html><body>
  <div class="job_seen_beacon">
    <h2 class="jobTitle"><a href="/rc/clk?jk=abc123"><span>Product Manager</span></a></h2>
    <div data-testid="company-name">Acme Corp</div>
    <div data-testid="text-location">Remote</div>
  </div>
  <div class="job_seen_beacon">
    <h2 class="jobTitle"><a href="https://example.com/job/2"><span>Senior PM</span></a></h2>
    <div data-testid="text-location">London</div>
  </div>
  <div class="job_seen_beacon"><p>an ad, not a job</p></div>
</body></html>
"""


class FakeResponse:
    def __init__(self, json_data=None, text=""):
        self._json, self.text = json_data, text

    def json(self):
        return self._json

    def raise_for_status(self):
        return None


class FakeClient:
    """Stands in for httpx.Client. Records the URLs it was asked for."""

    def __init__(self, response, requested):
        self._response, self._requested = response, requested

    def __call__(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, params=None):
        self._requested.append((url, params))
        return self._response


@pytest.fixture
def stub_http(monkeypatch):
    def install(module, response):
        requested = []
        monkeypatch.setattr(module.httpx, "Client", FakeClient(response, requested))
        return requested
    return install


# ── Greenhouse ─────────────────────────────────────────────────────────────

def test_greenhouse_parses_and_drops_unapplyable_jobs(stub_http):
    requested = stub_http(greenhouse, FakeResponse(json_data=GREENHOUSE_PAYLOAD))
    jobs = greenhouse.fetch_greenhouse_jobs("stripe")

    assert requested[0][0] == "https://boards-api.greenhouse.io/v1/boards/stripe/jobs"
    assert len(jobs) == 2

    first = jobs[0]
    assert first["source"] == "greenhouse"
    assert first["external_id"] == "4567890"
    assert first["title"] == "Staff Product Manager, Payments"
    assert first["company"] == "Stripe"
    assert first["apply_url"] == "https://boards.greenhouse.io/stripe/jobs/4567890"
    assert first["remote_type"] == "remote"
    # Descriptions are capped so one verbose posting can't blow up a prompt.
    assert len(first["description"]) == 3000

    assert jobs[1]["remote_type"] is None


def test_greenhouse_slug_becomes_a_readable_company_name(stub_http):
    stub_http(greenhouse, FakeResponse(json_data={"jobs": [
        {"id": 1, "title": "PM", "absolute_url": "https://x/1", "location": {"name": "NY"}}
    ]}))
    assert greenhouse.fetch_greenhouse_jobs("acme-labs")[0]["company"] == "Acme Labs"


def test_greenhouse_errors_propagate_to_the_digest(stub_http):
    class Boom(FakeResponse):
        def raise_for_status(self):
            raise RuntimeError("503 Service Unavailable")

    stub_http(greenhouse, Boom())
    with pytest.raises(RuntimeError, match="503"):
        greenhouse.fetch_greenhouse_jobs("stripe")


# ── Lever ──────────────────────────────────────────────────────────────────

def test_lever_parses_and_drops_unapplyable_jobs(stub_http):
    stub_http(lever, FakeResponse(json_data=LEVER_PAYLOAD))
    jobs = lever.fetch_lever_jobs("figma")

    assert len(jobs) == 1
    job = jobs[0]
    assert job["source"] == "lever"
    assert job["external_id"] == "a1b2c3"
    assert job["title"] == "Senior Product Manager, Growth"
    assert job["company"] == "Figma"
    assert job["apply_url"] == "https://jobs.lever.co/figma/a1b2c3"
    assert job["remote_type"] == "remote"
    assert job["description"] == "Own growth surface area end to end."


def test_lever_tolerates_missing_categories(stub_http):
    stub_http(lever, FakeResponse(json_data=[
        {"id": "z", "text": "PM", "hostedUrl": "https://jobs.lever.co/x/z"}
    ]))
    job = lever.fetch_lever_jobs("x")[0]
    assert job["location"] == ""
    assert job["remote_type"] is None


# ── Indeed ─────────────────────────────────────────────────────────────────

def test_indeed_parses_cards_and_skips_non_jobs(stub_http, monkeypatch):
    monkeypatch.setattr(indeed.time, "sleep", lambda s: None)
    stub_http(indeed, FakeResponse(text=INDEED_HTML))

    jobs = indeed.fetch_indeed_jobs(["Product Manager"], ["Remote"])
    assert len(jobs) == 2

    relative, absolute = jobs
    assert relative["apply_url"] == "https://www.indeed.com/rc/clk?jk=abc123"
    assert relative["company"] == "Acme Corp"
    assert relative["remote_type"] == "remote"
    # A card with no company element still yields a usable row.
    assert absolute["apply_url"] == "https://example.com/job/2"
    assert absolute["company"] == "Unknown"
    assert absolute["location"] == "London"


def test_indeed_ids_are_stable_across_runs(stub_http, monkeypatch):
    monkeypatch.setattr(indeed.time, "sleep", lambda s: None)
    stub_http(indeed, FakeResponse(text=INDEED_HTML))
    first = indeed.fetch_indeed_jobs(["PM"], ["Remote"])
    stub_http(indeed, FakeResponse(text=INDEED_HTML))
    second = indeed.fetch_indeed_jobs(["PM"], ["Remote"])

    assert [j["external_id"] for j in first] == [j["external_id"] for j in second]


def test_indeed_being_blocked_is_not_an_exception(stub_http, monkeypatch):
    """Indeed blocks scrapers routinely — an empty page must not break the digest."""
    monkeypatch.setattr(indeed.time, "sleep", lambda s: None)
    stub_http(indeed, FakeResponse(text="<html><body>Access denied</body></html>"))
    assert indeed.fetch_indeed_jobs(["PM"], ["Remote"]) == []


def test_indeed_bounds_the_number_of_searches(stub_http, monkeypatch):
    monkeypatch.setattr(indeed.time, "sleep", lambda s: None)
    requested = stub_http(indeed, FakeResponse(text=""))
    indeed.fetch_indeed_jobs(["a", "b", "c", "d", "e"], ["x", "y", "z"])
    # 3 titles x 2 locations, so one slow board can't stall the whole digest.
    assert len(requested) == 6
