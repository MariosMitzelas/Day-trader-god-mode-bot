FROM python:3.12-slim
WORKDIR /app
COPY bot.py live.py ./
CMD ["python", "-u", "live.py"]
