# One container: the FastAPI gateway (uvicorn, port 8080) plus the chat agents it starts as child
# processes (agent/agent.py --sid <sid> --chat). Build from the repo root:
#   docker build -f deploy/aws/Dockerfile -t homefront-gateway .
# No secret is ever copied in: Dockerfile.dockerignore drops every .env file, and the entrypoint
# writes the agent's settings at container start from variables ECS injects from Secrets Manager.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/Backend:/app/packages/mandate-contracts/src:/app/packages/atlas-enricher/src:/app/packages/mcp-tools/src:/app/packages/identity-gateway/src:/app/packages/guardrail-scanner/src

WORKDIR /app

# Two virtualenvs, the same layout as on a laptop: .venv for the gateway, agent/.venv for the agent
# (the supervisor runs agent/.venv/bin/python). Requirements first so code edits keep this layer cached.
COPY Backend/requirements.txt Backend/requirements.txt
COPY agent/requirements.txt agent/requirements.txt
RUN python -m venv /app/.venv \
 && /app/.venv/bin/pip install --no-cache-dir -r Backend/requirements.txt \
 && python -m venv /app/agent/.venv \
 && /app/agent/.venv/bin/pip install --no-cache-dir -r agent/requirements.txt

COPY Backend Backend
COPY packages packages
COPY agent agent
COPY deploy/aws/entrypoint.sh /app/entrypoint.sh

# Run as a normal user. It owns agent/ only, so the entrypoint can write agent/.env there.
RUN useradd --create-home --uid 10001 app \
 && chmod 755 /app/entrypoint.sh \
 && chown -R app:app /app/agent
USER app
ENV HOME=/home/app

EXPOSE 8080
ENTRYPOINT ["/app/entrypoint.sh"]
