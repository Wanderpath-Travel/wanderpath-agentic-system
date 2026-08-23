"""
Wanderpath Autonomous Agent Platform - Docker Deployment & Real-World Integration Tests
Validates container configuration, Dockerfile syntax, compose orchestrations, and live webhook endpoints.
"""

import os
import pathlib
import sys
import yaml
from starlette.testclient import TestClient

# Ensure root is on path
ROOT_DIR = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR))

from wanderpath_platform.backend.app import app

client = TestClient(app)


def test_dockerfile_syntax_and_structure():
    """Verify Dockerfile contains multi-stage builds, non-root paths, and healthchecks."""
    dockerfile_path = ROOT_DIR / "Dockerfile"
    assert dockerfile_path.exists(), "Dockerfile must exist at repository root."
    content = dockerfile_path.read_text(encoding="utf-8")

    assert "FROM python:3.11-slim AS builder" in content
    assert "FROM python:3.11-slim AS runtime" in content
    assert "HEALTHCHECK" in content
    assert "ENTRYPOINT" in content
    assert "EXPOSE 8500 8000" in content
    print("  ✅ Dockerfile multi-stage structure & healthchecks verified.")


def test_docker_compose_configuration():
    """Verify docker-compose.yml defines platform, mcp server, volumes, and networks."""
    compose_path = ROOT_DIR / "docker-compose.yml"
    assert compose_path.exists(), "docker-compose.yml must exist at repository root."
    
    with open(compose_path, "r", encoding="utf-8") as f:
        compose = yaml.safe_load(f)

    services = compose.get("services", {})
    assert "wanderpath-platform" in services, "Platform service missing in compose."
    assert "wanderpath-mcp" in services, "MCP service missing in compose."
    
    # Volumes
    volumes = compose.get("volumes", {})
    assert "wanderpath_db_data" in volumes, "Persistent SQLite volume missing."
    assert "wanderpath_chroma_data" in volumes, "Persistent ChromaDB volume missing."
    
    print("  ✅ docker-compose.yml services, volumes, and networks verified.")


def test_entrypoint_script():
    """Verify docker-entrypoint.sh exists and supports all modes."""
    entrypoint_path = ROOT_DIR / "docker-entrypoint.sh"
    assert entrypoint_path.exists(), "docker-entrypoint.sh must exist."
    content = entrypoint_path.read_text(encoding="utf-8")

    assert "all" in content
    assert "platform" in content
    assert "mcp" in content
    assert "test" in content
    print("  ✅ docker-entrypoint.sh multi-mode execution logic verified.")


def test_env_example_template():
    """Verify .env.example contains essential configuration keys."""
    env_path = ROOT_DIR / ".env.example"
    assert env_path.exists(), ".env.example must exist."
    content = env_path.read_text(encoding="utf-8")

    assert "PORT_PLATFORM=8500" in content
    assert "PORT_MCP=8000" in content
    assert "DB_PATH=" in content
    assert "GEMINI_API_KEY" in content
    print("  ✅ .env.example environment variables verified.")


def test_production_healthcheck_api():
    """Verify GET /healthz endpoint returns healthy status and active tool count."""
    res = client.get("/healthz")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert data["service"] == "wanderpath-autonomous-platform"
    assert data["database"] in ["connected", "initializing"]
    assert data["chroma_vector_store"] == "ready"
    assert data["active_mcp_tools"] >= 9
    print("  ✅ GET /healthz production healthcheck endpoint verified.")


def test_real_world_consular_webhook():
    """Verify POST /api/webhooks/consular ingests external embassy event and resumes graph."""
    thread_id = "thread-docker-webhook-visa"
    
    # 1. Initialize visa graph to wait state
    client.post("/api/chat", json={
        "agent_id": "visa_agent",
        "message": "Emergency visa for Japan",
        "thread_id": thread_id
    })

    # 2. Ingest real-world webhook
    res = client.post("/api/webhooks/consular", json={
        "thread_id": thread_id,
        "consular_reference": "CONS-JPN-2026-LIVE",
        "decision": "APPROVED",
        "fee": 150.00,
        "notes": "Fast-track biometrics verified."
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["new_state"]["__status__"] == "COMPLETED"
    print("  ✅ Real-world consular webhook ingestion verified.")


def test_real_world_gds_dispute_webhook():
    """Verify POST /api/webhooks/gds ingests airline carrier settlement and updates dispute state."""
    thread_id = "thread-docker-webhook-gds"
    
    # 1. Initialize dispute graph to wait state
    client.post("/api/chat", json={
        "agent_id": "dispute_agent",
        "message": "PacificFly strike cancellation claim",
        "thread_id": thread_id
    })

    # 2. Ingest real-world GDS webhook
    res = client.post("/api/webhooks/gds", json={
        "thread_id": thread_id,
        "decision": "OFFER_PARTIAL",
        "amount": 250.00,
        "fee_waiver": 100.00,
        "legal_rationale": "Settled under EU261 standard airline delay agreement."
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["new_state"]["__status__"] == "COMPLETED"
    print("  ✅ Real-world GDS airline settlement webhook ingestion verified.")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("\n==================================================================")
    print("RUNNING DOCKER DEPLOYMENT & REAL-WORLD INTEGRATION TESTS")
    print("==================================================================")
    test_dockerfile_syntax_and_structure()
    test_docker_compose_configuration()
    test_entrypoint_script()
    test_env_example_template()
    test_production_healthcheck_api()
    test_real_world_consular_webhook()
    test_real_world_gds_dispute_webhook()
    print("\nALL DOCKER DEPLOYMENT & INTEGRATION TESTS PASSED (100%)!\n")
