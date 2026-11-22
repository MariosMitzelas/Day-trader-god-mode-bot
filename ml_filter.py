"""Logistic regression filter."""
import json, logging, math
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, pstdev

log = logging.getLogger("ml_filter")

def _sigmoid(z):
    if z >= 0: return 1 / (1 + math.exp(-z))
    e = math.exp(z); return e / (1 + e)

@dataclass
class Features:
    volume_ratio: float = 1.0
    rvol: float = 1.0
    rsi: float = 50.0
    atr_pct: float = 0.01
    ema_slope: float = 0.0
    vwap_distance: float = 0.0
    premarket_distance: float = 0.0

    def to_vector(self, names):
        return [getattr(self, n, 0.0) for n in names]

class MLFilter:
    def __init__(self, model_path=None, threshold=None):
        self.model = None; self.features = []; self.weights = []; self.bias = 0.0
        self.threshold = threshold if threshold is not None else 0.55
        if model_path: self.load(model_path)

    def load(self, path):
        raw = json.loads(Path(path).read_text())
        self.features = raw["features"]; self.weights = raw["weights"]
        self.bias = raw["bias"]; self.threshold = raw.get("threshold", self.threshold)
        self.model = raw

    def predict_proba(self, features):
        if self.model is None: return 1.0
        v = features.to_vector(self.features)
        z = self.bias + sum(w * x for w, x in zip(self.weights, v))
        return _sigmoid(z)

    def should_alert(self, features):
        p = self.predict_proba(features)
        return p >= self.threshold, p
