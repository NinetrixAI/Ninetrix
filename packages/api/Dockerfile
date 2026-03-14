FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY . .

# Pre-built dashboard static files are included in packages/api/static/dashboard/
# (copied by `turbo build` → `make build` before docker build)

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --retries=6 \
  CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
