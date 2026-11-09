"""Standard-library checks of SourceBind's public registration callbacks.

Run from the working checkout: python3 tests/check.py
Native Hermes installation/execution is a separate validation step.
"""

import copy
import importlib.util
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("sourcebind_under_test", PLUGIN_ROOT / "__init__.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)
TEMP_ROOT = Path(os.environ.get("WORKSPACE_ROOT", Path.cwd())) / "temp" / "sourcebind-tests"


class State:
    """The public ctx.state get/set shape, with JSON-compatible values."""

    def __init__(self, values=None):
        self.values = copy.deepcopy(values or {})
        self.writes = 0

    def get(self, key, default=None):
        value = self.values
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                return copy.deepcopy(default)
            value = value[part]
        return copy.deepcopy(value)

    def set(self, key, value):
        container = self.values
        parts = key.split(".")
        for part in parts[:-1]:
            container = container.setdefault(part, {})
        container[parts[-1]] = copy.deepcopy(value)
        self.writes += 1


class Context:
    def __init__(self, state=None):
        self.state = state or State()
        self.tools = {}
        self.hooks = {}
        self.commands = {}

    def register_tool(self, **registration):
        self.tools[registration["name"]] = registration

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_command(self, name, callback, **kwargs):
        self.commands[name] = callback


class SourceBindChecks(unittest.TestCase):
    def setUp(self):
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="case-", dir=TEMP_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "current customer.json"
        self.write_source()
        self.ctx = Context()
        plugin.register(self.ctx)
        self.command = self.ctx.commands["sourcebind"]
        self.session = "native-root"

    def write_source(self, job="northwind-renewal", customer_id="nw-41"):
        self.source.write_text(json.dumps({"job": job, "customer_id": customer_id}), encoding="utf-8")

    def bind(self, tool=None, session=None):
        args = ["bind", "northwind-renewal", str(self.source)]
        if tool:
            args.append(tool)
        return self.command(shlex.join(args), session_id=session or self.session)

    def current(self, session=None, **kwargs):
        return json.loads(self.ctx.tools["sourcebind_current"]["handler"]({}, session_id=session or self.session, **kwargs))

    def guard(self, args, tool="sourcebind_demo_update", session=None, **kwargs):
        return self.ctx.hooks["pre_tool_call"](tool, args, session_id=session or self.session, **kwargs)

    def call(self, args, session=None):
        """Exercise the hook before the demo handler, matching the native seam."""
        blocked = self.guard(args, session=session)
        if blocked:
            return {"blocked": True, "error": blocked["message"]}
        return json.loads(self.ctx.tools["sourcebind_demo_update"]["handler"](args, session_id=session or self.session))

    def test_registration_and_fixture(self):
        self.assertEqual(set(self.ctx.tools), {"sourcebind_current", "sourcebind_demo_update"})
        self.assertEqual(set(self.ctx.hooks), {"pre_tool_call", "pre_llm_call", "subagent_start", "post_compaction"})
        self.assertEqual(set(self.ctx.commands), {"sourcebind"})
        schema = self.ctx.tools["sourcebind_demo_update"]["schema"]["parameters"]
        self.assertEqual(schema["required"], ["job", "customer_id"])
        self.assertEqual(set(schema["properties"]), {"job", "customer_id"})
        self.assertEqual(plugin.read_source(PLUGIN_ROOT / "examples/current-customer.json"), {"job": "northwind-renewal", "customer_id": "nw-41"})
        manifest = (PLUGIN_ROOT / "plugin.yaml").read_text(encoding="utf-8")
        self.assertIn("name: sourcebind\n", manifest)
        self.assertIn("version: 0.1.1\n", manifest)

    def test_bind_status_read_only_and_clear_lifecycle(self):
        self.assertEqual(self.current()["status"], "unbound")
        self.assertIn("binding set", self.bind())
        view = self.current()
        self.assertEqual(view["status"], "ready")
        self.assertEqual(view["binding"]["source"], str(self.source.resolve()))
        self.assertEqual(view["binding"]["protected_tool"], "sourcebind_demo_update")
        before = self.ctx.state.writes
        self.current()
        self.ctx.hooks["pre_llm_call"](session_id=self.session)
        status = self.command("status", session_id=self.session)
        self.assertIn("Current customer_id: nw-41", status)
        self.assertEqual(self.ctx.state.writes, before)
        self.assertIn("binding cleared", self.command("clear", session_id=self.session))
        self.assertEqual(self.current()["status"], "unbound")
        self.assertIsNone(self.ctx.state.get("binding"))

    def test_two_valid_ids_and_boundary_blocks(self):
        wrong = {"job": "northwind-renewal", "customer_id": "ct-91"}
        self.assertTrue(self.call(wrong)["ok"])
        self.bind()
        before = self.ctx.state.get("demo_records")
        blocked = self.call(wrong)
        self.assertTrue(blocked["blocked"])
        self.assertIn('"customer_id": "nw-41"', blocked["error"])
        self.assertIn("sourcebind_current", blocked["error"])
        self.assertEqual(self.ctx.state.get("demo_records"), before)
        self.assertTrue(self.call({"job": "contoso-renewal", "customer_id": "nw-41"})["blocked"])
        allowed = self.call({"job": "northwind-renewal", "customer_id": "nw-41"})
        self.assertTrue(allowed["ok"])
        self.assertEqual(allowed["binding_status"], "bound")
        self.assertEqual(allowed["record"]["job"], "northwind-renewal")
        self.assertEqual(set(self.ctx.state.get("demo_records")), {"nw-41", "ct-91"})

    def test_selected_source_rollover(self):
        self.bind()
        self.write_source(customer_id="ct-91")
        self.assertEqual(self.current()["current"]["customer_id"], "ct-91")
        self.assertTrue(self.call({"job": "northwind-renewal", "customer_id": "nw-41"})["blocked"])
        self.assertTrue(self.call({"job": "northwind-renewal", "customer_id": "ct-91"})["ok"])
        context = self.ctx.hooks["pre_llm_call"](session_id=self.session)["context"]
        self.assertIn('"customer_id": "ct-91"', context)

    def test_selected_source_failures_block_and_repair_restores(self):
        self.bind()
        binding = self.ctx.state.get("binding")
        for bad in (b"{broken", b"[]", b'{"job":"northwind-renewal"}', b'{"job":"northwind-renewal","customer_id":42}', b"\xff"):
            with self.subTest(source=bad):
                self.source.write_bytes(bad)
                result = self.current()
                self.assertEqual(result["status"], "blocked")
                self.assertIn("Selected source read failed", result["error"])
                self.assertTrue(self.call({"job": "northwind-renewal", "customer_id": "nw-41"})["blocked"])
                self.assertEqual(self.ctx.state.get("binding"), binding)
        self.source.unlink()
        self.assertEqual(self.current()["status"], "blocked")
        self.assertTrue(self.call({"job": "northwind-renewal", "customer_id": "nw-41"})["blocked"])
        self.write_source()
        self.assertEqual(self.current()["status"], "ready")

    def test_source_job_drift_requires_explicit_rebind(self):
        self.bind()
        binding = self.ctx.state.get("binding")
        self.write_source(job="contoso-renewal", customer_id="ct-91")
        result = self.current()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["current"]["job"], "contoso-renewal")
        self.assertTrue(self.call({"job": "contoso-renewal", "customer_id": "ct-91"})["blocked"])
        self.assertIn("bind failed", self.bind())
        self.assertEqual(self.ctx.state.get("binding"), binding)
        self.command(shlex.join(["bind", "contoso-renewal", str(self.source)]), session_id=self.session)
        self.assertEqual(self.current()["status"], "ready")

    def test_one_configurable_native_target(self):
        self.bind(tool="crm_update")
        wrong = {"job": "northwind-renewal", "customer_id": "ct-91"}
        self.assertIsNone(self.guard(wrong))
        self.assertEqual(self.guard(wrong, tool="crm_update")["action"], "block")
        self.assertIsNone(self.guard({"job": "northwind-renewal", "customer_id": "nw-41"}, tool="crm_update"))
        self.assertEqual(self.current()["binding"]["protected_tool"], "crm_update")
        self.bind()
        self.assertEqual(self.current()["binding"]["protected_tool"], "sourcebind_demo_update")
        self.assertIsNone(self.guard(wrong, tool="crm_update"))

    def test_delegation_compaction_and_reloaded_state_keep_stable_scope(self):
        self.bind()
        self.ctx.hooks["subagent_start"](parent_session_id=self.session, child_session_id="native-child")
        self.ctx.hooks["post_compaction"](old_session_id="native-child", session_id="native-child-rotated")
        self.ctx.hooks["post_compaction"](old_session_id=self.session, session_id="native-root-rotated")
        self.ctx.hooks["subagent_start"](parent_session_id="native-root-rotated", child_session_id="native-grandchild")
        wrong = {"job": "northwind-renewal", "customer_id": "ct-91"}
        for session in ("native-child", "native-child-rotated", "native-root-rotated", "native-grandchild"):
            with self.subTest(session=session):
                self.assertEqual(self.current(session=session)["scope_id"], self.session)
                self.assertTrue(self.call(wrong, session=session)["blocked"])
        persisted = json.loads(json.dumps(self.ctx.state.values))
        self.ctx = Context(State(persisted))
        plugin.register(self.ctx)
        self.command = self.ctx.commands["sourcebind"]
        self.assertEqual(self.current(session="native-child-rotated")["scope_id"], self.session)
        self.assertTrue(self.call(wrong, session="native-child-rotated")["blocked"])
        self.command("clear", session_id="native-child-rotated")
        self.assertEqual(self.current()["status"], "unbound")

    def test_independent_session_and_context_are_isolated(self):
        self.bind()
        self.source.unlink()
        kwargs = {"parent_session_id": self.session, "task_id": self.session, "user_task": "northwind-renewal"}
        result = self.current(session="independent", **kwargs)
        self.assertEqual(result["status"], "unbound")
        self.assertEqual(result["scope_id"], "independent")
        self.assertIsNone(result["binding"])
        self.assertIsNone(result["current"])
        context = self.ctx.hooks["pre_llm_call"](session_id="independent", **kwargs)["context"]
        self.assertNotIn(str(self.source), context)
        self.assertNotIn("nw-41", context)
        self.assertIsNone(self.guard({"job": "northwind-renewal", "customer_id": "ct-91"}, session="independent", **kwargs))
        before = self.ctx.state.get("binding")
        self.command("clear", session_id="independent")
        self.assertEqual(self.ctx.state.get("binding"), before)

    def test_parse_and_bind_failures_preserve_existing_binding(self):
        self.bind()
        before = self.ctx.state.get("binding")
        for raw in ("", "bind", "bind one", "bind one two three four", "status extra", "clear extra", "unknown", "bind 'unterminated"):
            with self.subTest(raw=raw):
                self.assertIn("Usage:", self.command(raw, session_id=self.session))
                self.assertEqual(self.ctx.state.get("binding"), before)
        for job, tool in (("", "sourcebind_demo_update"), ("northwind-renewal", "bad/name"), ("northwind-renewal", "sourcebind_current")):
            with self.subTest(job=job, tool=tool):
                self.assertIn("bind failed", self.command(shlex.join(["bind", job, str(self.source), tool]), session_id=self.session))
                self.assertEqual(self.ctx.state.get("binding"), before)

    def test_paths_with_spaces_relative_paths_and_tilde(self):
        relative = os.path.relpath(self.source, Path.cwd())
        self.command(shlex.join(["bind", "northwind-renewal", relative]), session_id=self.session)
        self.assertEqual(self.current()["binding"]["source"], str(self.source.resolve()))
        home_before = os.environ.get("HOME")
        os.environ["HOME"] = self.temp.name
        try:
            self.command(shlex.join(["bind", "northwind-renewal", "~/current customer.json"]), session_id=self.session)
            self.assertEqual(self.current()["binding"]["source"], str(self.source.resolve()))
        finally:
            if home_before is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = home_before

    def test_missing_native_identity_and_malformed_tool_args(self):
        self.assertIn("SESSION_REQUIRED", self.command("status"))
        self.assertIn("SESSION_REQUIRED", self.command(shlex.join(["bind", "northwind-renewal", str(self.source)])))
        self.assertIsNone(self.ctx.state.get("binding"))
        self.bind()
        blocked = self.ctx.hooks["pre_tool_call"]("sourcebind_demo_update", {"job": "northwind-renewal", "customer_id": "nw-41"})
        self.assertEqual(blocked["action"], "block")
        for args in ({}, {"job": "northwind-renewal", "customer_id": 41}, None, []):
            with self.subTest(args=args):
                self.assertEqual(self.guard(args)["action"], "block")
        handler = self.ctx.tools["sourcebind_demo_update"]["handler"]
        self.assertIn("error", json.loads(handler({"job": "", "customer_id": "nw-41"})))
        self.assertIn("error", json.loads(handler({"job": "northwind-renewal", "customer_id": "unknown"})))


if __name__ == "__main__":
    unittest.main(verbosity=2)
