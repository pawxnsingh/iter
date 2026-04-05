"""
Regression tests for bugs found during post-implementation review.

Each test locks down a specific misbehavior:
  1. Typing "/" must NOT leak the character into the prompt text area.
  2. MainScreen.forward_* must re-post a fresh Message, not reuse the
     already-stopped original.
  3. _run_slash must not hang when the agent never becomes ready.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")


async def _wait_for_agent(conv, pilot, timeout: float = 3.0) -> None:
    steps = int(timeout / 0.05)
    for _ in range(steps):
        if conv._agent is not None:
            return
        await pilot.pause(0.05)


# ─── Bug #1: '/' leaks into TextArea ───────────────────────────────────────


@pytest.mark.asyncio
async def test_slash_key_does_not_leak_into_prompt() -> None:
    """
    When the user types '/' on an empty prompt, the overlay should open AND
    the prompt text area must stay empty — otherwise the stale '/' lingers
    after a command is chosen.
    """
    from config.config import Config
    from ui.app import PieAgentApp
    from ui.widgets.prompt import Prompt, PromptTextArea

    app = PieAgentApp(Config(cwd=ROOT))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        ta = app.query_one(PromptTextArea)
        ta.focus()
        await pilot.pause()
        assert ta.text == ""

        await pilot.press("/")
        await pilot.pause()
        assert ta.text == "", (
            f"'/' leaked into prompt after opening overlay: {ta.text!r}"
        )
        # And the overlay should be visible
        assert app.query_one(Prompt).has_class("-show-slash-complete")


@pytest.mark.asyncio
async def test_slash_key_ignored_when_prompt_not_empty() -> None:
    """
    If the user already has text in the prompt and types '/', the overlay
    must NOT open — '/' is just a regular character in that context.
    """
    from config.config import Config
    from ui.app import PieAgentApp
    from ui.widgets.prompt import Prompt, PromptTextArea

    app = PieAgentApp(Config(cwd=ROOT))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        ta = app.query_one(PromptTextArea)
        ta.focus()
        await pilot.pause()
        ta.text = "hello"
        await pilot.pause()

        await pilot.press("/")
        await pilot.pause()
        # Overlay must not have opened
        assert not app.query_one(Prompt).has_class("-show-slash-complete")
        # Text area should now contain the '/' normally
        assert "/" in ta.text


# ─── Bug #2: re-posting Messages ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_main_screen_forwards_user_input_to_conversation() -> None:
    """
    UserInputSubmitted bubbles from Prompt → MainScreen → forwarded to
    Conversation as a fresh message. Behavioral check: a UserInput bubble
    ends up mounted with the right body.
    """
    from config.config import Config
    from ui.app import PieAgentApp
    from ui.messages import UserInputSubmitted
    from ui.widgets.conversation import Conversation
    from ui.widgets.user_input import UserInput

    app = PieAgentApp(Config(cwd=ROOT))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        conv = app.query_one(Conversation)
        await _wait_for_agent(conv, pilot)

        # Post to the Screen as Prompt would, bubbling up. MainScreen's
        # forward handler should rehydrate a new message and post it to
        # the Conversation.
        app.screen.post_message(UserInputSubmitted(body="hello there", shell=False))
        for _ in range(30):
            await pilot.pause(0.05)
            if list(app.query(UserInput)):
                break
        bubbles = list(app.query(UserInput))
        assert bubbles, "no UserInput bubble mounted after forwarded submission"
        assert any("hello there" in getattr(b, "content", "") for b in bubbles)


@pytest.mark.asyncio
async def test_main_screen_forwards_slash_chosen_to_conversation() -> None:
    """SlashCommandChosen must also be forwarded, not just UserInputSubmitted."""
    from config.config import Config
    from ui.app import PieAgentApp
    from ui.messages import SlashCommandChosen
    from ui.widgets.conversation import Conversation, Note

    def _text(w) -> str:
        blob = getattr(w, "_content", None)
        return str(blob) if blob is not None else str(w.render())

    app = PieAgentApp(Config(cwd=ROOT))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        conv = app.query_one(Conversation)
        await _wait_for_agent(conv, pilot)

        app.screen.post_message(SlashCommandChosen(command="/version"))
        for _ in range(40):
            await pilot.pause(0.05)
            if any("v0.1.0" in _text(n) for n in conv.query(Note)):
                break
        blobs = [_text(n) for n in conv.query(Note)]
        assert any("v0.1.0" in b for b in blobs), (
            f"/version result never surfaced via forwarding: {blobs}"
        )


# ─── Bug #3: hang when agent fails to start ────────────────────────────────


@pytest.mark.asyncio
async def test_slash_command_does_not_hang_when_agent_absent() -> None:
    """
    If _agent is never set (startup failed), a slash command should surface
    an error Note, not hang forever on the _agent_ready event.
    """
    from config.config import Config
    from ui.app import PieAgentApp
    from ui.messages import UserInputSubmitted
    from ui.widgets.conversation import Conversation, Note

    def _text(w) -> str:
        blob = getattr(w, "_content", None)
        return str(blob) if blob is not None else str(w.render())

    app = PieAgentApp(Config(cwd=ROOT))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        conv = app.query_one(Conversation)

        # Force the "agent never became ready" scenario: mark the event
        # as set (so we don't wait), but leave _agent as None.
        conv._agent = None
        conv._agent_ready.set()

        conv.post_message(UserInputSubmitted(body="/help"))

        found_error = False
        for _ in range(40):
            await pilot.pause(0.05)
            for n in conv.query(Note):
                if "not available" in _text(n).lower():
                    found_error = True
                    break
            if found_error:
                break
        assert found_error, (
            "Expected an 'Agent is not available' Note when _agent is None, "
            f"got {[_text(n) for n in conv.query(Note)]!r}"
        )
