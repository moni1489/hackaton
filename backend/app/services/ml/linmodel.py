"""Мультиномиальная логистическая регрессия на чистом Python.

Почему без scikit-learn: система разворачивается на школьных серверах и в
офлайн-демо, где колёса под текущий Python могут быть недоступны. Модель здесь
маленькая (десяток признаков, до пяти классов), обучается за секунды, а веса
хранятся читаемым JSON — их можно посмотреть глазами и объяснить на комиссии.
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parents[3] / "models"


def _softmax(logits: list[float]) -> list[float]:
    top = max(logits)
    exps = [math.exp(v - top) for v in logits]
    total = sum(exps)
    return [e / total for e in exps]


class SoftmaxRegression:
    """Линейный классификатор с L2-регуляризацией и взвешиванием классов."""

    def __init__(self, classes: list[str], features: list[str]):
        self.classes = list(classes)
        self.features = list(features)
        self.weights = [[0.0] * len(features) for _ in classes]
        self.bias = [0.0] * len(classes)
        self.mu = [0.0] * len(features)
        self.sigma = [1.0] * len(features)
        self.metrics: dict = {}
        self.version = "untrained"

    # --- обучение --------------------------------------------------------

    def fit(self, X: list[list[float]], y: list[str], *, epochs: int = 40,
            lr: float = 0.5, l2: float = 1e-4, batch: int = 64, seed: int = 7) -> None:
        n_f, n_c = len(self.features), len(self.classes)
        index = {c: i for i, c in enumerate(self.classes)}

        for j in range(n_f):
            column = [row[j] for row in X]
            self.mu[j] = sum(column) / len(column)
            variance = sum((v - self.mu[j]) ** 2 for v in column) / len(column)
            self.sigma[j] = math.sqrt(variance) or 1.0

        Xs = [[(row[j] - self.mu[j]) / self.sigma[j] for j in range(n_f)] for row in X]
        targets = [index[label] for label in y]

        # Взвешивание классов: «авария не подтверждена» встречается на порядок
        # чаще аварий уровня района — без веса модель выучила бы только её.
        counts = [0] * n_c
        for t in targets:
            counts[t] += 1
        weight = [len(targets) / (n_c * c) if c else 0.0 for c in counts]

        rng = random.Random(seed)
        order = list(range(len(Xs)))
        velocity_w = [[0.0] * n_f for _ in range(n_c)]
        velocity_b = [0.0] * n_c
        momentum = 0.9

        for epoch in range(epochs):
            rng.shuffle(order)
            step = lr * (1.0 - epoch / (epochs + 1))
            for cut in range(0, len(order), batch):
                chunk = order[cut:cut + batch]
                grad_w = [[0.0] * n_f for _ in range(n_c)]
                grad_b = [0.0] * n_c
                for i in chunk:
                    row, target = Xs[i], targets[i]
                    probs = _softmax([
                        self.bias[c] + sum(self.weights[c][j] * row[j] for j in range(n_f))
                        for c in range(n_c)])
                    w = weight[target]
                    for c in range(n_c):
                        error = (probs[c] - (1.0 if c == target else 0.0)) * w
                        if error:
                            gw = grad_w[c]
                            for j in range(n_f):
                                gw[j] += error * row[j]
                            grad_b[c] += error
                scale = step / len(chunk)
                for c in range(n_c):
                    wc, gw, vw = self.weights[c], grad_w[c], velocity_w[c]
                    for j in range(n_f):
                        vw[j] = momentum * vw[j] - scale * (gw[j] + l2 * wc[j] * len(chunk))
                        wc[j] += vw[j]
                    velocity_b[c] = momentum * velocity_b[c] - scale * grad_b[c]
                    self.bias[c] += velocity_b[c]

    # --- применение ------------------------------------------------------

    def predict_proba(self, features: list[float]) -> dict[str, float]:
        row = [(features[j] - self.mu[j]) / self.sigma[j] for j in range(len(self.features))]
        probs = _softmax([
            self.bias[c] + sum(self.weights[c][j] * row[j] for j in range(len(row)))
            for c in range(len(self.classes))])
        return dict(zip(self.classes, probs))

    def predict(self, features: list[float]) -> tuple[str, float]:
        probs = self.predict_proba(features)
        label = max(probs, key=probs.get)
        return label, probs[label]

    def top_drivers(self, features: list[float], label: str, limit: int = 3) -> list[dict]:
        """Вклад признаков в вердикт — объяснимость для оператора и для суда."""
        c = self.classes.index(label)
        parts = []
        for j, name in enumerate(self.features):
            z = (features[j] - self.mu[j]) / self.sigma[j]
            parts.append({"feature": name, "value": round(features[j], 3),
                          "contribution": round(self.weights[c][j] * z, 3)})
        parts.sort(key=lambda p: abs(p["contribution"]), reverse=True)
        return parts[:limit]

    # --- сериализация ----------------------------------------------------

    def to_dict(self) -> dict:
        return {"classes": self.classes, "features": self.features, "weights": self.weights,
                "bias": self.bias, "mu": self.mu, "sigma": self.sigma,
                "metrics": self.metrics, "version": self.version}

    @classmethod
    def from_dict(cls, payload: dict) -> "SoftmaxRegression":
        model = cls(payload["classes"], payload["features"])
        model.weights = payload["weights"]
        model.bias = payload["bias"]
        model.mu = payload["mu"]
        model.sigma = payload["sigma"]
        model.metrics = payload.get("metrics", {})
        model.version = payload.get("version", "unknown")
        return model

    def save(self, name: str) -> Path:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        path = MODELS_DIR / f"{name}.json"
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1))
        return path

    @classmethod
    def load(cls, name: str) -> "SoftmaxRegression | None":
        path = MODELS_DIR / f"{name}.json"
        if not path.exists():
            return None
        try:
            return cls.from_dict(json.loads(path.read_text()))
        except (json.JSONDecodeError, KeyError):
            return None


def evaluate(model: SoftmaxRegression, X: list[list[float]], y: list[str]) -> dict:
    """Accuracy и macro-F1 на отложенной выборке + матрица ошибок."""
    matrix = {a: {b: 0 for b in model.classes} for a in model.classes}
    correct = 0
    for row, truth in zip(X, y):
        predicted, _ = model.predict(row)
        matrix[truth][predicted] += 1
        correct += predicted == truth

    per_class, f1_sum = {}, 0.0
    for label in model.classes:
        tp = matrix[label][label]
        fp = sum(matrix[o][label] for o in model.classes if o != label)
        fn = sum(matrix[label][o] for o in model.classes if o != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {"precision": round(precision, 3), "recall": round(recall, 3),
                            "f1": round(f1, 3), "support": tp + fn}
        f1_sum += f1
    return {"samples": len(y), "accuracy": round(correct / len(y), 3) if y else 0.0,
            "macro_f1": round(f1_sum / len(model.classes), 3),
            "per_class": per_class, "confusion": matrix}
