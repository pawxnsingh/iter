"""Tests for session storage."""
import tempfile


from session.storage import SessionMetadata, SessionStorage


def test_append_and_load():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SessionStorage("test-session", tmpdir)
        storage.append(role="user", content="hello")
        storage.append(role="assistant", content="hi there")
        storage.append(role="tool", content="file content", tool_call_id="tc1")

        messages = SessionStorage.load_transcript(storage.path)
        assert len(messages) == 3
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "hello"
        assert messages[1]["role"] == "assistant"
        assert messages[2]["tool_call_id"] == "tc1"


def test_save_metadata():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SessionStorage("test-session", tmpdir)
        meta = SessionMetadata(
            session_id="test-session",
            project_dir=tmpdir,
            cwd=tmpdir,
            model="gpt-4",
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
            turn_count=5,
            message_count=15,
        )
        storage.save_metadata(meta)
        assert storage.meta_path.exists()


def test_list_sessions():
    with tempfile.TemporaryDirectory() as tmpdir:
        s1 = SessionStorage("session-1", tmpdir)
        s1.append(role="user", content="hello")
        s1.save_metadata(SessionMetadata(
            session_id="session-1", project_dir=tmpdir, cwd=tmpdir,
            model="gpt-4", created_at="2024-01-01", updated_at="2024-01-01",
        ))

        s2 = SessionStorage("session-2", tmpdir)
        s2.append(role="user", content="world")
        s2.save_metadata(SessionMetadata(
            session_id="session-2", project_dir=tmpdir, cwd=tmpdir,
            model="gpt-4", created_at="2024-01-02", updated_at="2024-01-02",
        ))

        sessions = SessionStorage.list_sessions(tmpdir)
        assert len(sessions) == 2
