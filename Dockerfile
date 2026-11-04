FROM --platform=$BUILDPLATFORM node:22-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ARG TARGETARCH
ARG APP_VERSION=0.1.0
ARG BUILD_REVISION=local
ARG BUILD_TIME=""
ENV WORK115_VERSION=$APP_VERSION \
    WORK115_BUILD_REVISION=$BUILD_REVISION \
    WORK115_BUILD_TIME=$BUILD_TIME
WORKDIR /opt/115tools
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 app && useradd --uid 1000 --gid app --create-home app
COPY scripts/install_7zip.py /tmp/install_7zip.py
RUN python /tmp/install_7zip.py "$TARGETARCH" && rm /tmp/install_7zip.py
COPY pyproject.toml uv.lock alembic.ini ./
COPY components.lock.json ./
COPY app/ ./app/
COPY migrations/ ./migrations/
RUN pip install --no-cache-dir uv==0.13.0 \
    && uv sync --frozen --no-dev --extra p115 --no-editable \
    && mkdir -p /data /opt/115tools/frontend/dist \
    && chown app:app /data
COPY --from=frontend /build/frontend/dist/ ./frontend/dist/
COPY THIRD_PARTY_NOTICES.md ./
COPY third_party/licenses/ ./licenses/dependencies/
COPY third_party/UNPACKFLOW_LICENSE ./licenses/UNPACKFLOW_LICENSE
ENV WORK115_DATA_DIR=/data \
    WORK115_EXTRACT_ENGINE=7zip \
    WORK115_EXTRACTOR_PATH=/usr/local/bin/7zz \
    WORK115_ADAPTER=disabled \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
ENV PATH="/opt/115tools/.venv/bin:$PATH"
USER 1000:1000
RUN /usr/local/bin/7zz i
EXPOSE 5656
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5656/health/ready', timeout=4)"
CMD ["python", "-m", "app.runtime.launch"]
