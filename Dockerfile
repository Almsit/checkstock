FROM python:3.13.18-slim-trixie AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home app
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini .
USER app

FROM base AS test
USER root
COPY requirements-test.txt .
RUN pip install --no-cache-dir -r requirements-test.txt
COPY tests ./tests
COPY pyproject.toml .
USER app
CMD ["python", "-m", "pytest"]

FROM base AS runtime
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--no-proxy-headers"]
