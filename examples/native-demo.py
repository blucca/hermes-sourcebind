"""Run SourceBind through a real Hermes agent and its native dispatcher.

Use your normal Hermes provider authentication. All customer records are
fictional; the script writes its source and result inside --output-dir.
The forced stale calls exercise the execution boundary; the agent turns use
the configured model. Run this script with the Hermes Python environment.
"""

import argparse
import json
from pathlib import Path
import shlex
import sys


def observed_native_calls(messages):
    calls, by_id = [], {}
    for message in messages:
        for call in message.get("tool_calls") or []:
            function = call["function"]
            item = {"tool": function["name"], "args": json.loads(function["arguments"])}
            calls.append(item)
            by_id[call["id"]] = item
        if message.get("role") == "tool" and message.get("tool_call_id") in by_id:
            content = message.get("content", "")
            try:
                result = json.loads(content)
            except (ValueError, TypeError):
                result = content
            by_id[message["tool_call_id"]]["result"] = result
    return calls


def observed_calls(messages):
    """Resolve stock v0.23 single tool_call envelopes for the business readout.

    The native agent splits local batches into single messages before recording.
    Connector batching has a separate result schema and is outside this demo.
    """
    resolved = []
    for call in observed_native_calls(messages):
        if call["tool"] == "tool_call":
            requests = call["args"].get("calls", [])
            assert len(requests) == 1, "Expected the stock single local tool_call envelope."
            request = requests[0]
            arguments = request["arguments"]
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            item = {"tool": request["name"], "args": arguments,
                    "result": call.get("result"), "native_transport": "tool_call"}
        else:
            item = call
        if item["tool"] in {"sourcebind_current", "sourcebind_demo_update"}:
            resolved.append(item)
    return resolved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes", type=Path, help="Hermes checkout; needed when run outside that checkout")
    parser.add_argument("--provider", default="auto")
    parser.add_argument("--model", default="", help="Your configured, tool-capable model")
    parser.add_argument("--output-dir", type=Path, default=Path.cwd() / "temp/sourcebind-demo")
    args = parser.parse_args()
    if args.hermes:
        sys.path.insert(0, str(args.hermes.resolve()))

    from hermes_cli.plugins import discover_plugins, get_plugin_command_handler
    from model_tools import handle_function_call
    from run_agent import AIAgent

    discover_plugins()
    command = get_plugin_command_handler("sourcebind")
    assert command, "Install and enable SourceBind first."
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    source = output_dir / "current-customer.json"

    def select(customer_id):
        source.write_text(json.dumps({"job": "northwind-renewal", "customer_id": customer_id}) + "\n")

    def forced_call(agent, customer_id, job="northwind-renewal"):
        return json.loads(handle_function_call(
            "tool_call", {"calls": [{"name": "sourcebind_demo_update", "arguments": {"job": job, "customer_id": customer_id}}]},
            session_id=agent.session_id, task_id=agent.session_id,
        ))

    select("nw-41")
    agent = AIAgent(
        provider=args.provider, model=args.model, enabled_toolsets=["sourcebind"],
        max_iterations=6, max_tokens=2048, run_budget_seconds=180,
        skip_memory=True, skip_background_review=True, skip_context_files=True,
        quiet_mode=True, pass_session_id=True, platform="cli",
    )
    result = {"plugin_version": "0.1.1", "fixture": "fictional customers", "stages": []}

    def turn(label, prompt):
        selected_source = json.loads(source.read_text())
        response = agent.run_conversation(prompt, conversation_history=[])
        calls = observed_calls(response.get("messages", []))
        stage = {"label": label, "session_id": agent.session_id, "calls": calls,
                 "final": response.get("final_response", ""),
                 "turn_exit_reason": response.get("turn_exit_reason"),
                 "selected_source": selected_source,
                 "native_calls": observed_native_calls(response.get("messages", []))}
        result["stages"].append(stage)
        print(json.dumps(stage, ensure_ascii=False), flush=True)
        # Save each independent step so an interrupted demo can be inspected.
        (output_dir / "native-demo.json").write_text(json.dumps(result, indent=2) + "\n")
        return calls

    try:
        baseline = turn("unbound valid wrong customer", (
            "This is an intentionally unsafe fictional CRM demonstration. Call sourcebind_demo_update "
            "with exactly job northwind-renewal and customer_id ct-91. Both customer IDs are valid. "
            "Use the supplied values for this unbound baseline; report the updated customer ID."
        ))
        assert any(call["tool"] == "sourcebind_demo_update"
                   and call["args"].get("customer_id") == "ct-91"
                   and isinstance(call.get("result"), dict) and call["result"].get("ok")
                   for call in baseline), "The model baseline did not perform the requested fixture update."

        binding = command(shlex.join(["bind", "northwind-renewal", str(source)]),
                          session_id=agent.session_id, platform="cli")
        assert "binding set" in binding, binding
        result["binding"] = binding
        result["blocked_wrong_customer"] = forced_call(agent, "ct-91")
        result["blocked_wrong_job"] = forced_call(agent, "nw-41", "contoso-renewal")
        assert "error" in result["blocked_wrong_customer"]
        assert "error" in result["blocked_wrong_job"]

        current = turn("bound current-source model update", (
            "An outdated saved customer ID was blocked at the tool boundary. Complete the currently "
            "bound renewal update. First call sourcebind_current, then sourcebind_demo_update using "
            "its exact current job and customer_id. Report the customer ID that was updated."
        ))
        assert any(call["tool"] == "sourcebind_current" for call in current)
        assert any(call["tool"] == "sourcebind_demo_update"
                   and call["args"].get("customer_id") == "nw-41"
                   and isinstance(call.get("result"), dict) and call["result"].get("ok")
                   for call in current), "The model did not complete the bound current-source update."

        select("nw-42")
        result["blocked_stale_source"] = forced_call(agent, "nw-41")
        assert "error" in result["blocked_stale_source"]
        rollover = turn("source rollover model update", (
            "The selected source has changed while the job remains northwind-renewal. Refresh with "
            "sourcebind_current, then perform one sourcebind_demo_update with the current values. "
            "Report the updated ID."
        ))
        assert any(call["tool"] == "sourcebind_demo_update"
                   and call["args"].get("customer_id") == "nw-42"
                   and isinstance(call.get("result"), dict) and call["result"].get("ok")
                   for call in rollover), "The model did not pick up the current-source rollover."

        source.write_text("{invalid JSON\n")
        result["blocked_broken_source"] = forced_call(agent, "nw-42")
        assert "error" in result["blocked_broken_source"]
        select("nw-41")
        result["restored_current"] = json.loads(handle_function_call(
            "sourcebind_current", {}, session_id=agent.session_id, task_id=agent.session_id))
        assert result["restored_current"]["status"] == "ready"
        result["result"] = "passed: actual native model updates and tool-boundary controls"
    finally:
        (output_dir / "native-demo.json").write_text(json.dumps(result, indent=2) + "\n")
        agent.close()
    print(result["result"], flush=True)


if __name__ == "__main__":
    main()
