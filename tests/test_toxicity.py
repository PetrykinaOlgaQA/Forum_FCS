"""Поведение ML-токсичности без обязательной модели."""
from services.toxicity import is_toxic, toxicity_score


def test_empty_text_zero_score():
    assert toxicity_score("") == 0.0
    assert toxicity_score(None) == 0.0
    assert is_toxic("") is False


def test_short_benign_text():
    s = "Обсуждаем лабораторную работу по базам данных."
    # Без файла модели score должен быть 0.0
    assert toxicity_score(s) == 0.0
    assert is_toxic(s) is False
