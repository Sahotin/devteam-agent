FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m pip install --no-cache-dir pytest==8.4.2 \
    && groupadd --gid 10001 runner \
    && useradd --uid 10001 --gid 10001 --no-create-home runner

WORKDIR /workspace
USER 10001:10001

ENTRYPOINT []
