# syntax=docker/dockerfile:1.4

##
# Universal build for arbitrage services.
# Build per service via:
#   docker build --build-arg SERVICE_NAME=api -t arbitrage-api:latest .
##

ARG PYTHON_VERSION=3.11-slim

FROM python:${PYTHON_VERSION} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN python -m venv /opt/venv && /opt/venv/bin/pip install --upgrade pip
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

ARG SERVICE_NAME=api
ENV SERVICE_NAME=${SERVICE_NAME}
ENV PORT=8000

CMD ["/entrypoint.sh"]

