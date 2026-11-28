FROM docker:28-cli AS docker-tools
FROM python:3.12-slim-bookworm

ARG AUTOSIGN_BUILD_REVISION=local
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    AUTOSIGN_BUILD_REVISION=$AUTOSIGN_BUILD_REVISION \
    AUTOSIGN_UPDATE_PROTOCOL=1

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tzdata ca-certificates libatomic1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 autosign \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /app --shell /usr/sbin/nologin autosign \
    && mkdir -p /app /data \
    && chown 10001:10001 /app /data

WORKDIR /app
COPY --from=docker-tools /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-tools /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose
LABEL org.opencontainers.image.source="https://github.com/mini3535/multi-site-checkin"
COPY seccomp_profile.json /usr/share/autosign/seccomp_profile.json
COPY requirements.txt ./
ARG PIP_INDEX_URL=
RUN if [ -n "$PIP_INDEX_URL" ]; then \
      python -m pip install --no-cache-dir --retries 5 --timeout 30 --index-url "$PIP_INDEX_URL" -r requirements.txt; \
    else \
      python -m pip install --no-cache-dir --retries 5 --timeout 30 -r requirements.txt; \
    fi
RUN python -m playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright \
    && rm -rf /var/lib/apt/lists/*
COPY --chown=10001:10001 . ./

USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "autosign"]
