FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 autosign \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /app --shell /usr/sbin/nologin autosign \
    && mkdir -p /app /data \
    && chown 10001:10001 /app /data

WORKDIR /app
COPY seccomp_profile.json /usr/share/autosign/seccomp_profile.json
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright \
    && rm -rf /var/lib/apt/lists/*
COPY --chown=10001:10001 . ./

USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "autosign"]
