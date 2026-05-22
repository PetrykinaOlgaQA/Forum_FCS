from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_MODEL: Any | None | bool = None


def _model_path() -> Path:
    """Путь к файлу обученной модели sklearn."""
    return Path(__file__).resolve().parents[1] / "ml" / "models" / "toxicity_model.joblib"


def _load_model():
    """Лениво загружает pipeline; при ошибке отключает фильтр на время работы процесса."""
    global _MODEL
    if _MODEL is False:
        return None
    if _MODEL is not None:
        return _MODEL
    try:
        import joblib

        fp = _model_path()
        if not fp.exists():
            _MODEL = False
            return None
        _MODEL = joblib.load(fp)
        return _MODEL
    except Exception:
        _MODEL = False
        return None


def toxicity_score(text: str | None) -> float:
    """Оценка вероятности токсичности от 0 до 1; без модели всегда 0."""
    if not text or not str(text).strip():
        return 0.0
    model = _load_model()
    if not model:
        return 0.0
    try:
        return float(model.predict_proba([text])[0][1])
    except Exception:
        return 0.0


def is_toxic(text: str | None, *, threshold: float | None = None) -> bool:
    """True, если score не ниже порога (по умолчанию из TOXIC_BLOCK_THRESHOLD или 0.72)."""
    if threshold is None:
        threshold = float(os.getenv("TOXIC_BLOCK_THRESHOLD", "0.72"))
    return toxicity_score(text) >= float(threshold)
