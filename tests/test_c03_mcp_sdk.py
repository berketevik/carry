"""C03 verification with the official MCP SDK, when it is installed.

The server itself has no SDK dependency; this test proves the hand-written
transport is what a real client expects. It skips when the SDK is absent, so a
dependency-free checkout still runs the whole suite.
"""
import asyncio
import importlib.util
import os
import sys
import unittest

from _support import SRC, WorkspaceCase

HAVE_SDK = importlib.util.find_spec("mcp") is not None


@unittest.skipUnless(HAVE_SDK, "official MCP SDK not installed")
class SDKClientTest(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.cedar()
        self.build()

    def session(self, coroutine):
        return asyncio.run(coroutine)

    async def _talk(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        parameters = StdioServerParameters(
            command=sys.executable, args=["-m", "carry.mcp_server"],
            env={"PYTHONPATH": str(SRC), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                 "CARRY_WORKSPACE": str(self.workspace.state_dir)})
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                initialised = await session.initialize()
                tools = await session.list_tools()
                evidence = await session.call_tool(
                    "carry_recall", {"query": "When is the Cedar pilot delivery?"})
                empty = await session.call_tool(
                    "carry_recall", {"query": "Cedar pilot delivery",
                                     "source_ids": ["records"]})
                status = await session.call_tool("carry_status", {})
                catalog = await session.call_tool("carry_catalog", {"folder": "notes"})
                failure = await session.call_tool("carry_recall", {"query": "  "})
                return initialised, tools, evidence, empty, status, failure, catalog

    def test_a_real_client_completes_a_full_session(self):
        initialised, tools, evidence, empty, status, failure, catalog = self.session(self._talk())
        self.assertEqual(initialised.serverInfo.name, "carry")
        self.assertEqual({tool.name for tool in tools.tools}, {"carry_recall", "carry_catalog", "carry_status"})

        text = evidence.content[0].text
        self.assertFalse(evidence.isError)
        self.assertIn("October 15, 2026", text)
        self.assertIn("corpus:notes/Cedar Pilot Plan.md", text)

        self.assertFalse(empty.isError)
        self.assertIn("No passage matched", empty.content[0].text)

        self.assertFalse(status.isError)
        self.assertIn('"state": "fresh"', status.content[0].text)

        self.assertTrue(failure.isError)
        self.assertFalse(catalog.isError)
        self.assertIn("corpus:notes/Cedar Pilot Plan.md", catalog.content[0].text)


if __name__ == "__main__":
    unittest.main()
