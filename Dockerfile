FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 TZ=America/New_York
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt || true
COPY bot.py ema_vwap.py live.py metrics.py dashboard.py backtest.py \
     webhook.py accounts.py ml_filter.py ./
RUN useradd --create-home --uid 10001 scanner && mkdir -p /app/state \
 && chown -R scanner:scanner /app
USER scanner
ENV STATE_DB=/app/state/alerts.sqlite3
ENTRYPOINT ["python", "live.py"]
