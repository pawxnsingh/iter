"""
End-to-end smoke test for the Textual UI.

Goals:
  1. All ui/* modules import without error.
  2. DEFAULT_CSS on every widget parses (Textual does this at class creation).
  3. NewtApp mounts MainScreen with Conversation + Prompt + Footer.
  4. The slash-complete overlay opens on "/" and closes on Escape.

Does NOT exercise the real Agent (no API calls) — Conversation's agent
worker starts but will fail silently at __aenter__ if no provider key is
set; we just verify the UI tree renders before that matters.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Add project root to path so `ui`, `config`, etc. import cleanly
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_imports() -> None:
    """All new UI modules must import cleanly — this also parses DEFAULT_CSS."""
    import ui.app  # noqa: F401
    import ui.messages  # noqa: F401
    import ui.screens.main  # noqa: F401
    import ui.screens.permissions  # noqa: F401
    import ui.widgets.agent_response  # noqa: F401
    import ui.widgets.conversation  # noqa: F401
    import ui.widgets.flash  # noqa: F401
    import ui.widgets.pill  # noqa: F401
    import ui.widgets.prompt  # noqa: F401
    import ui.widgets.slash_complete  # noqa: F401
    import ui.widgets.throbber  # noqa: F401
    import ui.widgets.tool_call  # noqa: F401
    import ui.widgets.user_input  # noqa: F401


@pytest.mark.asyncio
async def test_app_mounts() -> None:
    """App must mount without errors and expose core widgets."""
    os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")  # bypass Config.validate
    from config.config import Config
    from ui.app import NewtApp
    from ui.widgets.conversation import Conversation
    from ui.widgets.flash import Flash
    from ui.widgets.prompt import Prompt, PromptTextArea
    from ui.widgets.throbber import Throbber

    config = Config(cwd=ROOT)
    app = NewtApp(config)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert app.query_one(Conversation)
        assert app.query_one(Prompt)
        assert app.query_one(PromptTextArea)
        assert app.query_one(Throbber)
        assert app.query_one(Flash)


@pytest.mark.asyncio
async def test_slash_overlay_opens_on_slash() -> None:
    """Typing '/' at an empty prompt should reveal the slash overlay."""
    os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")
    from config.config import Config
    from ui.app import NewtApp
    from ui.widgets.prompt import Prompt, PromptTextArea
    from ui.widgets.slash_complete import SlashComplete

    config = Config(cwd=ROOT)
    app = NewtApp(config)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        prompt = app.query_one(Prompt)
        ta = app.query_one(PromptTextArea)
        ta.focus()
        await pilot.pause()

        # Sanity — overlay exists in the tree and starts hidden
        assert app.query_one(SlashComplete) is not None
        assert not prompt.has_class("-show-slash-complete")

        await pilot.press("/")
        await pilot.pause()
        # Prompt should now carry the reveal class
        assert prompt.has_class("-show-slash-complete"), (
            "slash overlay class not toggled after typing '/'"
        )

        await pilot.press("escape")
        await pilot.pause()
        assert not prompt.has_class("-show-slash-complete"), (
            "slash overlay did not close on Escape"
        )


@pytest.mark.asyncio
async def test_slash_command_executes_help() -> None:
    """Submitting '/help' should render a Note with the help text."""
    os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")
    from config.config import Config
    from ui.app import NewtApp
    from ui.widgets.conversation import Conversation, Note

    config = Config(cwd=ROOT)
    app = NewtApp(config)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        conv = app.query_one(Conversation)
        # Wait until the agent worker has initialised the session — slash
        # commands need conv._agent to be set before they can run.
        for _ in range(50):
            if conv._agent is not None:
                break
            await pilot.pause(0.05)

        # Submit the help command directly via the message bus
        from ui.messages import UserInputSubmitted
        conv.post_message(UserInputSubmitted(body="/help"))

        # Give the message loop time to mount the result Note
        for _ in range(40):
            await pilot.pause(0.05)
            if list(conv.query(Note)):
                break

        notes = list(conv.query(Note))
        assert notes, "no Note was mounted after /help"
        # Static widgets expose their content via the `_content` attr in
        # current Textual; fall back to `.render()` if that ever changes.
        text_blobs: list[str] = []
        for n in notes:
            blob = getattr(n, "_content", None)
            text_blobs.append(str(blob) if blob is not None else str(n.render()))
        assert any("Available commands" in t for t in text_blobs), (
            f"help output not found in notes: {text_blobs!r}"
        )
