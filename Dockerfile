FROM node:24-alpine AS frontend-builder

WORKDIR /build/frontend
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM node:24-bookworm-slim AS node-runtime

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NPM_CONFIG_CACHE=/tmp/.npm \
    COREPACK_HOME=/tmp/.corepack

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system devteam \
    && useradd --system --gid devteam --home-dir /app devteam

# The API container also executes generated Node.js projects through the
# restricted terminal runner. Keep Node/npm available in the final image
# instead of only in the discarded frontend build stage.
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules

# Docker COPY dereferences npm/npx links from the Node image. Recreate them so
# npm resolves ../lib/cli.js relative to its real package directory.
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version \
    && npm --version \
    && npx --version

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY backend/ ./backend/
COPY sandbox/ ./sandbox/
COPY --from=frontend-builder /build/frontend/dist ./frontend/dist/

RUN python -m pip install --no-cache-dir . \
    && mkdir -p /workspace /app/data \
    && chown -R devteam:devteam /workspace /app/data

USER devteam
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/ready', timeout=2)"

CMD ["sh", "-c", "alembic upgrade head && uvicorn backend.app.main:app --host 0.0.0.0 --port 8000"]
