FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 agent
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install .
# The mutable zone and data dir are the only writable places (mounted as volumes in compose).
RUN mkdir -p /app/data && chown -R agent:agent /app
USER agent
ENV TA_DATA_DIR=/app/data TA_GENERATED_STRATEGIES_DIR=/app/src/trading_agent/strategies/generated TA_CONFIG=/app/config/settings.yaml
EXPOSE 9100
HEALTHCHECK --interval=60s --timeout=5s --retries=3 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9100/healthz', timeout=4).status==200 else 1)"
ENTRYPOINT ["trading-agent"]
CMD ["run"]
