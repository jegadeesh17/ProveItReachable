# Hugging Face Docker Space image (ARCHITECTURE section 18; ADR-0013, ADR-0030).
# Base digest: python:3.13-slim index digest, read on 2026-10-26 from Docker Hub
# (`docker buildx imagetools inspect python:3.13-slim` and the registry manifest API).
FROM python:3.13-slim@sha256:d98cc29d3266e535dd9da1099688d9cb3ca4753813571e071763d17725460962

# Non-root user, uid 1000 (Spaces run the container as uid 1000).
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin user

WORKDIR /app

# Hash-checked runtime dependencies first, so this layer is cached across source changes.
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# Only what the app needs. Never .env, data/, eval/results/ or the repo's personal files.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

COPY web ./web
COPY demo ./demo

# The one file from eval/results/ that ships: the published eval summary (ADR-0036).
COPY eval/results/latest.json ./eval/results/latest.json

RUN mkdir -p /app/data && chown 1000:1000 /app/data

USER 1000

EXPOSE 7860
CMD ["python", "-m", "pir", "serve", "--host", "0.0.0.0", "--port", "7860"]
