from __future__ import annotations

import argparse
import csv
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from ml.text_roots import stem_tokenize


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_dataset_path() -> Path:
    return _project_root() / "ml" / "data" / "toxicity_dataset.csv"


def _default_model_path() -> Path:
    return _project_root() / "ml" / "models" / "toxicity_model.joblib"


def build_model() -> Pipeline:
    # TF-IDF по "корням" (стемам) слов + LogisticRegression.
    # Это даёт обобщение по формам слов (оскорблять/оскорбление/оскорбил -> "оскорбл").
    return Pipeline(
        steps=[
            (
                "tfidf",
                TfidfVectorizer(
                    analyzer="word",
                    tokenizer=stem_tokenize,
                    token_pattern=None,  # обязателен при кастомном tokenizer
                    ngram_range=(1, 2),
                    min_df=2,
                    max_features=80_000,
                ),
            ),
            ("clf", LogisticRegression(max_iter=2000, n_jobs=1)),
        ]
    )


def load_csv_dataset(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")

    x: list[str] = []
    y: list[int] = []

    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        if not r.fieldnames or "text" not in r.fieldnames or "label" not in r.fieldnames:
            raise ValueError("Dataset must have columns: text,label")
        for row in r:
            txt = (row.get("text") or "").strip()
            lab_raw = (row.get("label") or "").strip()
            if not lab_raw:
                continue
            try:
                lab = int(lab_raw)
            except ValueError:
                continue
            x.append(txt)
            y.append(1 if lab else 0)
    return x, y


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", type=str, default=str(_default_dataset_path()))
    p.add_argument("--out", type=str, default=str(_default_model_path()))
    args = p.parse_args()

    data_path = Path(args.data)
    out_path = Path(args.out)

    x, y = load_csv_dataset(data_path)
    model = build_model()
    model.fit(x, y)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, out_path)
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()

