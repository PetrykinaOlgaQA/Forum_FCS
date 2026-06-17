"""Unit-тесты валидации полей товара/услуги."""
from services.post_fields import parse_price_rub, validate_http_url, validate_telegram_nick


def test_parse_price_ok():
    assert parse_price_rub("0") == (0, None)
    assert parse_price_rub("1500") == (1500, None)
    assert parse_price_rub(" 1\u00a0000 ") == (1000, None)


def test_parse_price_errors():
    assert parse_price_rub("12.5")[1] is not None
    assert parse_price_rub("abc")[1] is not None
    assert parse_price_rub(None) == (None, None)
    assert parse_price_rub("") == (None, None)


def test_telegram_ok():
    nick, err = validate_telegram_nick("@validuser")
    assert err is None
    assert nick == "@validuser"
    nick2, err2 = validate_telegram_nick("othernick")
    assert err2 is None
    assert nick2 == "@othernick"


def test_telegram_invalid():
    _, err = validate_telegram_nick("12bad")  # не с буквы после опционального @
    assert err is not None
    _, err = validate_telegram_nick("")
    assert err is None and _ is None


def test_url_ok():
    u, err = validate_http_url("https://example.com/path?q=1")
    assert err is None
    assert u.startswith("https://")


def test_url_invalid():
    _, err = validate_http_url("ftp://example.com")
    assert err is not None
    _, err = validate_http_url("not-a-url")
    assert err is not None
