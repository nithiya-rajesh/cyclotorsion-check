# CyclotorsionCheck container images — API service and nightly backup job.
#
# Multi-stage: a shared `base` stage (interpreter pin, deps, non-root user)
# feeds two independently-buildable targets, `api` and `backup` (staff review:
# these used to be two near-identical Dockerfiles; consolidated here to remove
# the duplicated preamble rather than maintaining two copies of it).
#
#   docker build -f docker/Dockerfile --target api    -t cyclotorsion-api:0.1.0    .
#   docker build -f docker/Dockerfile --target backup -t cyclotorsion-backup:0.1.0 .
#   docker run --rm -p 8080:8080 cyclotorsion-api:0.1.0
#
# Build from the repository root so the build context includes backend/ and
# scripts/ (the app runs the `cyclotorsion` package in backend/).

# Pinned to a digest, not just the mutable "3.13-slim" tag (AppSec review Low:
# reproducible builds; a tag can be repointed upstream without our knowledge).
# Refresh deliberately via Dependabot's docker ecosystem, or:
#   docker buildx imagetools inspect python:3.13-slim
FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install the backend package with its production extras (Gemini, Firebase,
# BigQuery). The package metadata lives in backend/ so we copy it first.
COPY backend/ backend/

# Install pinned versions when a requirements.lock is present (the runtime
# image's source of truth), else fall back to the ranges in requirements.txt.
RUN pip install --upgrade pip && \
    if [ -f backend/requirements.lock ]; then \
      pip install -r backend/requirements.lock; \
    else \
      pip install -r backend/requirements.txt; \
    fi && \
    pip install -e "backend[prod]"

# Run as a non-root user (AppSec review High: CWE-250). Cloud Run's own
# sandbox (gVisor) is not a substitute for this — it bounds the blast radius
# of any future RCE in an image dependency to an unprivileged process.
RUN groupadd --system app && useradd --system --gid app --no-create-home app \
    && chown -R app:app /app
USER app

# --- api: the FastAPI service deployed to Cloud Run v2 -----------------------

FROM base AS api

# Cloud Run invokes the app on port 8080. __main__.py binds 127.0.0.1 for local
# dev only; the container entrypoint listens on 0.0.0.0 for the platform probe.
EXPOSE 8080

CMD ["uvicorn", "cyclotorsion.app:app", "--host", "0.0.0.0", "--port", "8080"]

# --- backup: the nightly BigQuery -> GCS export job (Cloud Run v2 job) -------

FROM base AS backup

COPY --chown=app:app scripts/ scripts/

# backup_export.py reads CC_PROJECT_ID / CC_BACKUP_TABLE / CC_BACKUP_BUCKET from
# the environment (set in the Cloud Run job). Exit code 0 == success; non-zero
# surfacing in Cloud Monitoring for the bigquery_write_failure alert.
ENTRYPOINT ["python", "scripts/backup_export.py"]
CMD []
