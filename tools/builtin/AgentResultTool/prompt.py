"""AgentResultTool prompt."""

DESCRIPTION = (
    "Retrieve the result of a background agent launched earlier with the Agent "
    "tool (run_in_background=true)."
)

AGENT_RESULT_TOOL_PROMPT = """Retrieve the output of a background subagent.

When you launch an agent with run_in_background=true, the Agent tool returns a
task_id immediately and the agent keeps working. Use this tool with that
task_id to collect its result once it has finished.

- If the agent is still running, this returns a "running" status — wait and
  try again, or do other work in the meantime.
- If the agent has finished, this returns its full output.
- Call with no task_id to list all background agents and their status.
"""
