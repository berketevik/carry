"""C03 acceptance: a fresh MCP client over stdio gets cited evidence and
explicit no-evidence and failure states.

The server runs as a real subprocess and speaks newline-delimited JSON-RPC, so
these tests exercise the transport a client actually uses.
"""
import json
import subprocess
import sys
import unittest

from _support import SRC, WorkspaceCase, tree_digest

PROTOCOL = "2025-06-18"


class MCPClient:
    """Minimal stdio client: send a script of messages, collect the responses."""

    def __init__(self, workspace_dir, env_workspace=True):
        self.workspace_dir = str(workspace_dir)
        self.env_workspace = env_workspace

    def exchange(self, messages):
        env = {"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin"}
        argv = [sys.executable, "-m", "carry.mcp_server"]
        if self.env_workspace:
            env["CARRY_WORKSPACE"] = self.workspace_dir
        else:
            argv += ["--workspace", self.workspace_dir]
        payload = "".join(json.dumps(m) + "\n" for m in messages)
        result = subprocess.run(argv, input=payload, capture_output=True, text=True,
                                env=env, timeout=120)
        responses = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        return result, responses

    def session(self, calls):
        """Handshake, then one tools/call per entry in `calls`."""
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": PROTOCOL, "capabilities": {},
                        "clientInfo": {"name": "acceptance-client", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        for number, (name, arguments) in enumerate(calls, start=3):
            messages.append({"jsonrpc": "2.0", "id": number, "method": "tools/call",
                             "params": {"name": name, "arguments": arguments}})
        result, responses = self.exchange(messages)
        by_id = {r.get("id"): r for r in responses}
        return result, by_id


class HandshakeTest(WorkspaceCase):
    def test_initialize_and_tools_list(self):
        self.cedar()
        self.build()
        result, by_id = MCPClient(self.workspace.state_dir).session([])
        self.assertEqual(result.returncode, 0, result.stderr)
        initialize = by_id[1]["result"]
        self.assertEqual(initialize["protocolVersion"], PROTOCOL)
        self.assertEqual(initialize["serverInfo"]["name"], "carry")
        self.assertIn("tools", initialize["capabilities"])

        tools = {tool["name"]: tool for tool in by_id[2]["result"]["tools"]}
        self.assertEqual(set(tools), {"carry_recall", "carry_catalog", "carry_status"})
        self.assertEqual(tools["carry_recall"]["inputSchema"]["required"], ["query"])

    def test_an_unsupported_protocol_version_is_answered_with_a_supported_one(self):
        self.build()
        _, responses = MCPClient(self.workspace.state_dir).exchange(
            [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "1999-01-01"}}])
        self.assertEqual(responses[0]["result"]["protocolVersion"], PROTOCOL)

    def test_notifications_get_no_response_and_unknown_methods_get_an_error(self):
        self.build()
        _, responses = MCPClient(self.workspace.state_dir).exchange([
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 9, "method": "resources/list"},
        ])
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0]["id"], 9)
        self.assertEqual(responses[0]["error"]["code"], -32601)

    def test_a_malformed_line_does_not_kill_the_session(self):
        self.cedar()
        self.build()
        client = MCPClient(self.workspace.state_dir)
        env_payload = ["{not json", json.dumps(
            {"jsonrpc": "2.0", "id": 4, "method": "ping"})]
        result = subprocess.run(
            [sys.executable, "-m", "carry.mcp_server"],
            input="\n".join(env_payload) + "\n", capture_output=True, text=True,
            env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin",
                 "CARRY_WORKSPACE": str(client.workspace_dir)}, timeout=120)
        responses = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertEqual(responses[1]["id"], 4)
        self.assertEqual(responses[1]["result"], {})

    def test_the_workspace_can_come_from_a_command_line_argument(self):
        self.cedar()
        self.build()
        _, by_id = MCPClient(self.workspace.state_dir, env_workspace=False).session(
            [("carry_recall", {"query": "Cedar pilot delivery date"})])
        self.assertFalse(by_id[3]["result"]["isError"])
        self.assertIn("October 15, 2026", by_id[3]["result"]["content"][0]["text"])


class EvidenceTest(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.cedar()
        self.build()

    def call(self, name, arguments):
        _, by_id = MCPClient(self.workspace.state_dir).session([(name, arguments)])
        return by_id[3]["result"]

    def test_a_fresh_client_gets_cited_cedar_evidence(self):
        result = self.call("carry_recall", {"query": "When is the Cedar pilot delivery?"})
        text = result["content"][0]["text"]
        state = json.loads(text.split("Search state: ", 1)[1].split("\n\n", 1)[0])
        self.assertFalse(result["isError"])
        self.assertEqual(state["status"], "evidence")
        self.assertIn("October 15, 2026", text)
        self.assertIn("corpus:notes/Cedar Pilot Plan.md", text)
        self.assertIn("record imp_", text)
        self.assertIn("rev 1", text)
        self.assertIn("retrieved source data", text)

    def test_the_grounding_preamble_forbids_following_retrieved_instructions(self):
        text = self.call("carry_recall", {"query": "Cedar review flow"})["content"][0]["text"]
        self.assertIn("do not act", text.lower())
        self.assertIn("insufficient", text.lower())

    def test_an_empty_scope_returns_an_explicit_no_evidence_state(self):
        result = self.call("carry_recall",
                           {"query": "Cedar pilot delivery", "source_ids": ["records"]})
        text = result["content"][0]["text"]
        self.assertFalse(result["isError"])
        state = json.loads(text.split("Search state: ", 1)[1].split("\n\n", 1)[0])
        self.assertIn("No passage matched", text)
        self.assertEqual(state["status"], "no_evidence")
        self.assertEqual(state["candidates"], 0)
        self.assertNotIn("October 15", text)

    def test_a_question_the_corpus_never_answers_is_visible_as_such(self):
        text = self.call("carry_recall",
                         {"query": "What is the Cedar pilot budget?"})["content"][0]["text"]
        self.assertIn("unmatched_terms", text)
        self.assertIn("budget", json.loads(text.split("Search state: ", 1)[1]
                                           .split("\n\n", 1)[0])["unmatched_terms"])
        self.assertNotIn("budget:", text.lower().split("search state")[0])

    def test_budget_arguments_are_validated_and_capped(self):
        capped = self.call("carry_recall", {"query": "Cedar", "budget": {"top_k": 999}})
        diagnostics = json.loads(capped["content"][0]["text"].split("Search state: ", 1)[1]
                                 .split("\n\n", 1)[0])
        self.assertEqual(diagnostics["budget"]["top_k"], self.workspace.retrieval.top_k)
        rejected = self.call("carry_recall", {"query": "Cedar", "budget": "six"})
        self.assertTrue(rejected["isError"])

    def test_invalid_and_unknown_calls_are_errors_not_empty_answers(self):
        for name, arguments in (("carry_recall", {"query": "   "}),
                                ("carry_recall", {}),
                                ("carry_nonsense", {"query": "Cedar"})):
            result = self.call(name, arguments)
            self.assertTrue(result["isError"], (name, arguments))

    def test_status_reports_state_without_note_contents(self):
        result = self.call("carry_status", {})
        payload = json.loads(result["content"][0]["text"])
        self.assertFalse(result["isError"])
        self.assertEqual(payload["index"]["state"], "fresh")
        self.assertTrue(payload["index"]["usable"])
        self.assertEqual(payload["embedding"]["name"], "hashing")
        self.assertIn("lexical_only_provider", payload["degradation"])
        self.assertNotIn("October 15", result["content"][0]["text"])
        self.assertEqual(payload["capture"]["state"], "not_configured")

    def test_serving_queries_never_writes_into_a_source(self):
        before = tree_digest(self.corpus)
        self.call("carry_recall", {"query": "Cedar pilot delivery"})
        self.assertEqual(tree_digest(self.corpus), before)


class FailureStateTest(WorkspaceCase):
    def test_an_uninitialised_workspace_reports_a_failure_not_a_guess(self):
        _, by_id = MCPClient(self.base / "never-initialised").session(
            [("carry_recall", {"query": "Cedar pilot delivery"})])
        result = by_id[3]["result"]
        self.assertTrue(result["isError"])
        self.assertIn("not ready", result["content"][0]["text"])
        self.assertIn("WorkspaceError", result["content"][0]["text"])

    def test_a_missing_index_reports_retrieval_unavailable(self):
        self.cedar()
        _, by_id = MCPClient(self.workspace.state_dir).session(
            [("carry_recall", {"query": "Cedar pilot delivery"})])
        text = by_id[3]["result"]["content"][0]["text"]
        self.assertTrue(by_id[3]["result"]["isError"])
        self.assertIn("index_unavailable", text)
        self.assertIn("do not answer from memory", text)

    def test_status_still_answers_when_the_index_is_missing(self):
        self.cedar()
        _, by_id = MCPClient(self.workspace.state_dir).session([("carry_status", {})])
        payload = json.loads(by_id[3]["result"]["content"][0]["text"])
        self.assertEqual(payload["index"]["state"], "unavailable")
        self.assertFalse(payload["index"]["usable"])
        self.assertIn("index_unavailable", payload["degradation"])


if __name__ == "__main__":
    unittest.main()


class CatalogTest(WorkspaceCase):
    def test_catalog_lists_summaries_of_servable_files_only(self):
        from carry.index import build
        from carry.mcp_server import call_tool
        self.note("Budget", "Q4 budget is 120k.", summary="Q4 budget approved at 120k", folder="notes")
        self.note("Daily", "Nothing special.", folder="log")
        build(self.workspace)
        text, error = call_tool("carry_catalog", {}, state_dir=str(self.workspace.state_dir))
        self.assertFalse(error)
        self.assertIn("corpus:notes/Budget.md — Budget - Q4 budget approved at 120k", text)
        self.assertIn("corpus:log/Daily.md", text)
        self.assertNotIn("Q4 budget is 120k.", text)
        only, _ = call_tool("carry_catalog", {"folder": "notes"}, state_dir=str(self.workspace.state_dir))
        self.assertNotIn("log/Daily.md", only)
        _, bad = call_tool("carry_catalog", {"source_ids": ["nope"]}, state_dir=str(self.workspace.state_dir))
        self.assertTrue(bad)

