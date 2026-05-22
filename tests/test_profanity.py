"""Unit-тесты словаря нецензурной лексики."""
from services.profanity import contains_profanity


def test_clean_text_not_flagged():
    assert contains_profanity("Хорошая статья, спасибо автору") is False
    assert contains_profanity("") is False
    assert contains_profanity("   ") is False


def test_substring_from_bad_list_flagged():
    # Корень «пизд» из встроенного списка (см. services/profanity.py)
    assert contains_profanity("такой ответ совершенно пиздатый") is True


def test_normalization_yo():
    # ё → е в нормализации
    assert contains_profanity("блять") is True
