"""Bind one explicit job and current local JSON record at Hermes' tool boundary.

Bindings follow the public native delegation and compaction events. Independent
sessions retain their own scope. Only the /sourcebind command changes a binding.
"""

import json
import re
import shlex
from pathlib import Path


DEFAULT_TOOL = "sourcebind_demo_update"
DEMO_CUSTOMERS = {"nw-41": "Northwind (fictional)", "nw-42": "Northwind (fictional)", "ct-91": "Contoso (fictional)"}
USAGE = (
    "Usage: /sourcebind bind <job> <source.json> [protected_tool] [id-only]\n"
    "       /sourcebind status\n"
    "       /sourcebind clear"
)
SOURCE_ERRORS = (OSError, UnicodeError, ValueError)


def read_source(path):
    """Read the selected file afresh; retain just the two binding fields."""
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError("selected JSON must be an object")
    for field in ("job", "customer_id"):
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise ValueError(f"selected JSON requires a nonempty string {field}")
    return {"job": record["job"], "customer_id": record["customer_id"]}


def register(ctx):
    def scope_id(session_id):
        if not session_id:
            return ""
        return ctx.state.get("native_roots." + session_id, default=session_id)

    def subagent_start(parent_session_id="", child_session_id="", **kwargs):
        if parent_session_id and child_session_id:
            ctx.state.set("native_roots." + child_session_id, scope_id(parent_session_id))

    def post_compaction(session_id="", old_session_id="", **kwargs):
        if old_session_id and session_id and old_session_id != session_id:
            ctx.state.set("native_roots." + session_id, scope_id(old_session_id))

    def binding_for(session_id):
        binding = ctx.state.get("binding", default=None)
        if session_id and binding and binding["scope_id"] == scope_id(session_id):
            return binding
        return None

    def view(session_id):
        result = {
            "status": "unbound",
            "scope_id": scope_id(session_id),
            "binding": None,
            "current": None,
            "expected_args": None,
            "next_step": "Set an explicit binding with /sourcebind bind <job> <source.json> [protected_tool] [id-only].",
        }
        if not session_id:
            result.update(
                status="session_required",
                error="An active native Hermes session is required.",
                next_step="Run /sourcebind status in an active Hermes conversation.",
            )
            return result
        binding = binding_for(session_id)
        if binding is None:
            return result
        result["binding"] = dict(binding)
        try:
            record = read_source(binding["source"])
        except SOURCE_ERRORS as exc:
            result.update(
                status="blocked",
                error=f"Selected source read failed: {exc}",
                next_step=(
                    f"Repair the selected JSON at {binding['source']}, then read sourcebind_current. "
                    "Use /sourcebind clear to end this scope's binding."
                ),
            )
            return result
        result["current"] = record
        if record["job"] != binding["job"]:
            result.update(
                status="blocked",
                error=f"Selected source job {record['job']!r} differs from bound job {binding['job']!r}.",
                next_step=(
                    f"Restore the selected source job to {binding['job']!r}, "
                    "or explicitly select the new job with /sourcebind bind <job> <source.json> [protected_tool] [id-only]."
                ),
            )
            return result
        expected_args = record if binding.get("check_job_arg", True) else {"customer_id": record["customer_id"]}
        result.update(
            status="ready",
            expected_args=expected_args,
            next_step=f"Call {binding['protected_tool']} with {json.dumps(expected_args, ensure_ascii=False)}. Add other required tool arguments from your task. Read sourcebind_current again after a source change.",
        )
        return result

    def display(result):
        lines = [f"SourceBind {result['status'].upper()}", f"Scope: {result['scope_id'] or '(missing)'}"]
        if result["binding"]:
            binding = result["binding"]
            lines.extend(
                [f"Bound job: {binding['job']}", f"Protected tool: {binding['protected_tool']}", f"Source: {binding['source']}"]
            )
        if result["current"]:
            lines.extend([f"Current job: {result['current']['job']}", f"Current customer_id: {result['current']['customer_id']}"])
        if result["expected_args"]:
            lines.append("Expected tool args: " + json.dumps(result["expected_args"], ensure_ascii=False))
        if result.get("error"):
            lines.append(result["error"])
        lines.append("Next: " + result["next_step"])
        return "\n".join(lines)

    def command(raw="", *, session_id="", **kwargs):
        try:
            parts = shlex.split(raw)
        except ValueError as exc:
            return f"SourceBind command parse failed: {exc}\n{USAGE}"
        if not parts:
            return USAGE
        action = parts[0]
        if action == "status" and len(parts) == 1:
            return display(view(session_id))
        if action == "clear" and len(parts) == 1:
            if not session_id:
                return display(view(session_id))
            if binding_for(session_id):
                ctx.state.set("binding", None)
                return "SourceBind binding cleared.\n" + display(view(session_id))
            return display(view(session_id))
        if action != "bind" or len(parts) not in (3, 4, 5):
            return USAGE
        if len(parts) == 5 and parts[4] != "id-only":
            return USAGE
        if len(parts) == 4 and parts[3] == "id-only":
            return USAGE
        if not session_id:
            return display(view(session_id))
        job, source_arg = parts[1:3]
        tool = parts[3] if len(parts) >= 4 else DEFAULT_TOOL
        if not job.strip():
            return "SourceBind bind failed: job requires a nonempty string.\n" + USAGE
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tool) or tool == "sourcebind_current":
            return "SourceBind bind failed: choose a native update tool name (1–64 letters, digits, underscores or hyphens).\n" + USAGE
        try:
            source = str(Path(source_arg).expanduser().resolve())
            record = read_source(source)
        except SOURCE_ERRORS + (RuntimeError,) as exc:
            return f"SourceBind bind failed: selected source read failed: {exc}\n{USAGE}"
        if record["job"] != job:
            return f"SourceBind bind failed: selected source job is {record['job']!r}; requested job is {job!r}.\n{USAGE}"
        ctx.state.set("binding", {
            "scope_id": scope_id(session_id), "job": job, "source": source,
            "protected_tool": tool, "check_job_arg": len(parts) != 5,
        })
        return "SourceBind binding set.\n" + display(view(session_id))

    def current(args, *, session_id="", **kwargs):
        return json.dumps(view(session_id), ensure_ascii=False)

    def update(args, *, session_id="", **kwargs):
        if not isinstance(args, dict) or not isinstance(args.get("job"), str) or not args["job"].strip():
            return json.dumps({"error": "Fictional update requires a nonempty string job."})
        customer_id = args.get("customer_id")
        if not isinstance(customer_id, str) or customer_id not in DEMO_CUSTOMERS:
            return json.dumps({"error": "Unknown fictional customer_id. Valid demo IDs: nw-41, nw-42, ct-91."})
        records = ctx.state.get("demo_records", default={})
        record = {
            "customer_id": customer_id,
            "name": DEMO_CUSTOMERS[customer_id],
            "job": args["job"],
            "updates": records.get(customer_id, {}).get("updates", 0) + 1,
        }
        records[customer_id] = record
        ctx.state.set("demo_records", records)
        return json.dumps(
            {
                "ok": True,
                "demo": True,
                "updated_customer": customer_id,
                "scope_id": scope_id(session_id),
                "binding_status": "bound" if binding_for(session_id) else "unbound",
                "record": record,
                "next_step": "Fictional update recorded locally. Read sourcebind_current for the current binding.",
            },
            ensure_ascii=False,
        )

    def guard(tool_name, args, *, session_id="", **kwargs):
        binding = ctx.state.get("binding", default=None)
        if binding is None or tool_name != binding["protected_tool"]:
            return None
        if not session_id:
            return {"action": "block", "message": "SourceBind blocked: native session identity required. Run in an active Hermes conversation and read sourcebind_current."}
        if binding_for(session_id) is None:
            return None
        result = view(session_id)
        if result["status"] != "ready":
            return {"action": "block", "message": display(result)}
        expected = result["expected_args"]
        if not isinstance(args, dict) or any(args.get(field) != value for field, value in expected.items()):
            received = {field: args.get(field) for field in expected} if isinstance(args, dict) else args
            return {
                "action": "block",
                "message": (
                    f"SourceBind blocked {tool_name} in scope {result['scope_id']}: wrong current-source arguments. "
                    f"Expected current {json.dumps(expected, ensure_ascii=False)}; received {json.dumps(received, ensure_ascii=False)}. "
                    "Read sourcebind_current and retry with its expected_args."
                ),
            }
        return None

    def before_llm(user_message="", *, session_id="", **kwargs):
        return {"context": "SourceBind current scope: " + json.dumps(view(session_id), ensure_ascii=False)}

    ctx.register_tool(
        name="sourcebind_current",
        toolset="sourcebind",
        handler=current,
        schema={
            "name": "sourcebind_current",
            "description": "Read this scope's binding, current selected JSON job/customer_id, and expected tool args. Read-only; follow next_step after a block or source change.",
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
    )
    ctx.register_tool(
        name=DEFAULT_TOOL,
        toolset="sourcebind",
        handler=update,
        schema={
            "name": DEFAULT_TOOL,
            "description": "Update a fictional local customer record. Both nw-41 and ct-91 are valid; SourceBind guards the selected job/current ID after explicit binding. No network or payment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job": {"type": "string", "description": "The exact job from sourcebind_current."},
                    "customer_id": {"type": "string", "description": "The current selected customer_id; demo IDs are nw-41, nw-42 and ct-91."},
                },
                "required": ["job", "customer_id"],
                "additionalProperties": False,
            },
        },
    )
    ctx.register_hook("pre_tool_call", guard)
    ctx.register_hook("pre_llm_call", before_llm)
    ctx.register_hook("subagent_start", subagent_start)
    ctx.register_hook("post_compaction", post_compaction)
    ctx.register_command("sourcebind", command, description="Explicitly bind, inspect or clear one job/current JSON source for this native scope.")
