"""
Wanderpath Travel Agency - Full-Stack Platform API
==================================================
Starlette ASGI server serving:
1. Multi-Agent Chat Router (3 State Graph Agents + Planning Agent + Memory/RAG Agent)
2. Live MCP Dynamic Tool Management & RBAC Controls
3. Live RAG Document Ingestion & Deletion
4. Admin Human-in-the-Loop (HITL) Resolution
5. Admin Failure Ticket Inspection & Mid-Node Recovery
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import pathlib
import sqlite3
import sys
import uuid
from typing import Any, Dict, List, Optional

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

# Ensure project root is in sys.path
project_root = pathlib.Path(__file__).parent.parent.parent.resolve()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

mcp_server_dir = project_root / "mcp_server"
if str(mcp_server_dir) not in sys.path:
    sys.path.insert(0, str(mcp_server_dir))

from server import (
    register_dynamic_tool,
    deregister_dynamic_tool,
    set_tool_enabled,
    get_all_registered_tools,
    broadcast_tool_list_changed,
)
from rag.vector_store import WanderpathVectorStore
from state_graph.checkpointer import DurableCheckpointer
from state_graph.recovery.hitl_engine import HITLEngine
from state_graph.recovery.ticket_engine import TicketEngine
from state_graph.graphs.visa_graph import create_visa_processing_graph
from state_graph.graphs.dispute_graph import create_dispute_reconciliation_graph
from state_graph.graphs.medevac_graph import create_medevac_repatriation_graph
from planning.routing.route_subtask import route_subtask
from planning.algorithms_glue.plan_and_solve import run_plan_and_solve
from planning.adapters.model_provider import WanderpathModelProvider

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("WanderpathPlatformAPI")

# Persistent Shared Subsystems
DB_PATH = os.getenv("DB_PATH", str(project_root / "db" / "wanderpath.sqlite3"))
os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)

CHROMA_DIR = os.getenv("CHROMA_PERSIST_DIR", str(project_root / "rag" / "chroma_db"))
os.makedirs(os.path.abspath(CHROMA_DIR), exist_ok=True)

checkpointer = DurableCheckpointer(db_path=DB_PATH)
hitl_engine = HITLEngine(db_path=DB_PATH)
ticket_engine = TicketEngine(db_path=DB_PATH)
vector_db = WanderpathVectorStore(collection_name="wanderpath_knowledge", persist_dir=CHROMA_DIR)

# Seed baseline policies if empty
if len(vector_db.list_documents()) == 0:
    vector_db.add_document(
        doc_id="policy_hotel_alpine",
        document="Alpine Resort & Spa Policy: Cancellations must be made at least 14 days prior to check-in for a 100% full refund.",
        metadata={"property": "Alpine Resort & Spa", "category": "cancellation"}
    )
    vector_db.add_document(
        doc_id="policy_airline_pacificfly",
        document="PacificFly Delay Policy: Delays exceeding 3 hours qualify for EU261 statutory compensation.",
        metadata={"carrier": "PacificFly", "category": "delay"}
    )

# Graph Instances Registry
visa_graph = create_visa_processing_graph(checkpointer=checkpointer, db_path=DB_PATH)
dispute_graph = create_dispute_reconciliation_graph(checkpointer=checkpointer, db_path=DB_PATH)
medevac_graph = create_medevac_repatriation_graph(checkpointer=checkpointer, db_path=DB_PATH)

GRAPH_REGISTRY = {
    "visa_processing_graph": visa_graph,
    "supplier_dispute_graph": dispute_graph,
    "medevac_repatriation_graph": medevac_graph,
}


# ============================================================================
# 1. USER CHAT API & MULTI-AGENT ROUTER
# ============================================================================
GREETINGS = {"hi", "hello", "hey", "greetings", "good morning", "good evening", "good afternoon", "welcome"}
STATUS_KEYWORDS = {"now?", "now", "status", "status?", "what now?", "what next?", "what next", "what's next?", "where is my application?", "update", "update?"}
GENERAL_QA = {"what can you help me with?", "what can you do?", "what can you do", "who are you?", "who are you", "are you smart?", "are you smart", "help", "help me"}

def is_greeting(msg: str) -> bool:
    clean = msg.strip().lower().rstrip("!?.")
    return clean in GREETINGS

def is_status_query(msg: str) -> bool:
    clean = msg.strip().lower()
    return clean in STATUS_KEYWORDS or "what now" in clean or "what next" in clean or "status" in clean

def is_general_qa(msg: str) -> bool:
    clean = msg.strip().lower()
    return clean in GENERAL_QA or "can you help" in clean or "are you smart" in clean or "who are you" in clean

async def handle_agent_chat(request: Request) -> JSONResponse:
    body = await request.json()
    thread_id = body.get("thread_id") or f"thread-{uuid.uuid4().hex[:8]}"
    agent_id = body.get("agent_id", "").lower()
    user_msg = body.get("message", "").strip()
    params = body.get("parameters") or {}

    logger.info(f"[ChatAPI] Route to agent '{agent_id}' on thread '{thread_id}': '{user_msg}'")

    # 1. Handle Greetings
    if is_greeting(user_msg):
        intros = {
            "visa_agent": (
                "👋 **Hello! I am the Wanderpath Visa & Consular Desk Agent.**\n\n"
                "I manage complex, multi-stage international visa applications using Task Decomposition, "
                "live diplomatic policy retrieval (RAG), external embassy webhooks, and managerial fee approvals.\n\n"
                "💡 *Quick Start:* Type your destination (e.g., *'Emergency Schengen visa for France'*) or click a scenario above!"
            ),
            "dispute_agent": (
                "👋 **Hello! I am the Supplier Contract Dispute & Chargeback Specialist.**\n\n"
                "I evaluate airline cancellations, force majeure clauses, and EU261 statutory compensation claims "
                "using Tree of Thoughts (ToT) legal analysis and automated GDS clearinghouse filings.\n\n"
                "💡 *Quick Start:* Enter your flight issue (e.g., *'PacificFly cancelled flight WP-202 due to crew strike'*) or click a scenario above!"
            ),
            "medevac_agent": (
                "👋 **Hello! I am the VIP Aeromedical Evacuation & Repatriation Coordinator.**\n\n"
                "I execute critical medical evacuations, utilizing LATS airfield scoring to match patient acuity against "
                "aircraft range, runway lengths, and receiving ICU bed capacity, with physician sign-off gates.\n\n"
                "💡 *Quick Start:* Enter the patient alert (e.g., *'Spinal trauma in Bali needing air charter to Singapore'*) or click a scenario above!"
            ),
            "planning_agent": (
                "👋 **Hello! I am the Trip Disruption Planning Agent.**\n\n"
                "I generate multi-stage dynamic DAGs to solve complex flight cancellations, hotel rebookings, and itinerary disruptions.\n\n"
                "💡 *Quick Start:* Describe your broken itinerary to generate a rebooking plan!"
            ),
            "memory_rag_agent": (
                "👋 **Hello! I am the Memory & Hybrid RAG Knowledge Agent.**\n\n"
                "I maintain long-term traveler profiles and search our internal knowledge base for luxury resort and airline policies.\n\n"
                "💡 *Quick Start:* Ask any policy question, e.g. *'What is the cancellation policy for Alpine Resort & Spa?'*"
            )
        }
        return JSONResponse({
            "agent_id": agent_id,
            "thread_id": thread_id,
            "status": "READY",
            "response": intros.get(agent_id, "Hello! How can I assist you with Wanderpath travel services today?"),
            "state": {"__status__": "READY"}
        })

    # 2. Handle General Q&A / Agency Assistance Inquiries
    if is_general_qa(user_msg):
        qa_resp = (
            "✨ **Wanderpath Autonomous Concierge Capabilities**:\n\n"
            "I am powered by an enterprise agentic architecture featuring 5 specialized operational desks:\n\n"
            "1. 🛂 **Visa & Consular Desk**: Stateful task decomposition & live consular RAG lookup with embassy webhooks.\n"
            "2. ⚖️ **Supplier Dispute Desk**: Tree of Thoughts (ToT) arbitration & GDS chargeback filings.\n"
            "3. 🚁 **VIP Medevac Desk**: Language Agent Tree Search (LATS) airfield routing with physician authorization.\n"
            "4. 🗺️ **Disruption Planner**: DAG dynamic replanning for cancelled flights and multi-leg connections.\n"
            "5. 🧠 **Memory & RAG Agent**: Long-term traveler profile memory and vector search across luxury policy guides.\n\n"
            "Every state transition is backed by durable SQLite checkpoints guaranteeing zero data loss."
        )
        return JSONResponse({
            "agent_id": agent_id,
            "thread_id": thread_id,
            "status": "READY",
            "response": qa_resp,
            "state": {"__status__": "READY"}
        })

    # 3. Handle Context-Aware Status / "What Next?" Inquiries on Active Threads
    if is_status_query(user_msg):
        graph_name_map = {
            "visa_agent": "visa_processing_graph",
            "dispute_agent": "supplier_dispute_graph",
            "medevac_agent": "medevac_repatriation_graph"
        }
        target_gname = graph_name_map.get(agent_id)
        latest_chk = checkpointer.load_latest_checkpoint(thread_id, graph_name=target_gname) if target_gname else None

        if latest_chk:
            cnode = latest_chk.current_node
            cstatus = latest_chk.state_data.get("__status__")
            if cnode == "awaiting_consular_webhook" and cstatus == "INTERRUPTED":
                return JSONResponse({
                    "agent_id": agent_id,
                    "thread_id": thread_id,
                    "status": "INTERRUPTED",
                    "current_node": cnode,
                    "response": (
                        "⏳ **Current Status: Awaiting Embassy Webhook**\n\n"
                        "Your digital visa dossier has been submitted. The application is paused waiting for the consulate to deliver its appointment webhook.\n\n"
                        "👉 *Next Action:* Click **'Simulate Webhook / Event Arrival & Resume'** in the chat to simulate the embassy's response."
                    ),
                    "state": latest_chk.state_data
                })
            elif cnode == "evaluate_consular_response" and cstatus == "INTERRUPTED":
                return JSONResponse({
                    "agent_id": agent_id,
                    "thread_id": thread_id,
                    "status": "INTERRUPTED",
                    "current_node": cnode,
                    "response": (
                        "🚨 **Current Status: Managerial Approval Required (HITL)**\n\n"
                        "The consulate confirmed an emergency slot with a fee of **$650.00**, which exceeds the standard $500 threshold.\n\n"
                        "👉 *Next Action:* Go to **Command Center -> HITL Tasks** and click **Approve & Resume Graph** to issue the visa."
                    ),
                    "state": latest_chk.state_data
                })
            elif cnode == "awaiting_carrier_adjudication" and cstatus == "INTERRUPTED":
                return JSONResponse({
                    "agent_id": agent_id,
                    "thread_id": thread_id,
                    "status": "INTERRUPTED",
                    "current_node": cnode,
                    "response": (
                        "⏳ **Current Status: Awaiting Carrier Settlement**\n\n"
                        "Your EU261 dispute claim is filed with the airline clearinghouse (7-day response window).\n\n"
                        "👉 *Next Action:* Click **'Simulate Webhook / Event Arrival & Resume'** to receive the settlement offer."
                    ),
                    "state": latest_chk.state_data
                })
            elif cnode == "evaluate_settlement_offer" and cstatus == "INTERRUPTED":
                return JSONResponse({
                    "agent_id": agent_id,
                    "thread_id": thread_id,
                    "status": "INTERRUPTED",
                    "current_node": cnode,
                    "response": (
                        "🚨 **Current Status: Fee Waiver Approval Required (HITL)**\n\n"
                        "The airline proposed a $200 refund with a $350 booking fee waiver (exceeds $300 limit).\n\n"
                        "👉 *Next Action:* Authorize the settlement under **Command Center -> HITL Tasks**."
                    ),
                    "state": latest_chk.state_data
                })
            elif cstatus == "COMPLETED":
                return JSONResponse({
                    "agent_id": agent_id,
                    "thread_id": thread_id,
                    "status": "COMPLETED",
                    "current_node": cnode,
                    "response": "✅ **Workflow Completed**: Your travel request has been fully processed and finalized in our database.",
                    "state": latest_chk.state_data
                })

    # Route to Visa Agent
    if agent_id == "visa_agent":
        init_state = {
            "client_id": params.get("client_id", 1),
            "destination": params.get("destination", "Japan" if "japan" in user_msg.lower() else "France"),
            "visa_type": params.get("visa_type", "tourist" if "tourist" in user_msg.lower() else "schengen"),
            "user_prompt": user_msg,
        }
        res = await visa_graph.execute(thread_id, initial_state=init_state)
        current_node = res.get("__current_node__")
        status = res.get("__status__")

        if current_node == "awaiting_consular_webhook" and status == "INTERRUPTED":
            resp_text = (
                f"🛂 **Consular Visa Dossier Submitted**\n\n"
                f"• **Destination**: {res.get('destination', 'France')} ({res.get('visa_type', 'schengen').title()} Visa)\n"
                f"• **Decomposed Milestones**: 6 milestones identified; passport verified & consular rules retrieved.\n"
                f"• **Expedited Fee Estimated**: ${res.get('retrieved_fee', 650.0):.2f}\n"
                f"• **Status**: ⏳ Application submitted to consular portal. **Awaiting asynchronous embassy webhook.**\n\n"
                f"*(You can trigger the webhook arrival using the button below or simulate in the Command Center.)*"
            )
        elif current_node == "evaluate_consular_response" and status == "INTERRUPTED":
            resp_text = (
                f"🚨 **Consular Fee Authorization Required (HITL Gate)**\n\n"
                f"• **Consular Response**: Emergency slot allocated.\n"
                f"• **Expedited Fee**: **${res.get('consular_fee', 650.0):.2f}** (Exceeds agency standard threshold of $500.00).\n"
                f"• **Action Required**: Escalated to Operations Command for managerial approval."
            )
        elif status == "COMPLETED":
            resp_text = (
                f"✅ **Visa Issued Successfully**\n\n"
                f"• **Visa Reference**: `{res.get('visa_number', 'VISA-APPROVED')}`\n"
                f"• **Destination**: {res.get('destination', 'France')}\n"
                f"• **Fee Paid**: ${res.get('consular_fee', res.get('retrieved_fee', 650.0)):.2f}\n"
                f"• **Status**: Validated and added to client traveler profile."
            )
        else:
            resp_text = f"Visa Application processed to step: {current_node}. Status: {status}"

        return JSONResponse({
            "agent_id": agent_id,
            "thread_id": thread_id,
            "status": status,
            "current_node": current_node,
            "response": resp_text,
            "state": res,
        })

    # Route to Dispute Agent
    elif agent_id == "dispute_agent":
        init_state = {
            "booking_id": params.get("booking_id", 3),
            "carrier": params.get("carrier", "PacificFly"),
            "amount_disputed": params.get("amount", 450.00),
            "dispute_reason": user_msg,
        }
        res = await dispute_graph.execute(thread_id, initial_state=init_state)
        current_node = res.get("__current_node__")
        status = res.get("__status__")

        if current_node == "awaiting_carrier_adjudication" and status == "INTERRUPTED":
            resp_text = (
                f"⚖️ **Supplier Dispute Claim Filed via GDS**\n\n"
                f"• **Carrier**: {res.get('carrier', 'PacificFly')} | **Booking ID**: #{res.get('booking_id', 3)}\n"
                f"• **Tree of Thoughts Strategy**: `{res.get('selected_strategy', 'EU261_STATUTORY_CLAIM')}`\n"
                f"• **GDS Filing Ref**: `{res.get('gds_filing_ref', 'GDS-DISP-3')}`\n"
                f"• **Status**: ⏳ Formal case filed. **Awaiting carrier 7-day settlement window.**"
            )
        elif current_node == "evaluate_settlement_offer" and status == "INTERRUPTED":
            resp_text = (
                f"🚨 **Fee Waiver Authorization Required (HITL Gate)**\n\n"
                f"• **Carrier Settlement Offer**: Refund of ${res.get('carrier_settlement', {}).get('amount', 200.0):.2f}\n"
                f"• **Fee Waiver Demanded**: **${res.get('carrier_settlement', {}).get('fee_waiver', 350.0):.2f}** (Exceeds agency threshold of $300.00).\n"
                f"• **Action Required**: Escalated to Operations Command for commercial approval."
            )
        elif status == "COMPLETED":
            resp_text = (
                f"✅ **Dispute Settled & Ledger Credited**\n\n"
                f"• **Booking ID**: #{res.get('booking_id', 3)}\n"
                f"• **Refund Credited**: **${res.get('refund_credited', 200.0):.2f}**\n"
                f"• **Status**: Finalized and reconciled in agency ledger."
            )
        else:
            resp_text = f"Dispute Claim processed to step: {current_node}. Strategy: {res.get('selected_strategy')}"

        return JSONResponse({
            "agent_id": agent_id,
            "thread_id": thread_id,
            "status": status,
            "current_node": current_node,
            "response": resp_text,
            "state": res,
        })

    # Route to Medevac Agent
    elif agent_id == "medevac_agent":
        init_state = {
            "patient_name": params.get("patient_name", "Elena Rostova"),
            "current_location": params.get("location", "Bali (DPS)"),
            "medical_condition": user_msg,
            "acuity_level": params.get("acuity", "CRITICAL"),
        }
        res = await medevac_graph.execute(thread_id, initial_state=init_state)
        current_node = res.get("__current_node__")
        status = res.get("__status__")

        if current_node == "awaiting_hospital_admission" and status == "INTERRUPTED":
            resp_text = (
                f"🚁 **VIP Aeromedical Evacuation Charter Initialized**\n\n"
                f"• **Patient**: {res.get('patient_name', 'Elena Rostova')} | **Location**: {res.get('current_location', 'Bali')}\n"
                f"• **LATS Selected Route**: `{res.get('selected_route_id', 'ROUTE_SGH_DIRECT')}` (Learjet 60XR Direct Air Ambulance to Singapore General Hospital)\n"
                f"• **Standby Guarantee**: `${res.get('estimated_cost', 14500.0):.2f}` (`WP-MED-99`)\n"
                f"• **Status**: ⏳ Air ambulance on tarmac standby. **Awaiting ICU bed availability confirmation.**"
            )
        elif current_node == "evaluate_physician_authorization" and status == "INTERRUPTED":
            resp_text = (
                f"🚨 **Physician Medical Director Authorization Required (HITL Gate)**\n\n"
                f"• **Hospital Bed**: ICU Level 1 Confirmed (`ICU-BED-04` at SGH)\n"
                f"• **Charter Cost**: **${res.get('estimated_cost', 14500.0):.2f}** (Exceeds emergency threshold of $5,000.00).\n"
                f"• **Action Required**: Chief Medical Officer sign-off required for wheels-up launch."
            )
        elif status == "COMPLETED":
            resp_text = (
                f"🛫 **Patient Airborne & En Route to ICU**\n\n"
                f"• **Mission ID**: `{res.get('mission_id', 'MEDEVAC-MISSION-COMPLETE')}`\n"
                f"• **Status**: Learjet 60XR airborne; Singapore trauma team standing by on tarmac."
            )
        else:
            resp_text = f"Medevac Mission processed to step: {current_node}. Selected Route: {res.get('selected_route_id')}"

        return JSONResponse({
            "agent_id": agent_id,
            "thread_id": thread_id,
            "status": status,
            "current_node": current_node,
            "response": resp_text,
            "state": res,
        })

    # Route to Planning Agent
    elif agent_id == "planning_agent":
        provider = WanderpathModelProvider()
        routed_algo = route_subtask(user_msg, risk_level="medium", requires_branching=True)
        plan_res = run_plan_and_solve(user_msg, provider)
        return JSONResponse({
            "agent_id": agent_id,
            "thread_id": thread_id,
            "status": "COMPLETED",
            "routed_algorithm": routed_algo,
            "response": f"🗺️ **Dynamic Trip Disruption Plan ({routed_algo})**:\n\n{plan_res}",
            "state": {"plan_output": plan_res, "algorithm": routed_algo},
        })

    # Route to Memory & RAG Agent
    elif agent_id == "memory_rag_agent":
        retrieved = vector_db.similarity_search(user_msg, n_results=1)
        rag_context = retrieved[0]["document"] if retrieved else "No direct policy match found in knowledge base."
        return JSONResponse({
            "agent_id": agent_id,
            "thread_id": thread_id,
            "status": "COMPLETED",
            "response": f"📖 **Verified Policy Knowledge Retrieval**:\n\n> {rag_context}",
            "state": {"retrieved_context": rag_context},
        })

    return JSONResponse({"error": f"Unknown agent_id: {agent_id}"}, status_code=400)


# ============================================================================
# 1.1 SIMULATE EXTERNAL ASYNCHRONOUS EVENTS & RESUME GRAPH
# ============================================================================
async def handle_simulate_external_event(request: Request) -> JSONResponse:
    body = await request.json()
    thread_id = body.get("thread_id")
    agent_id = body.get("agent_id")
    event_type = body.get("event_type", "webhook")

    if not thread_id:
        return JSONResponse({"error": "thread_id is required"}, status_code=400)

    logger.info(f"[SimulateEvent] Triggering '{event_type}' on thread '{thread_id}' for agent '{agent_id}'")

    if agent_id == "visa_agent":
        payload = {
            "consular_reference": "CONS-FRA-2026-991",
            "decision": "APPROVED",
            "fee": 650.00,
            "notes": "Emergency consular fast-track biometrics slot allocated."
        }
        res = await visa_graph.execute(thread_id, resume_payload=payload)
    elif agent_id == "dispute_agent":
        payload = {
            "carrier_settlement": {
                "decision": "OFFER_PARTIAL",
                "amount": 200.00,
                "fee_waiver": 350.00,
                "notes": "Carrier agrees to credit $200.00 with $350.00 fee waiver."
            }
        }
        res = await dispute_graph.execute(thread_id, resume_payload=payload)
    elif agent_id == "medevac_agent":
        payload = {
            "hospital_bed_confirmed": True,
            "bed_id": "ICU-BED-04",
            "physician": "Dr. K. Tan",
            "saturated": False
        }
        res = await medevac_graph.execute(thread_id, resume_payload=payload)
    else:
        return JSONResponse({"error": f"Unsupported agent for event simulation: {agent_id}"}, status_code=400)

    return JSONResponse({
        "status": "success",
        "agent_id": agent_id,
        "thread_id": thread_id,
        "new_graph_status": res.get("__status__"),
        "current_node": res.get("__current_node__"),
        "state": res
    })


# ============================================================================
# 2. ADMIN MCP TOOL MANAGEMENT
# ============================================================================
async def list_admin_tools(request: Request) -> JSONResponse:
    tools = get_all_registered_tools()
    return JSONResponse({"status": "success", "tools": tools})

async def toggle_admin_tool(request: Request) -> JSONResponse:
    body = await request.json()
    tool_name = body.get("tool_name")
    enabled = body.get("enabled", True)
    set_tool_enabled(tool_name, enabled)
    await broadcast_tool_list_changed()
    return JSONResponse({"status": "success", "tool_name": tool_name, "enabled": enabled})

async def register_new_admin_tool(request: Request) -> JSONResponse:
    body = await request.json()
    name = body.get("name")
    desc = body.get("description", "")
    schema = body.get("input_schema", {})
    register_dynamic_tool(name, desc, schema, enabled=True)
    await broadcast_tool_list_changed()
    return JSONResponse({"status": "success", "registered_tool": name})

async def deregister_admin_tool(request: Request) -> JSONResponse:
    name = request.path_params.get("name")
    success = deregister_dynamic_tool(name)
    if not success:
        return JSONResponse({"error": "Tool not found or is protected"}, status_code=404)
    await broadcast_tool_list_changed()
    return JSONResponse({"status": "success", "deregistered": name})


# ============================================================================
# 3. ADMIN RAG KNOWLEDGE BASE MANAGEMENT
# ============================================================================
async def list_rag_documents(request: Request) -> JSONResponse:
    docs = vector_db.list_documents()
    return JSONResponse({"status": "success", "count": len(docs), "documents": docs})

async def add_rag_document(request: Request) -> JSONResponse:
    body = await request.json()
    doc_id = body.get("doc_id")
    content = body.get("content")
    meta = body.get("metadata")
    vector_db.add_document(doc_id=doc_id, document=content, metadata=meta)
    return JSONResponse({"status": "success", "added_doc_id": doc_id})

async def delete_rag_document(request: Request) -> JSONResponse:
    doc_id = request.path_params.get("doc_id")
    vector_db.delete_document(doc_id)
    return JSONResponse({"status": "success", "deleted_doc_id": doc_id})


# ============================================================================
# 4. ADMIN HUMAN-IN-THE-LOOP (HITL) RESOLUTION
# ============================================================================
async def list_hitl_tasks(request: Request) -> JSONResponse:
    status = request.query_params.get("status")
    tasks = hitl_engine.list_tasks(status=status)
    return JSONResponse({"status": "success", "count": len(tasks), "tasks": [t.__dict__ for t in tasks]})

async def resolve_hitl_task(request: Request) -> JSONResponse:
    task_id = request.path_params.get("task_id")
    body = await request.json()
    decision = body.get("decision", "APPROVED")
    notes = body.get("admin_notes", "")

    task = hitl_engine.get_task(task_id)
    if not task:
        return JSONResponse({"error": "HITL task not found"}, status_code=404)

    target_graph = GRAPH_REGISTRY.get(task.graph_name)
    result = hitl_engine.resolve_task(
        task_id=task_id,
        admin_decision=decision,
        admin_notes=notes,
        graph=target_graph,
    )
    return JSONResponse({"status": "success", "task_id": task_id, "decision": decision, "resumed_state": result})


# ============================================================================
# 5. ADMIN FAILURE TICKET CENTER
# ============================================================================
async def list_failure_tickets(request: Request) -> JSONResponse:
    status = request.query_params.get("status")
    tickets = ticket_engine.list_tickets(status=status)
    return JSONResponse({"status": "success", "count": len(tickets), "tickets": [t.__dict__ for t in tickets]})

async def update_ticket_status(request: Request) -> JSONResponse:
    ticket_id = request.path_params.get("ticket_id")
    status = request.query_params.get("status", "INVESTIGATING")
    ticket_engine.update_status(ticket_id, status=status)
    return JSONResponse({"status": "success", "ticket_id": ticket_id, "new_status": status})

async def resolve_and_resume_ticket(request: Request) -> JSONResponse:
    ticket_id = request.path_params.get("ticket_id")
    body = await request.json()
    patch = body.get("state_patch")
    notes = body.get("resolution_notes", "")

    ticket = ticket_engine.get_ticket(ticket_id)
    if not ticket:
        return JSONResponse({"error": "Failure ticket not found"}, status_code=404)

    target_graph = GRAPH_REGISTRY.get(ticket.graph_name)
    result = ticket_engine.resolve_and_resume_ticket(
        ticket_id=ticket_id,
        state_patch=patch,
        resolution_notes=notes,
        graph=target_graph,
    )
    return JSONResponse({"status": "success", "ticket_id": ticket_id, "resumed_state": result})


# ============================================================================
# 6. SYSTEM OVERVIEW
# ============================================================================
async def get_system_overview(request: Request) -> JSONResponse:
    hitl_pending = len(hitl_engine.list_tasks(status="PENDING"))
    tickets_open = len(ticket_engine.list_tickets(status="OPEN"))
    rag_count = len(vector_db.list_documents())
    tools = get_all_registered_tools()
    
    return JSONResponse({
        "status": "OPERATIONAL",
        "active_mcp_tools": len([t for t in tools if t.get("enabled")]),
        "total_mcp_tools": len(tools),
        "pending_hitl_tasks": hitl_pending,
        "open_failure_tickets": tickets_open,
        "rag_documents_count": rag_count,
        "supported_agents": ["visa_agent", "dispute_agent", "medevac_agent", "planning_agent", "memory_rag_agent"],
    })


# ============================================================================
# 7. REAL-WORLD PRODUCTION WEBHOOK INGESTION
# ============================================================================
async def handle_consular_webhook(request: Request) -> JSONResponse:
    """Production endpoint for diplomatic visa status / biometrics callbacks."""
    body = await request.json()
    thread_id = body.get("thread_id")
    if not thread_id:
        return JSONResponse({"error": "Missing required field 'thread_id'"}, status_code=400)
    
    payload = {
        "consular_reference": body.get("consular_reference", "CONS-FRA-2026-991"),
        "decision": body.get("decision", "APPROVED"),
        "fee": float(body.get("fee", 650.00)),
        "notes": body.get("notes", "Consular biometrics verified and fast-track slot allocated."),
        "additional_docs": body.get("additional_docs")
    }
    logger.info(f"[ConsularWebhook] Processing external callback for thread '{thread_id}': {payload}")
    res = await visa_graph.execute(thread_id, resume_payload=payload)
    return JSONResponse({
        "status": "success",
        "message": "Consular webhook ingested successfully.",
        "thread_id": thread_id,
        "new_state": res
    })

async def handle_gds_webhook(request: Request) -> JSONResponse:
    """Production endpoint for airline GDS carrier settlement / EU261 adjudication."""
    body = await request.json()
    thread_id = body.get("thread_id")
    if not thread_id:
        return JSONResponse({"error": "Missing required field 'thread_id'"}, status_code=400)

    payload = {
        "decision": body.get("decision", "OFFER_PARTIAL"),
        "amount": float(body.get("amount", 200.00)),
        "fee_waiver": float(body.get("fee_waiver", 350.00)),
        "legal_rationale": body.get("legal_rationale", "Carrier acknowledged EU261 crew scheduling delay.")
    }
    logger.info(f"[GDSWebhook] Processing carrier adjudication for thread '{thread_id}': {payload}")
    res = await dispute_graph.execute(thread_id, resume_payload=payload)
    return JSONResponse({
        "status": "success",
        "message": "GDS settlement webhook ingested successfully.",
        "thread_id": thread_id,
        "new_state": res
    })

async def handle_hospital_webhook(request: Request) -> JSONResponse:
    """Production endpoint for receiving hospital ICU bed and aeromedical confirmation."""
    body = await request.json()
    thread_id = body.get("thread_id")
    if not thread_id:
        return JSONResponse({"error": "Missing required field 'thread_id'"}, status_code=400)

    payload = {
        "bed_confirmed": body.get("bed_confirmed", True),
        "icu_ward": body.get("icu_ward", "SGH-ICU-BED-04"),
        "attending_physician": body.get("attending_physician", "Dr. K. Chen, MD"),
        "charter_guarantee": float(body.get("charter_guarantee", 14500.00))
    }
    logger.info(f"[HospitalWebhook] Processing ICU bed admission for thread '{thread_id}': {payload}")
    res = await medevac_graph.execute(thread_id, resume_payload=payload)
    return JSONResponse({
        "status": "success",
        "message": "Hospital ICU bed confirmation webhook ingested successfully.",
        "thread_id": thread_id,
        "new_state": res
    })


# ============================================================================
# 8. PRODUCTION HEALTHCHECK & STATIC FRONTEND
# ============================================================================
async def health_check(request: Request) -> JSONResponse:
    db_ok = pathlib.Path(DB_PATH).exists()
    tools = get_all_registered_tools()
    return JSONResponse({
        "status": "healthy",
        "service": "wanderpath-autonomous-platform",
        "version": "3.0.0",
        "database": "connected" if db_ok else "initializing",
        "chroma_vector_store": "ready",
        "active_mcp_tools": len([t for t in tools if t.get("enabled")]),
        "total_mcp_tools": len(tools),
        "timestamp": datetime.now(timezone.utc).isoformat()
    })

FRONTEND_DIR = pathlib.Path(__file__).parent.parent / "frontend"

async def serve_frontend_index(request: Request) -> HTMLResponse:
    index_file = FRONTEND_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Wanderpath Platform Active</h1>")


async def purge_resolved_hitl_tasks(request: Request) -> JSONResponse:
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    try:
        conn.execute("DELETE FROM hitl_tasks WHERE status != 'PENDING';")
        conn.commit()
    finally:
        conn.close()
    return JSONResponse({"status": "success", "message": "Purged resolved HITL tasks."})


# Route Declarations
routes = [
    Route("/", endpoint=serve_frontend_index, methods=["GET"]),
    Route("/healthz", endpoint=health_check, methods=["GET"]),
    Route("/api/chat", endpoint=handle_agent_chat, methods=["POST"]),
    Route("/api/chat/simulate_event", endpoint=handle_simulate_external_event, methods=["POST"]),
    Route("/api/webhooks/consular", endpoint=handle_consular_webhook, methods=["POST"]),
    Route("/api/webhooks/gds", endpoint=handle_gds_webhook, methods=["POST"]),
    Route("/api/webhooks/hospital", endpoint=handle_hospital_webhook, methods=["POST"]),
    Route("/api/admin/overview", endpoint=get_system_overview, methods=["GET"]),
    Route("/api/admin/tools", endpoint=list_admin_tools, methods=["GET"]),
    Route("/api/admin/tools/toggle", endpoint=toggle_admin_tool, methods=["POST"]),
    Route("/api/admin/tools/register", endpoint=register_new_admin_tool, methods=["POST"]),
    Route("/api/admin/tools/{name}", endpoint=deregister_admin_tool, methods=["DELETE"]),
    Route("/api/admin/rag/documents", endpoint=list_rag_documents, methods=["GET"]),
    Route("/api/admin/rag/documents", endpoint=add_rag_document, methods=["POST"]),
    Route("/api/admin/rag/documents/{doc_id}", endpoint=delete_rag_document, methods=["DELETE"]),
    Route("/api/admin/hitl/tasks", endpoint=list_hitl_tasks, methods=["GET"]),
    Route("/api/admin/hitl/tasks/purge", endpoint=purge_resolved_hitl_tasks, methods=["POST"]),
    Route("/api/admin/hitl/tasks/{task_id}/resolve", endpoint=resolve_hitl_task, methods=["POST"]),
    Route("/api/admin/tickets", endpoint=list_failure_tickets, methods=["GET"]),
    Route("/api/admin/tickets/{ticket_id}/status", endpoint=update_ticket_status, methods=["POST"]),
    Route("/api/admin/tickets/{ticket_id}/resolve", endpoint=resolve_and_resume_ticket, methods=["POST"]),
]

middleware = [
    Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
]

app = Starlette(debug=True, routes=routes, middleware=middleware)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8500)
