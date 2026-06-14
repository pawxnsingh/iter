"""
Agent (subagent) tool prompt, rendered for this configuration: inline agent
list, concurrency note included, no fork/coordinator/teammate variants, and
Glob/Grep available as dedicated tools.

Tool name constants substituted statically.

`render_agent_prompt(agent_lines)` builds the final string with the runtime
agent list. AGENT_PROMPT is the static module-level value computed from
BUILT_IN_AGENTS at import time so the Tool class attribute is populated.
"""

from tools.builtin.AgentTool.built_in_types import AgentDefinition


def format_agent_line(agent: AgentDefinition) -> str:
    """Mirrors the formatAgentLine.

    Format: `- type: whenToUse (Tools: ...)`
    """
    if agent.tools and len(agent.tools) > 0 and agent.disallowed_tools:
        deny_set = set(agent.disallowed_tools)
        effective = [t for t in agent.tools if t not in deny_set]
        tools_desc = ", ".join(effective) if effective else "None"
    elif agent.tools and len(agent.tools) > 0:
        tools_desc = ", ".join(agent.tools)
    elif agent.disallowed_tools:
        tools_desc = f"All tools except {', '.join(agent.disallowed_tools)}"
    else:
        tools_desc = "All tools"
    return f"- {agent.agent_type}: {agent.when_to_use} (Tools: {tools_desc})"


CURRENT_EXAMPLES = """Example usage:

<example_agent_descriptions>
"test-runner": use this agent after you are done writing code to run tests
"greeting-responder": use this agent to respond to user greetings with a friendly joke
</example_agent_descriptions>

<example>
user: "Please write a function that checks if a number is prime"
assistant: I'm going to use the Write tool to write the following code:
<code>
function isPrime(n) {
  if (n <= 1) return false
  for (let i = 2; i * i <= n; i++) {
    if (n % i === 0) return false
  }
  return true
}
</code>
<commentary>
Since a significant piece of code was written and the task was completed, now use the test-runner agent to run the tests
</commentary>
assistant: Uses the Agent tool to launch the test-runner agent
</example>

<example>
user: "Hello"
<commentary>
Since the user is greeting, use the greeting-responder agent to respond with a friendly joke
</commentary>
assistant: "I'm going to use the Agent tool to launch the greeting-responder agent"
</example>
"""


WRITING_THE_PROMPT_SECTION = """

## Writing the prompt

Brief the agent like a smart colleague who just walked into the room — it hasn't seen this conversation, doesn't know what you've tried, doesn't understand why this task matters.
- Explain what you're trying to accomplish and why.
- Describe what you've already learned or ruled out.
- Give enough context about the surrounding problem that the agent can make judgment calls rather than just following a narrow instruction.
- If you need a short response, say so ("report in under 200 words").
- Lookups: hand over the exact command. Investigations: hand over the question — prescribed steps become dead weight when the premise is wrong.

Terse command-style prompts produce shallow, generic work.

**Never delegate understanding.** Don't write "based on your findings, fix the bug" or "based on the research, implement it." Those phrases push synthesis onto the agent instead of doing it yourself. Write prompts that prove you understood: include file paths, line numbers, what specifically to change.
"""


WHEN_NOT_TO_USE_SECTION = """
When NOT to use the Agent tool:
- If you want to read a specific file path, use the Read tool or the Glob tool instead of the Agent tool, to find the match more quickly
- If you are searching for a specific class definition like "class Foo", use the Glob tool instead, to find the match more quickly
- If you are searching for code within a specific file or set of 2-3 files, use the Read tool instead of the Agent tool, to find the match more quickly
- Other tasks that are not related to the agent descriptions above
"""


def build_shared(agent_list_section: str) -> str:
    """Mirrors the `shared` template."""
    return f"""Launch a new agent to handle complex, multi-step tasks autonomously.

The Agent tool launches specialized agents (subprocesses) that autonomously handle complex tasks. Each agent type has specific capabilities and tools available to it.

{agent_list_section}

When using the Agent tool, specify a subagent_type parameter to select which agent type to use. If omitted, the general-purpose agent is used."""


CONCURRENCY_NOTE = "\n- Launch multiple agents concurrently whenever possible, to maximize performance; to do that, use a single message with multiple tool uses"

BACKGROUND_NOTE = """
- You can optionally run agents in the background using the run_in_background parameter. When an agent runs in the background, you will be automatically notified when it completes — do NOT sleep, poll, or proactively check on its progress. Continue with other work or respond to the user instead.
- **Foreground vs background**: Use foreground (default) when you need the agent's results before you can proceed — e.g., research agents whose findings inform your next steps. Use background when you have genuinely independent work to do in parallel."""


def render_agent_prompt(agent_definitions: list[AgentDefinition]) -> str:
    """Mirrors the getPrompt — non-coordinator, non-fork variant."""
    agent_lines = [format_agent_line(a) for a in agent_definitions]
    agent_list_section = (
        "Available agent types and the tools they have access to:\n"
        + "\n".join(agent_lines)
    )

    shared = build_shared(agent_list_section)

    return f"""{shared}
{WHEN_NOT_TO_USE_SECTION}

Usage notes:
- Always include a short description summarizing what the agent will do{CONCURRENCY_NOTE}
- When the agent is done, it will return a single message back to you. The result returned by the agent is not visible to the user. To show the user the result, you should send a text message back to the user with a concise summary of the result.
- Trust but verify: an agent's summary describes what it intended to do, not necessarily what it did. When an agent writes or edits code, check the actual changes before reporting the work as done.{BACKGROUND_NOTE}
- **Foreground vs background**: Use foreground (default) when you need the agent's results before you can proceed — e.g., research agents whose findings inform your next steps. Use background when you have genuinely independent work to do in parallel.
- To continue a previously spawned agent, use SendMessage with the agent's ID or name as the `to` field — that resumes it with full context. A new Agent call starts a fresh agent with no memory of prior runs, so the prompt must be self-contained.
- Clearly tell the agent whether you expect it to write code or just to do research (search, file reads, web fetches, etc.), since it is not aware of the user's intent
- If the agent description mentions that it should be used proactively, then you should try your best to use it without the user having to ask for it first.
- If the user specifies that they want you to run agents "in parallel", you MUST send a single message with multiple Agent tool use content blocks. For example, if you need to launch both a build-validator agent and a test-runner agent in parallel, send a single message with both tool calls.
- With `isolation: "worktree"`, the worktree is automatically cleaned up if the agent makes no changes; otherwise the path and branch are returned in the result.{WRITING_THE_PROMPT_SECTION}

{CURRENT_EXAMPLES}"""


def _build_static_prompt() -> str:
    from tools.builtin.AgentTool.built_in import BUILT_IN_AGENTS
    return render_agent_prompt(list(BUILT_IN_AGENTS.values()))


AGENT_PROMPT = _build_static_prompt()
