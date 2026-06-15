from app.ingest.cleaning import MIN_CONTENT_CHARS, has_enough_content, is_usable_title


def test_rejects_lone_punctuation():
    # The real junk case observed in the data: a title reduced to ")".
    assert is_usable_title(")") is False


def test_rejects_empty_and_whitespace():
    assert is_usable_title("") is False
    assert is_usable_title("   ") is False


def test_rejects_below_threshold():
    # Two alphanumerics is below the 3-char threshold.
    assert is_usable_title("a-b") is False


def test_accepts_normal_title():
    assert is_usable_title("What's new in Python 3.15") is True


def test_accepts_accented_and_non_latin():
    # str.isalnum is Unicode-aware: accented and non-Latin titles stay usable.
    assert is_usable_title("café") is True
    assert is_usable_title("日本語") is True


def test_has_enough_content_rejects_empty_and_thin():
    assert has_enough_content("") is False
    assert has_enough_content(None) is False  # type: ignore[arg-type]
    assert has_enough_content("a one-sentence excerpt") is False


def test_has_enough_content_accepts_substantial():
    assert has_enough_content("x" * MIN_CONTENT_CHARS) is True


def test_has_enough_content_ignores_surrounding_whitespace():
    # Whitespace padding must not count toward the threshold.
    assert has_enough_content("   " + "x" * (MIN_CONTENT_CHARS - 1) + "   ") is False
