# Build context expects an extracted NetPulse all-in-one Linux bundle, for example:
#   docker build -t netpulse-node --build-context bundle=/path/to/extracted deploy/docker
# Simpler: copy this Dockerfile into the extracted linux/ folder and build there.
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY hub /app/hub
COPY agent /app/agent
COPY start-all.sh /app/start-all.sh

RUN chmod +x /app/start-all.sh /app/hub/start-hub.sh /app/agent/start-agent.sh \
    && python3 -m pip install --no-cache-dir -r /app/agent/requirements.txt

EXPOSE 8787
ENV PYTHONUNBUFFERED=1
ENV SKIP_PIP=1

CMD ["/app/start-all.sh"]
