FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PANEL_DATA=/app/data
WORKDIR /app
COPY requirements.txt ./
RUN python -m venv /app/.venv \
    && /app/.venv/bin/python -m pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 panel \
    && useradd --uid 10001 --gid panel --no-create-home --shell /usr/sbin/nologin panel \
    && mkdir -p /app/data \
    && chown 10001:10001 /app/data \
    && chmod 700 /app/data
COPY app.py ./
COPY core/ ./core/
COPY static/ ./static/
USER 10001:10001
EXPOSE 8080
# Container binding is internal; Compose publishes only to host loopback.
CMD ["/app/.venv/bin/python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
