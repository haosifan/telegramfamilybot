FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml /app/
COPY src /app/src
RUN pip install --upgrade pip && pip install .
ENV PYTHONPATH=/app/src
CMD ["python", "-m", "familybot.main"]
