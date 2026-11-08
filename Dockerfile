# HomeFront images for AWS (deploy/lambda/deploy.py builds both, for arm64):
#   --target api    the API, /mcp and the built console, as an AWS Lambda function
#   --target agent  the chat agent, one ECS Fargate task per session (agent/agent.py --sid <sid> --chat)

# --- console (Vite build) ---------------------------------------------------
FROM node:22-slim AS frontend
WORKDIR /repo
COPY Frontend/package.json Frontend/package-lock.json Frontend/
RUN cd Frontend && npm ci --no-audit --no-fund
# The console imports these from outside Frontend/.
COPY packages/mandate-contracts packages/mandate-contracts
COPY agent/data agent/data
COPY Frontend Frontend
# Public values only (they are in the browser bundle anyway). The login redirect uses window.location.origin.
ARG VITE_COGNITO_DOMAIN=""
ARG VITE_COGNITO_CLIENT_ID=""
ENV VITE_COGNITO_DOMAIN=${VITE_COGNITO_DOMAIN} VITE_COGNITO_CLIENT_ID=${VITE_COGNITO_CLIENT_ID}
RUN cd Frontend && npm run build

# --- API on Lambda ----------------------------------------------------------
FROM public.ecr.aws/lambda/python:3.12 AS api
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY Backend/requirements.txt ${LAMBDA_TASK_ROOT}/Backend/requirements.txt
RUN pip install -r ${LAMBDA_TASK_ROOT}/Backend/requirements.txt
COPY Backend ${LAMBDA_TASK_ROOT}/Backend
COPY packages ${LAMBDA_TASK_ROOT}/packages
COPY --from=frontend /repo/Frontend/dist ${LAMBDA_TASK_ROOT}/Frontend/dist
ENV PYTHONPATH=/var/task/Backend:/var/task/packages/mandate-contracts/src:/var/task/packages/aauth/src:/var/task/packages/atlas-enricher/src:/var/task/packages/mcp-tools/src:/var/task/packages/identity-gateway/src:/var/task/packages/guardrail-scanner/src \
    FRONTEND_DIST=/var/task/Frontend/dist
CMD ["app.lambda_handler.handler"]

# --- chat agent on Fargate --------------------------------------------------
FROM python:3.12-slim AS agent
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY agent/requirements.txt agent/requirements.txt
RUN python -m venv agent/.venv && agent/.venv/bin/pip install -r agent/requirements.txt
COPY agent agent
COPY packages/mandate-contracts packages/mandate-contracts
COPY packages/aauth packages/aauth
RUN useradd --system --home /app app
USER app
# The supervisor passes `--sid <sid> --chat`; identity and gateway URL come from the task's environment.
ENTRYPOINT ["/app/agent/.venv/bin/python", "agent/agent.py"]
