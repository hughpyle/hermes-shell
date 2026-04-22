import base64
import io
import os
from types import SimpleNamespace

import pytest

from hermes_shell import shell


class DualOut:
    """Text-mode file object whose .buffer captures raw bytes."""

    def __init__(self):
        self.buffer = io.BytesIO()

    def write(self, s):
        self.buffer.write(s.encode("ascii", "replace"))

    def flush(self):
        self.buffer.flush()

    @property
    def raw(self):
        return self.buffer.getvalue()


def test_detect_terminal_profile_prefers_environment(monkeypatch):
    monkeypatch.setenv("TERM", "tty33")
    monkeypatch.setenv("COLUMNS", "72")
    monkeypatch.setenv("LINES", "24")

    profile = shell.detect_terminal_profile()

    assert profile.term == "tty33"
    assert profile.columns == 72
    assert profile.lines == 24


def test_build_system_prompt_includes_teletype_constraints():
    profile = shell.TerminalProfile(term="tty33", columns=72, lines=24)

    prompt = shell.build_system_prompt(profile)

    assert "ASCII" in prompt
    assert "72" in prompt


def test_parse_hermes_output_extracts_response_and_session_id():
    response, session_id = shell.parse_hermes_output("HELLO\n\nsession_id: abc123\n")

    assert response == "HELLO"
    assert session_id == "abc123"


def test_run_turn_invokes_hermes_with_resume_and_prompt(monkeypatch):
    calls = {}

    def fake_run(cmd, capture_output, text, env, check):
        calls["cmd"] = cmd
        calls["env"] = env
        return SimpleNamespace(stdout="OK\n\nsession_id: s1\n", returncode=0)

    monkeypatch.setattr(shell.subprocess, "run", fake_run)

    profile = shell.TerminalProfile(term="tty33", columns=72, lines=24)
    response, session_id = shell.run_turn(
        prompt="status",
        session_id="oldsession",
        hermes_bin="hermes",
        profile=profile,
        max_turns=7,
    )

    assert response == "OK"
    assert session_id == "s1"
    assert calls["cmd"][:4] == ["hermes", "chat", "-Q", "--resume"]
    assert "oldsession" in calls["cmd"]
    assert calls["env"]["HERMES_EPHEMERAL_SYSTEM_PROMPT"]


def test_shell_loop_resets_session_with_local_command(monkeypatch, capsys):
    prompts = iter(["hello", ".reset", "again", ".exit"])
    seen_sessions = []

    def fake_input(_prompt):
        return next(prompts)

    def fake_run_turn(prompt, session_id, hermes_bin, profile, max_turns, model=None, provider=None, toolsets=None, skills=None):
        seen_sessions.append(session_id)
        if prompt == "hello":
            return ("FIRST", "s1")
        if prompt == "again":
            return ("SECOND", "s2")
        raise AssertionError(prompt)

    monkeypatch.setattr(shell, "run_turn", fake_run_turn)
    monkeypatch.setattr(shell, "emit_output", lambda text, **kwargs: print(text))
    monkeypatch.setattr(shell, "input", fake_input)

    shell.run_shell_loop(hermes_bin="hermes", profile=shell.TerminalProfile(), max_turns=5)

    out = capsys.readouterr().out
    assert "FIRST" in out
    assert "SECOND" in out
    assert seen_sessions == [None, None]


def test_emit_text_wraps_and_ascii_sanitizes(capsys):
    shell.emit_text("caf\u00e9 " + ("X" * 80), width=10)

    out = capsys.readouterr().out
    assert "cafe" in out
    for line in out.splitlines():
        assert len(line) <= 10


def test_wrap_text_preserves_leading_whitespace():
    text = "hello\n    indented line\n        deeper"
    result = shell.wrap_text(text, width=40)
    lines = result.splitlines()
    assert lines[0] == "hello"
    assert lines[1].startswith("    ")
    assert lines[2].startswith("        ")


def test_wrap_text_preserves_short_lines_verbatim():
    art = " /\\_/\\\n( o.o )\n > ^ <"
    result = shell.wrap_text(art, width=72)
    assert result == art


def test_wrap_text_preserves_blank_lines():
    text = "line one\n\nline three"
    result = shell.wrap_text(text, width=72)
    assert result == "line one\n\nline three"


def test_ascii_sanitize_preserves_bel():
    assert shell.ascii_sanitize("hello\x07world") == "hello\x07world"


def test_ascii_sanitize_strips_non_ascii():
    assert shell.ascii_sanitize("\u2603") == ""


def test_parse_segments_text_only():
    segs = shell.parse_segments("just plain text")
    assert segs == [("text", "just plain text")]


def test_parse_segments_mixed():
    encoded = base64.b64encode(b"\x80\x81\x82").decode()
    text = f"hello\n<<BINARY>>\n{encoded}\n<<END>>\ngoodbye"
    segs = shell.parse_segments(text)
    assert len(segs) == 3
    assert segs[0] == ("text", "hello\n")
    assert segs[1][0] == "binary"
    assert segs[2] == ("text", "\ngoodbye")


def test_parse_segments_unterminated_binary():
    segs = shell.parse_segments("start<<BINARY>>leftover")
    assert segs == [("text", "start"), ("binary", "leftover")]


def test_emit_output_text_only(capsys):
    shell.emit_output("hello world", width=72)
    assert "hello world" in capsys.readouterr().out


def test_emit_output_binary_decodes_and_writes_raw():
    encoded = base64.b64encode(b"\x00\xff\x80").decode()
    text = f"before\n<<BINARY>>\n{encoded}\n<<END>>\nafter"

    out = DualOut()
    shell.emit_output(text, width=72, out=out)

    assert b"\x00\xff\x80" in out.raw
    assert b"before" in out.raw
    assert b"after" in out.raw


def test_emit_output_binary_invalid_base64(capsys):
    text = "<<BINARY>>not-valid-base64!!!<<END>>"
    shell.emit_output(text, width=72)
    out = capsys.readouterr().out
    assert "error: invalid binary data" in out


def test_build_system_prompt_includes_binary_instructions():
    profile = shell.TerminalProfile()
    prompt = shell.build_system_prompt(profile)
    assert "<<BINARY>>" in prompt
    assert "<<END>>" in prompt
    assert "binary" in prompt.lower()


def test_build_system_prompt_includes_file_instructions():
    profile = shell.TerminalProfile()
    prompt = shell.build_system_prompt(profile)
    assert "<<FILE>>" in prompt


def test_parse_segments_file_marker():
    text = "here it is:\n<<FILE>>\n/tmp/art.txt\n<<END>>\ndone"
    segs = shell.parse_segments(text)
    assert len(segs) == 3
    assert segs[0] == ("text", "here it is:\n")
    assert segs[1] == ("file", "\n/tmp/art.txt\n")
    assert segs[2] == ("text", "\ndone")


def test_parse_segments_mixed_binary_and_file():
    encoded = base64.b64encode(b"\xff").decode()
    text = f"<<FILE>>/a.txt<<END>>middle<<BINARY>>{encoded}<<END>>"
    segs = shell.parse_segments(text)
    assert segs[0] == ("file", "/a.txt")
    assert segs[1] == ("text", "middle")
    assert segs[2][0] == "binary"


def test_emit_file_writes_raw_bytes(tmp_path):
    f = tmp_path / "overstrike.txt"
    f.write_bytes(b"HELLO\rWORLD\n")

    out = DualOut()
    shell.emit_file(str(f), out=out)
    assert out.raw == b"HELLO\rWORLD\n"


def test_emit_file_missing_file(capsys):
    shell.emit_file("/nonexistent/path/xyz.txt")
    out = capsys.readouterr().out
    assert "error:" in out


def test_emit_output_file_segment(tmp_path):
    f = tmp_path / "test.txt"
    f.write_bytes(b"RAW\rCONTENT")

    out = DualOut()
    text = f"intro\n<<FILE>>\n{f}\n<<END>>\noutro"
    shell.emit_output(text, width=72, out=out)
    assert b"RAW\rCONTENT" in out.raw
    assert b"intro" in out.raw
    assert b"outro" in out.raw


def test_shell_loop_print_command(monkeypatch, tmp_path, capsys):
    f = tmp_path / "hello.txt"
    f.write_bytes(b"PRINTED\n")

    prompts = iter([f".print {f}", ".exit"])

    def fake_input(_prompt):
        return next(prompts)

    monkeypatch.setattr(shell, "input", fake_input)

    shell.run_shell_loop(hermes_bin="hermes", profile=shell.TerminalProfile(), max_turns=5)

    out = capsys.readouterr().out
    assert "error" not in out
    assert "PRINTED" in out


def test_ctrl_c_during_output_returns_to_prompt(monkeypatch, capsys):
    call_count = 0

    def fake_input(_prompt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "hello"
        if call_count == 2:
            return ".exit"
        raise AssertionError("too many prompts")

    def fake_run_turn(prompt, session_id, hermes_bin, profile, max_turns, **kw):
        return ("long response", "s1")

    def fake_emit_output(text, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(shell, "input", fake_input)
    monkeypatch.setattr(shell, "run_turn", fake_run_turn)
    monkeypatch.setattr(shell, "emit_output", fake_emit_output)
    monkeypatch.setattr(shell, "flush_tty", lambda: None)

    code = shell.run_shell_loop(hermes_bin="hermes", profile=shell.TerminalProfile(), max_turns=5)
    assert code == 0


def test_ctrl_c_at_prompt_continues(monkeypatch, capsys):
    call_count = 0

    def fake_input(_prompt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise KeyboardInterrupt
        if call_count == 2:
            return ".exit"
        raise AssertionError("too many prompts")

    monkeypatch.setattr(shell, "input", fake_input)
    monkeypatch.setattr(shell, "flush_tty", lambda: None)

    code = shell.run_shell_loop(hermes_bin="hermes", profile=shell.TerminalProfile(), max_turns=5)
    assert code == 0


def test_shell_loop_catches_hermes_error(monkeypatch, capsys):
    prompts = iter(["boom", ".exit"])

    def fake_input(_prompt):
        return next(prompts)

    def fake_run_turn(prompt, session_id, hermes_bin, profile, max_turns, model=None, provider=None, toolsets=None, skills=None):
        raise RuntimeError("hermes exploded with a very long traceback")

    monkeypatch.setattr(shell, "run_turn", fake_run_turn)
    monkeypatch.setattr(shell, "input", fake_input)

    code = shell.run_shell_loop(hermes_bin="hermes", profile=shell.TerminalProfile(), max_turns=5)

    assert code == 0
    out = capsys.readouterr().out
    assert "error:" in out
    assert "hermes exploded" in out
    # no traceback — just one line
    error_lines = [l for l in out.splitlines() if "error:" in l]
    assert len(error_lines) == 1


def test_run_turn_gateway_sends_correct_request(monkeypatch):
    import json
    import urllib.request
    captured = {}

    class FakeResponse:
        def __init__(self):
            self.data = json.dumps({
                "choices": [{"message": {"content": "gateway says hello"}}]
            }).encode()
        def read(self):
            return self.data
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.headers)
        captured["body"] = json.loads(req.data)
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    profile = shell.TerminalProfile(columns=72)
    response, sid = shell.run_turn_gateway(
        prompt="hello",
        session_id="test-session-123",
        gateway_url="http://localhost:8642",
        profile=profile,
    )

    assert response == "gateway says hello"
    assert sid == "test-session-123"
    assert captured["url"] == "http://localhost:8642/v1/chat/completions"
    assert captured["headers"]["X-hermes-session-id"] == "test-session-123"
    assert captured["body"]["messages"][0]["role"] == "system"
    assert captured["body"]["messages"][1] == {"role": "user", "content": "hello"}


def test_run_turn_gateway_generates_session_id_when_none(monkeypatch):
    import json
    import urllib.request

    class FakeResponse:
        def read(self):
            return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, **kw: FakeResponse())

    _, sid = shell.run_turn_gateway(
        prompt="hi",
        session_id=None,
        gateway_url="http://localhost:8642",
        profile=shell.TerminalProfile(),
    )

    assert sid is not None
    assert len(sid) == 32  # uuid hex


def test_run_turn_gateway_http_error(monkeypatch):
    import urllib.error
    import urllib.request

    def fake_urlopen(req, **kw):
        raise urllib.error.HTTPError(
            req.full_url, 500, "Internal Server Error",
            {}, io.BytesIO(b"something broke"),
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="something broke"):
        shell.run_turn_gateway(
            prompt="hi",
            session_id="s1",
            gateway_url="http://localhost:8642",
            profile=shell.TerminalProfile(),
        )


def test_run_turn_gateway_unreachable(monkeypatch):
    import urllib.error
    import urllib.request

    def fake_urlopen(req, **kw):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="gateway unreachable"):
        shell.run_turn_gateway(
            prompt="hi",
            session_id="s1",
            gateway_url="http://localhost:8642",
            profile=shell.TerminalProfile(),
        )


def test_shell_loop_uses_gateway_when_set(monkeypatch, capsys):
    prompts = iter(["hello", ".exit"])

    def fake_input(_prompt):
        return next(prompts)

    def fake_gateway(prompt, session_id, gateway_url, profile, model=None):
        return ("GATEWAY RESPONSE", "gs1")

    monkeypatch.setattr(shell, "input", fake_input)
    monkeypatch.setattr(shell, "run_turn_gateway", fake_gateway)
    monkeypatch.setattr(shell, "emit_output", lambda text, **kw: print(text))

    code = shell.run_shell_loop(
        profile=shell.TerminalProfile(), max_turns=5,
        gateway="http://localhost:8642",
    )

    assert code == 0
    assert "GATEWAY RESPONSE" in capsys.readouterr().out
