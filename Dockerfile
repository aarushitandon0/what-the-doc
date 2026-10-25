# The whole application in one container: Ollama with gemma4:e2b, LibreOffice, the
# Python API, and the built React interface served by that API on one origin.
#
# Needs about 8 GB of RAM at runtime (the model alone uses ~5-6 GB). See DEPLOYMENT.md.

# ---- 1. Build the interface ------------------------------------------------
FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 2. Runtime ---------------------------------------------------------------
# The same Python minor version the test suite runs on.
FROM python:3.14-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates zstd \
        libreoffice-writer-nogui \
        fonts-liberation fonts-crosextra-carlito fonts-crosextra-caladea fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Ollama, pinned to the version the recorded evaluation ran against. The release
# bundles GPU libraries; this service runs on CPU, so they are removed in the same
# layer to keep the image small.
ARG OLLAMA_VERSION=0.40.10
RUN curl -fsSL "https://github.com/ollama/ollama/releases/download/v${OLLAMA_VERSION}/ollama-linux-amd64.tar.zst" \
        | zstd -d | tar -x -C /usr/local \
    && find /usr/local/lib/ollama -mindepth 1 -maxdepth 1 -type d \
        \( -name 'cuda*' -o -name 'rocm*' -o -name 'vulkan*' -o -name 'mlx*' \) \
        -exec rm -rf {} + \
    && ollama --version

# The model is baked into the image so a restart does not re-download 4.6 GB.
# start.sh also pulls it if it is ever missing.
ENV OLLAMA_MODELS=/opt/ollama/models \
    GEMMA_MODEL=gemma4:e2b
RUN mkdir -p /opt/ollama/models \
    && (ollama serve >/tmp/ollama-build.log 2>&1 &) \
    && for i in $(seq 1 60); do curl -sf http://127.0.0.1:11434/api/version >/dev/null && break; sleep 1; done \
    && ollama pull "$GEMMA_MODEL" \
    && chmod -R a+rwX /opt/ollama

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY test-documents/ ./test-documents/
COPY deploy/start.sh ./deploy/start.sh
COPY --from=frontend /build/dist ./frontend/dist

# Run as an unprivileged user. Ollama, LibreOffice and the API all run as this user.
RUN useradd -m -u 1000 app && mkdir -p /data && chown app /data \
    && sed -i 's/\r$//' deploy/start.sh && chmod +x deploy/start.sh
USER app
ENV HOME=/home/app \
    WTD_PUBLIC=1 \
    WTD_STATIC_DIR=/app/frontend/dist \
    WTD_API_ROOT=/data/api \
    OLLAMA_HOST=127.0.0.1:11434 \
    OLLAMA_KEEP_ALIVE=-1 \
    OLLAMA_NUM_PARALLEL=1 \
    PORT=10000

EXPOSE 10000
CMD ["/app/deploy/start.sh"]
