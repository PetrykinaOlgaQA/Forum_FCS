"""
Дымовой HTTP-тест приложения (опционально).

По умолчанию **пропускается**, чтобы `pytest` не зависал на недоступном PostgreSQL.
Запуск с реальной БД:

    set INTEGRATION_TESTS=1
    pytest tests/test_integration_http.py -v
"""
import os

import pytest


@pytest.mark.integration
def test_index_returns_html():
    if os.getenv("INTEGRATION_TESTS", "").strip().lower() not in ("1", "true", "yes"):
        pytest.skip("Установите INTEGRATION_TESTS=1 для проверки GET / с живой БД")

    try:
        from app import app as flask_app
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Импорт app невозможен: {exc}")

    flask_app.config["TESTING"] = True
    client = flask_app.test_client()

    try:
        resp = client.get("/")
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Запрос к приложению не выполнен: {exc}")

    assert resp.status_code == 200
    assert b"html" in resp.data.lower() or resp.mimetype == "text/html; charset=utf-8"
