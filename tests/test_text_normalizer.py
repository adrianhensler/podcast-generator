from app.services.text_normalizer import normalize_for_speech


def test_dollar_simple():
    assert normalize_for_speech("$100") == "100 dollars"


def test_dollar_with_commas():
    assert normalize_for_speech("$1,500") == "1500 dollars"


def test_dollar_millions():
    assert normalize_for_speech("$3.5 million") == "3.5 million dollars"


def test_dollar_billions():
    assert normalize_for_speech("$2.1 billion") == "2.1 billion dollars"


def test_percentage():
    result = normalize_for_speech("3.5%")
    assert "3.5 percent" in result


def test_eg_expansion():
    result = normalize_for_speech("e.g. something")
    assert "for example" in result


def test_ie_expansion():
    result = normalize_for_speech("i.e. that means")
    assert "that is" in result


def test_etc_expansion():
    result = normalize_for_speech("cats, dogs, etc.")
    assert "etcetera" in result


def test_url_removal():
    result = normalize_for_speech("Visit https://example.com for more.")
    assert "https://" not in result
    assert "example.com" not in result


def test_bold_markdown():
    result = normalize_for_speech("**bold text** here")
    assert "bold text" in result
    assert "**" not in result


def test_italic_markdown():
    result = normalize_for_speech("*italic text* here")
    assert "italic text" in result
    assert "*" not in result


def test_markdown_header():
    result = normalize_for_speech("## Section Title\nsome text")
    assert "##" not in result
    assert "Section Title" in result


def test_bullet_points():
    result = normalize_for_speech("- First item\n- Second item")
    assert "-" not in result.lstrip()
    assert "First item" in result


def test_numbered_list():
    result = normalize_for_speech("1. First\n2. Second")
    assert "First" in result
    assert "Second" in result


def test_em_dash():
    result = normalize_for_speech("word — other word")
    assert "—" not in result
    assert "," in result


def test_en_dash():
    result = normalize_for_speech("2020–2025")
    assert "–" not in result


def test_hash_stripped():
    result = normalize_for_speech("topic #1 is important")
    assert "#" not in result


def test_passthrough_clean_text():
    text = "The economy grew by three percent last year."
    assert normalize_for_speech(text) == text
