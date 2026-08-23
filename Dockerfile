# syntax=docker/dockerfile:1
# ==============================================================================
# Wanderpath Autonomous Agent Platform - Production Multi-Stage Dockerfile
# ==============================================================================

FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    curl \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Create dedicated virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install dependencies inside the virtualenv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# ==============================================================================
# Final Production Runtime Stage
# ==============================================================================
FROM python:3.11-slim AS runtime

LABEL maintainer="Ahmed Hossam <ahmedhossam7800@gmail.com>"
LABEL description="Wanderpath Autonomous Travel Concierge Platform"
LABEL version="3.0.0"

WORKDIR /app

# Install runtime utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Copy virtualenv from builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

# Copy application source code
COPY . .

# Sanitize line endings, ensure entrypoint is executable, and create persistence dirs
RUN sed -i 's/\r$//' docker-entrypoint.sh && \
    chmod +x docker-entrypoint.sh && \
    mkdir -p /app/db /app/rag/chroma_db /app/docs/transcripts

# Expose Platform Web UI (8500) and MCP SSE Server (8000)
EXPOSE 8500 8000

# Healthcheck
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8500/healthz || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["all"]
