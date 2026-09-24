FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY app ./app
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
RUN pip install --no-cache-dir uv && uv sync --locked --no-dev
ENV PATH="/app/.venv/bin:$PATH"
CMD ["sh", "-c", "uv run alembic -c alembic.ini upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
