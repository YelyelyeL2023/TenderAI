# Single image for api / worker / one-off jobs (backfill, etl, migrate).
FROM python:3.12.7-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY tenderai ./tenderai
RUN pip install . && useradd --uid 10001 --create-home app
USER app
EXPOSE 8010
# No secrets are baked into the image: OWS_TOKEN / DB passwords come from env or mounted secrets at runtime.
CMD ["uvicorn", "tenderai.api.main:app", "--host", "0.0.0.0", "--port", "8010"]
