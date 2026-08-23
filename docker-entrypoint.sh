#!/bin/bash
set -e

echo "=================================================================="
echo "🌟 WANDERPATH AUTONOMOUS AGENT SYSTEM — CONTAINER INITIALIZING"
echo "=================================================================="

# Ensure directories exist
mkdir -p /app/db /app/rag/chroma_db /app/docs/transcripts

MODE="${1:-all}"
PORT_MCP="${PORT_MCP:-8000}"
PORT_PLATFORM="${PORT_PLATFORM:-8500}"

if [ "$MODE" = "test" ]; then
    echo "🧪 Running Master Smoke Test Suite..."
    python tests/master_smoke_test.py
    exit $?
fi

if [ "$MODE" = "mcp" ]; then
    echo "🚀 Starting Wanderpath MCP Server on http://0.0.0.0:${PORT_MCP}/sse..."
    exec python mcp_server/server.py --transport sse --port "${PORT_MCP}"
fi

if [ "$MODE" = "platform" ]; then
    echo "🚀 Starting Wanderpath Platform on http://0.0.0.0:${PORT_PLATFORM}..."
    exec uvicorn wanderpath_platform.backend.app:app --host 0.0.0.0 --port "${PORT_PLATFORM}"
fi

if [ "$MODE" = "all" ]; then
    echo "🚀 Starting Wanderpath Dual-Service Architecture..."
    
    # 1. Start MCP Server in background
    echo "  -> Launching MCP Server on http://0.0.0.0:${PORT_MCP}/sse..."
    python mcp_server/server.py --transport sse --port "${PORT_MCP}" &
    MCP_PID=$!
    
    # Wait for MCP to initialize
    sleep 2
    
    # 2. Trap signals to cleanly shut down background MCP process
    trap "echo 'Stopping container...'; kill -TERM $MCP_PID 2>/dev/null; exit 0" SIGINT SIGTERM
    
    # 3. Start Platform Web Server in foreground
    echo "  -> Launching Full-Stack Platform on http://0.0.0.0:${PORT_PLATFORM}..."
    uvicorn wanderpath_platform.backend.app:app --host 0.0.0.0 --port "${PORT_PLATFORM}" &
    PLATFORM_PID=$!
    
    wait $PLATFORM_PID
fi

exec "$@"
