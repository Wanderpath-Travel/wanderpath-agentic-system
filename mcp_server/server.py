import argparse
import asyncio
import json
import os
import pathlib
from typing import Any, Callable, Dict, List, Optional
import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.sse import SseServerTransport
import uvicorn

# Initialize MCP Server
app = Server("WanderpathTravelAgent")

# ============================================================================
# SESSION STATE, RBAC & DYNAMIC TOOL REGISTRY
# ============================================================================
CURRENT_ROLE = "junior_agent"  # junior_agent | senior_manager

# Dynamic Tools Registry: maps tool_name -> dict(tool=types.Tool, handler=callable, enabled=bool, is_dynamic=bool)
DYNAMIC_TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {}
TOOL_ENABLED_STATUS: Dict[str, bool] = {}

def register_dynamic_tool(
    name: str,
    description: str,
    input_schema: dict,
    handler: Optional[Callable] = None,
    enabled: bool = True,
) -> None:
    """Registers a tool at runtime without redeploying the server."""
    tool_def = types.Tool(
        name=name,
        description=description,
        inputSchema=input_schema,
    )
    DYNAMIC_TOOL_REGISTRY[name] = {
        "tool": tool_def,
        "handler": handler,
        "enabled": enabled,
        "is_dynamic": True,
    }
    TOOL_ENABLED_STATUS[name] = enabled


def deregister_dynamic_tool(name: str) -> bool:
    """Removes a dynamic tool from the registry."""
    if name in DYNAMIC_TOOL_REGISTRY and DYNAMIC_TOOL_REGISTRY[name].get("is_dynamic"):
        del DYNAMIC_TOOL_REGISTRY[name]
        TOOL_ENABLED_STATUS.pop(name, None)
        return True
    return False


def set_tool_enabled(name: str, enabled: bool) -> bool:
    """Enables or disables any tool (base or dynamic) at runtime."""
    TOOL_ENABLED_STATUS[name] = enabled
    if name in DYNAMIC_TOOL_REGISTRY:
        DYNAMIC_TOOL_REGISTRY[name]["enabled"] = enabled
    return True


def get_all_registered_tools() -> List[Dict[str, Any]]:
    """Returns metadata for all available tools and their current status."""
    tools_info = []
    
    # Base tools info
    base_tools = [
        {"name": "get_itinerary_details", "description": "Fetch travel itinerary details by ID.", "is_dynamic": False, "role_required": "junior_agent"},
        {"name": "authenticate_manager", "description": "Authenticate as a Senior Manager.", "is_dynamic": False, "role_required": "junior_agent"},
        {"name": "cancel_booking", "description": "Cancel a flight or hotel booking by ID. Triggers elicitation if non-refundable.", "is_dynamic": False, "role_required": "junior_agent"},
        {"name": "generate_itinerary_report", "description": "Compile a comprehensive multi-city travel itinerary with progress updates.", "is_dynamic": False, "role_required": "junior_agent"},
        {"name": "modify_booking_dates", "description": "Modify start and end dates for an existing travel booking.", "is_dynamic": False, "role_required": "senior_manager"},
        {"name": "override_cancellation_fee", "description": "Override and waive cancellation fees on non-refundable bookings.", "is_dynamic": False, "role_required": "senior_manager"},
        {"name": "admin_register_tool", "description": "Dynamically register a new tool at runtime.", "is_dynamic": False, "role_required": "senior_manager"},
        {"name": "admin_toggle_tool", "description": "Dynamically enable or disable a tool at runtime.", "is_dynamic": False, "role_required": "senior_manager"},
        {"name": "admin_list_all_tools", "description": "List all tools and their live status.", "is_dynamic": False, "role_required": "junior_agent"},
    ]
    for bt in base_tools:
        bt["enabled"] = TOOL_ENABLED_STATUS.get(bt["name"], True)
        tools_info.append(bt)

    # Dynamic tools
    for name, entry in DYNAMIC_TOOL_REGISTRY.items():
        if entry.get("is_dynamic"):
            t = entry["tool"]
            tools_info.append({
                "name": t.name,
                "description": t.description,
                "inputSchema": t.inputSchema,
                "enabled": entry.get("enabled", True),
                "is_dynamic": True,
                "role_required": "junior_agent",
            })

    return tools_info


async def broadcast_tool_list_changed():
    """Notifies active client sessions that the tool list has changed."""
    try:
        ctx = app.request_context
        if ctx and ctx.session:
            await ctx.session.send_tool_list_changed()
    except Exception:
        pass


# ============================================================================
# 1. RESOURCES
# ============================================================================
RESOURCES_DIR = pathlib.Path(__file__).parent / "resources"

@app.list_resources()
async def list_resources() -> list[types.Resource]:
    return [
        types.Resource(
            uri="policy://passport-rules",
            name="Passport & Entry Policy",
            description="Static international passport validity rules.",
            mimeType="text/plain",
        )
    ]

@app.read_resource()
async def read_resource(uri: str | types.AnyUrl) -> str | bytes:
    if str(uri) == "policy://passport-rules":
        policy_file = RESOURCES_DIR / "passport_policy.md"
        return policy_file.read_text(encoding="utf-8")
    raise ValueError(f"Unknown resource URI: {uri}")

# ============================================================================
# 2. PROMPTS
# ============================================================================
@app.list_prompts()
async def list_prompts() -> list[types.Prompt]:
    return [
        types.Prompt(
            name="draft_refund_explanation",
            description="Draft a professional refund explanation email.",
            arguments=[
                types.PromptArgument(name="booking_id", description="ID of the booking", required=True),
                types.PromptArgument(name="client_name", description="Name of the client", required=True),
                types.PromptArgument(name="refund_amount", description="Amount refunded", required=True),
            ]
        )
    ]

@app.get_prompt()
async def get_prompt(name: str, arguments: dict[str, str] | None) -> types.GetPromptResult:
    if name == "draft_refund_explanation":
        args = arguments or {}
        b_id = args.get("booking_id", "[ID]")
        c_name = args.get("client_name", "[Name]")
        r_amt = args.get("refund_amount", "[Amount]")
        
        rendered_text = (
            f"You are a polite travel assistant for Wanderpath Travel B.\n\n"
            f"Draft a professional customer service email to client '{c_name}' regarding "
            f"their recent refund request for booking ID '{b_id}'.\n\n"
            f"Key details to include:\n"
            f"- Acknowledge their cancellation request for booking ID {b_id}.\n"
            f"- State clearly that a refund of ${r_amt} has been processed according to Wanderpath policy.\n"
            f"- Thank them for choosing Wanderpath Travel B. and offer further assistance if needed."
        )
        return types.GetPromptResult(
            description="Draft refund email",
            messages=[types.PromptMessage(role="user", content=types.TextContent(type="text", text=rendered_text))]
        )
    raise ValueError(f"Unknown prompt: {name}")

# ============================================================================
# 3. DEFENSIVE & DYNAMIC TOOLS
# ============================================================================
@app.list_tools()
async def list_tools() -> list[types.Tool]:
    tools: list[types.Tool] = []

    # 1. Base Tools (Subject to TOOL_ENABLED_STATUS)
    if TOOL_ENABLED_STATUS.get("get_itinerary_details", True):
        tools.append(types.Tool(
            name="get_itinerary_details",
            description="Fetch travel itinerary details by ID.",
            inputSchema={
                "type": "object",
                "properties": {"itinerary_id": {"type": "integer"}},
                "required": ["itinerary_id"],
                "additionalProperties": False,
            },
        ))

    if TOOL_ENABLED_STATUS.get("authenticate_manager", True):
        tools.append(types.Tool(
            name="authenticate_manager",
            description="Authenticate as a Senior Manager to unlock administrative write tools.",
            inputSchema={
                "type": "object",
                "properties": {"passcode": {"type": "string"}},
                "required": ["passcode"],
                "additionalProperties": False,
            },
        ))

    if TOOL_ENABLED_STATUS.get("cancel_booking", True):
        tools.append(types.Tool(
            name="cancel_booking",
            description="Cancel a flight or hotel booking by ID. Triggers human elicitation if non-refundable.",
            inputSchema={
                "type": "object",
                "properties": {
                    "booking_id": {"type": "integer"},
                    "reason": {"type": "string"},
                    "human_confirmation": {
                        "type": "string",
                        "description": "Optional human sign-off response ('APPROVED' or 'REJECTED').",
                    },
                },
                "required": ["booking_id", "reason"],
                "additionalProperties": False,
            },
        ))

    if TOOL_ENABLED_STATUS.get("generate_itinerary_report", True):
        tools.append(types.Tool(
            name="generate_itinerary_report",
            description="Long-running operation: Compile a comprehensive multi-city travel itinerary with progress updates.",
            inputSchema={
                "type": "object",
                "properties": {
                    "destination": {"type": "string"},
                    "duration_days": {"type": "integer"},
                },
                "required": ["destination", "duration_days"],
                "additionalProperties": False,
            },
        ))

    if TOOL_ENABLED_STATUS.get("modify_booking_dates", True):
        tools.append(types.Tool(
            name="modify_booking_dates",
            description="[DEFENSIVE WRITE TOOL] Modify start and end dates for an existing travel booking.",
            inputSchema={
                "type": "object",
                "properties": {
                    "booking_id": {"type": "integer", "minimum": 1},
                    "start_date": {"type": "string", "description": "YYYY-MM-DD format"},
                    "end_date": {"type": "string", "description": "YYYY-MM-DD format"},
                },
                "required": ["booking_id", "start_date", "end_date"],
                "additionalProperties": False,
            },
        ))

    # Privileged Base Tools
    if CURRENT_ROLE == "senior_manager":
        if TOOL_ENABLED_STATUS.get("override_cancellation_fee", True):
            tools.append(types.Tool(
                name="override_cancellation_fee",
                description="[PRIVILEGED] Override and waive cancellation fees on non-refundable bookings.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "booking_id": {"type": "integer"},
                        "waived_amount": {"type": "number"},
                    },
                    "required": ["booking_id", "waived_amount"],
                    "additionalProperties": False,
                },
            ))
        
        if TOOL_ENABLED_STATUS.get("admin_register_tool", True):
            tools.append(types.Tool(
                name="admin_register_tool",
                description="[ADMIN] Dynamically register a new tool definition at runtime.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "input_schema": {"type": "object"},
                    },
                    "required": ["name", "description", "input_schema"],
                    "additionalProperties": False,
                },
            ))

        if TOOL_ENABLED_STATUS.get("admin_toggle_tool", True):
            tools.append(types.Tool(
                name="admin_toggle_tool",
                description="[ADMIN] Dynamically enable or disable a tool at runtime.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "enabled": {"type": "boolean"},
                    },
                    "required": ["name", "enabled"],
                    "additionalProperties": False,
                },
            ))

    if TOOL_ENABLED_STATUS.get("admin_list_all_tools", True):
        tools.append(types.Tool(
            name="admin_list_all_tools",
            description="List all available tools and their live enabled/disabled status.",
            inputSchema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ))

    # 2. Dynamic Tools from Registry
    for name, entry in DYNAMIC_TOOL_REGISTRY.items():
        if entry.get("enabled", True) and entry.get("is_dynamic"):
            tools.append(entry["tool"])

    return tools

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    global CURRENT_ROLE

    # Check if tool is explicitly disabled
    if not TOOL_ENABLED_STATUS.get(name, True):
        return [types.TextContent(type="text", text=f"PERMISSION_DENIED: Tool '{name}' has been dynamically disabled by administrator.")]

    # 1. Base Tools Handlers
    if name == "get_itinerary_details":
        it_id = arguments.get("itinerary_id")
        return [types.TextContent(type="text", text=f"Itinerary #{it_id}: Active (London Autumn Break)")]

    elif name == "authenticate_manager":
        passcode = arguments.get("passcode")
        if passcode == "admin123":
            CURRENT_ROLE = "senior_manager"
            await broadcast_tool_list_changed()
            return [types.TextContent(type="text", text="Authentication successful! Senior Manager role activated.")]
        else:
            return [types.TextContent(type="text", text="Authentication failed: Invalid passcode.")]

    elif name == "cancel_booking":
        b_id = arguments.get("booking_id")
        reason = arguments.get("reason", "Customer request")
        confirmation = arguments.get("human_confirmation")
        is_refundable = False if b_id == 3 else True

        if not is_refundable:
            if not confirmation:
                return [
                    types.TextContent(
                        type="text",
                        text=(
                            f"ELICITATION_REQUIRED: Booking #{b_id} is NON-REFUNDABLE! "
                            f"Canceling will incur a $250.00 cancellation fee. "
                            f"Please respond with 'human_confirmation': 'APPROVED' or 'REJECTED' to proceed."
                        ),
                    )
                ]
            if confirmation.upper() != "APPROVED":
                return [types.TextContent(type="text", text=f"ABORTED: Cancellation for non-refundable Booking #{b_id} was rejected.")]
            return [types.TextContent(type="text", text=f"SUCCESS (ELICITED): Non-refundable Booking #{b_id} cancelled with human sign-off. Reason: {reason}")]

        return [types.TextContent(type="text", text=f"SUCCESS: Refundable Booking #{b_id} cancelled successfully. Full refund issued.")]

    elif name == "generate_itinerary_report":
        dest = arguments.get("destination")
        days = arguments.get("duration_days")
        try:
            session = app.request_context.session
            steps = 4
            for idx in range(1, steps + 1):
                if session:
                    await session.send_progress_notification(
                        progress_token=f"itinerary-{dest}",
                        progress=float(idx),
                        total=float(steps),
                    )
                await asyncio.sleep(0.2)
        except Exception:
            pass

        return [types.TextContent(type="text", text=f"COMPLETED: Generated {days}-day itinerary report for {dest}. All 4 processing stages finished successfully.")]

    elif name == "modify_booking_dates":
        if CURRENT_ROLE != "senior_manager":
            return [types.TextContent(type="text", text="PERMISSION_DENIED: Senior Manager authorization required to modify booking dates.")]

        b_id = arguments.get("booking_id")
        s_date = arguments.get("start_date")
        e_date = arguments.get("end_date")

        if e_date <= s_date:
            return [types.TextContent(type="text", text=f"VALIDATION_ERROR: End date ({e_date}) must be strictly after start date ({s_date}).")]

        return [types.TextContent(type="text", text=f"SUCCESS: Booking #{b_id} dates updated to {s_date} -> {e_date}.")]

    elif name == "override_cancellation_fee":
        if CURRENT_ROLE != "senior_manager":
            raise PermissionError("Handler Auth Failed: Senior Manager role required.")
        b_id = arguments.get("booking_id")
        amt = arguments.get("waived_amount")
        return [types.TextContent(type="text", text=f"SUCCESS: Waived ${amt} cancellation fee for Booking #{b_id}.")]

    elif name == "admin_register_tool":
        if CURRENT_ROLE != "senior_manager":
            return [types.TextContent(type="text", text="PERMISSION_DENIED: Senior Manager authorization required to register tools.")]
        t_name = arguments.get("name")
        t_desc = arguments.get("description")
        t_schema = arguments.get("input_schema", {})
        register_dynamic_tool(t_name, t_desc, t_schema, enabled=True)
        await broadcast_tool_list_changed()
        return [types.TextContent(type="text", text=f"SUCCESS: Dynamically registered tool '{t_name}'. Tool list updated.")]

    elif name == "admin_toggle_tool":
        if CURRENT_ROLE != "senior_manager":
            return [types.TextContent(type="text", text="PERMISSION_DENIED: Senior Manager authorization required to toggle tools.")]
        t_name = arguments.get("name")
        t_enabled = arguments.get("enabled", True)
        set_tool_enabled(t_name, t_enabled)
        await broadcast_tool_list_changed()
        status_str = "ENABLED" if t_enabled else "DISABLED"
        return [types.TextContent(type="text", text=f"SUCCESS: Tool '{t_name}' is now {status_str}. Tool list updated.")]

    elif name == "admin_list_all_tools":
        tools_data = get_all_registered_tools()
        return [types.TextContent(type="text", text=json.dumps(tools_data, indent=2))]

    # 2. Dynamic Tool Execution
    if name in DYNAMIC_TOOL_REGISTRY:
        entry = DYNAMIC_TOOL_REGISTRY[name]
        handler = entry.get("handler")
        if handler:
            if asyncio.iscoroutinefunction(handler):
                result = await handler(arguments)
            else:
                result = handler(arguments)
            return [types.TextContent(type="text", text=str(result))]
        return [types.TextContent(type="text", text=f"SUCCESS (DYNAMIC TOOL): Executed '{name}' with arguments: {arguments}")]

    raise ValueError(f"Unknown tool: {name}")

# ============================================================================
# DUAL TRANSPORT RUNNER (SSE / HTTP vs STDIO)
# ============================================================================
async def run_stdio():
    print("Starting MCP Server over stdio transport...")
    async with stdio_server() as (read_stream, write_stream):
        init_options = app.create_initialization_options()
        await app.run(read_stream, write_stream, init_options)

def run_sse(host: str = "0.0.0.0", port: int = 8000):
    print(f"🌐 Starting MCP Server over Streamable HTTP / SSE transport on http://{host}:{port}/sse")
    
    sse_transport = SseServerTransport("/messages")

    async def asgi_app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    break
        elif scope["type"] == "http":
            path = scope.get("path", "")
            method = scope.get("method", "GET")

            # Platform REST Endpoints for Tool Management
            if path == "/api/tools" and method == "GET":
                tools_data = get_all_registered_tools()
                body = json.dumps({"status": "success", "tools": tools_data}).encode("utf-8")
                await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": body})
                return

            elif path == "/api/tools/toggle" and method == "POST":
                body_bytes = b""
                while True:
                    chunk = await receive()
                    body_bytes += chunk.get("body", b"")
                    if not chunk.get("more_body", False):
                        break
                data = json.loads(body_bytes.decode("utf-8"))
                tool_name = data.get("name")
                enabled = data.get("enabled", True)
                set_tool_enabled(tool_name, enabled)
                await broadcast_tool_list_changed()
                res = json.dumps({"status": "success", "tool": tool_name, "enabled": enabled}).encode("utf-8")
                await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": res})
                return

            elif path == "/api/tools/register" and method == "POST":
                body_bytes = b""
                while True:
                    chunk = await receive()
                    body_bytes += chunk.get("body", b"")
                    if not chunk.get("more_body", False):
                        break
                data = json.loads(body_bytes.decode("utf-8"))
                register_dynamic_tool(data["name"], data["description"], data.get("input_schema", {}))
                await broadcast_tool_list_changed()
                res = json.dumps({"status": "success", "registered": data["name"]}).encode("utf-8")
                await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": res})
                return

            # Core MCP SSE routes
            elif path == "/sse":
                async with sse_transport.connect_sse(scope, receive, send) as (read_stream, write_stream):
                    init_options = app.create_initialization_options()
                    await app.run(read_stream, write_stream, init_options)
            elif path == "/messages":
                await sse_transport.handle_post_message(scope, receive, send)
            else:
                await send({"type": "http.response.start", "status": 404, "headers": []})
                await send({"type": "http.response.body", "body": b""})

    uvicorn.run(asgi_app, host=host, port=port, log_level="info")

if __name__ == "__main__":
    default_transport = os.getenv("MCP_TRANSPORT", "sse" if os.getenv("PORT_MCP") or os.getenv("WANDERPATH_ENV") else "stdio")
    default_port = int(os.getenv("PORT_MCP", 8000))
    parser = argparse.ArgumentParser(description="Wanderpath Travel Agent MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default=default_transport, help="Transport mechanism (default: sse in production/docker, stdio in CLI)")
    parser.add_argument("--port", type=int, default=default_port, help="Port for SSE transport")
    args = parser.parse_args()

    if args.transport == "stdio":
        asyncio.run(run_stdio())
    else:
        run_sse(port=args.port)