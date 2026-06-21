from types import SimpleNamespace

import pytest

from app.config import ControlledTopic
from app.review import article_reviewer as reviewer
from app.review.review_orchestrator import is_blocker

GOOD_TITLE = "A Real Article Title"
GOOD_CONTENT = "x" * 200  # comfortably above MIN_CONTENT_CHARS
THIN_CONTENT = "too short"
JUNK_TITLE = ")"


# ── is_blocker (pure) ─────────────────────────────────────────────────────────


def test_is_blocker_false_for_valid_record():
    assert is_blocker({"title": GOOD_TITLE, "content": GOOD_CONTENT}) is False


def test_is_blocker_true_for_junk_title():
    assert is_blocker({"title": JUNK_TITLE, "content": GOOD_CONTENT}) is True


def test_is_blocker_true_for_thin_content():
    assert is_blocker({"title": GOOD_TITLE, "content": THIN_CONTENT}) is True


def test_is_blocker_true_for_missing_fields():
    assert is_blocker({}) is True


# ── review_article — decision matrix (agent + scraper mocked) ─────────────────


class _FakeAgent:
    """Stand-in for the LLM client: with_structured_output(...).invoke(...) -> payload."""

    def __init__(self, payload):
        self._payload = payload

    def with_structured_output(self, schema):
        return self

    def invoke(self, messages):
        return self._payload


@pytest.fixture
def mock_agent(monkeypatch):
    """Wire a configured fake agent and a fixed allowed-topics vocabulary."""
    payload = SimpleNamespace(keywords=["kw"], topics=["AI"], summary="a summary")
    monkeypatch.setattr(reviewer, "get_mini_agent", lambda: _FakeAgent(payload))
    monkeypatch.setattr(
        reviewer,
        "get_settings",
        lambda: SimpleNamespace(available_topics=[ControlledTopic(name="AI")]),
    )
    return payload


def _set_scrape(monkeypatch, result):
    monkeypatch.setattr(reviewer, "_scrape", lambda url: result)


def test_returns_none_when_agent_not_configured(monkeypatch):
    monkeypatch.setattr(reviewer, "get_mini_agent", lambda: None)
    assert reviewer.review_article({"reference": "r", "title": GOOD_TITLE, "content": GOOD_CONTENT}) is None


def test_valid_record_is_annotated_without_scrape(mock_agent, monkeypatch):
    # A scrape here would be a bug: a valid record must not touch the source page.
    _set_scrape(monkeypatch, RuntimeError("should not scrape a valid record"))
    result = reviewer.review_article({"reference": "r", "title": GOOD_TITLE, "content": GOOD_CONTENT})
    assert result is not None
    assert result.rejected is False
    assert result.generated_summary is None
    assert result.recovered_title is None
    assert result.tags == ["AI"]


def test_blocker_without_url_is_rejected(mock_agent):
    result = reviewer.review_article({"reference": "r", "title": JUNK_TITLE, "content": "", "url": ""})
    assert result is not None
    assert result.rejected is True


def test_blocker_unreachable_is_retried(mock_agent, monkeypatch):
    _set_scrape(monkeypatch, None)  # unreachable → possibly transient
    result = reviewer.review_article(
        {"reference": "r", "title": JUNK_TITLE, "content": "", "url": "http://x"}
    )
    assert result is None


def test_blocker_recovers_title_from_source(mock_agent, monkeypatch):
    # Junk title but good content: the page title is read and persisted, no summary.
    _set_scrape(monkeypatch, {"title": "Recovered Real Title", "content": ""})
    result = reviewer.review_article(
        {"reference": "r", "title": JUNK_TITLE, "content": GOOD_CONTENT, "url": "http://x"}
    )
    assert result is not None
    assert result.rejected is False
    assert result.recovered_title == "Recovered Real Title"
    assert result.generated_summary is None


def test_blocker_recovers_content_from_source(mock_agent, monkeypatch):
    # Good title but thin content: the page content is summarized into `content`.
    _set_scrape(monkeypatch, {"title": "", "content": "y" * 300})
    result = reviewer.review_article(
        {"reference": "r", "title": GOOD_TITLE, "content": THIN_CONTENT, "url": "http://x"}
    )
    assert result is not None
    assert result.rejected is False
    assert result.generated_summary == "a summary"
    assert result.recovered_title is None


def test_blocker_reached_but_nothing_usable_is_rejected(mock_agent, monkeypatch):
    # Page reached but its title is junk and its content is thin → terminal reject.
    _set_scrape(monkeypatch, {"title": ")", "content": "short"})
    result = reviewer.review_article(
        {"reference": "r", "title": JUNK_TITLE, "content": "", "url": "http://x"}
    )
    assert result is not None
    assert result.rejected is True
