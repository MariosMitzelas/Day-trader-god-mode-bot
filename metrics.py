"""Prometheus metrics exporter."""
import threading
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import monotonic

_LOCK = threading.Lock()
_COUNTERS = defaultdict(float)
_GAUGES = defaultdict(float)
_HISTOGRAMS = defaultdict(list)

def inc(name, value=1.0, **labels):
    key = _label_key(name, labels)
    with _LOCK:
        _COUNTERS[key] += value

def set_gauge(name, value, **labels):
    key = _label_key(name, labels)
    with _LOCK:
        _GAUGES[key] = float(value)

def observe(name, value, **labels):
    key = _label_key(name, labels)
    with _LOCK:
        _HISTOGRAMS[key].append(float(value))
        if len(_HISTOGRAMS[key]) > 1000:
            _HISTOGRAMS[key] = _HISTOGRAMS[key][-1000:]

def _label_key(name, labels):
    if not labels: return name
    parts = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return f"{name}{{{parts}}}"

def start_server(port=9090):
    pass
