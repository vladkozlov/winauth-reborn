FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WINAUTH_HOST=0.0.0.0 \
    WINAUTH_DATA_DIR=/app/data

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY winauth_reborn ./winauth_reborn
RUN pip install --no-cache-dir . \
    && useradd --uid 1000 --create-home appuser \
    && mkdir /app/data \
    && chown appuser:appuser /app/data

USER appuser
EXPOSE 8080
CMD ["python", "-m", "winauth_reborn"]
