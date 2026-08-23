# syntax=docker/dockerfile:1
# ==============================================================================
# Wanderpath Autonomous Agent Platform - Production Dockerfile
# Multi-stage optimized build for Python 3.11
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

# Layer-cached dependency installation
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt


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

# Copy installed wheels from builder stage
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

# Copy application source code
COPY . .

# Ensure entrypoint is executable and storage directories exist
RUN sed -i 's/\r$//' docker-entrypoint.sh && \
    chmod +x docker-entrypoint.sh && \
    mkdir -p /app/db /app/rag/chroma_db /app/docs/transcripts

# Expose Platform Web UI (8500) and MCP SSE Server (8000)
EXPOSE 8500 8000

# Healthcheck targeting the Platform status API
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8500/healthz || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["all"]
