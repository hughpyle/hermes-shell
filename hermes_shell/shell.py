from __future__ import annotations

import base64
import builtins
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import textwrap
import unicodedata
from dataclasses import dataclass
from typing import Iterable

_PACKAGE_DIR = Path(__file__).resolve().parent
_SYSTEM_PROMPT_TEMPLATE = (_PACKAGE_DIR / "system_prompt.txt").read_text()
_WRITE_CHUNK = 256
# Translation table: keep ASCII + BEL, delete everything else
_ASCII_TABLE = {i: None for i in range(128, 0x110000)}
_ASCII_TABLE[0x07] = 0x07  # preserve BEL

input = builtins.input


def flush_tty() -> None:
    """Discard pending tty output (kernel buffer). Best-effort."""
    try:
        import termios
        termios.tcflush(sys.stdout.fileno(), termios.TCOFLUSH)
    except (ImportError, OSError):
        pass


def _interrupted() -> None:
    flush_tty()
    sys.stdout.write("\n")
    sys.stdout.flush()


BINARY_START = "<<BINARY>>"
FILE_START = "<<FILE>>"
SEGMENT_END = "<<END>>"
_MARKERS = {BINARY_START: "binary", FILE_START: "file"}


@dataclass(frozen=True)
class TerminalProfile:
    term: str = "dumb"
    columns: int = 72
    lines: int = 24


def detect_terminal_profile() -> TerminalProfile:
    term = os.getenv("TERM", "dumb") or "dumb"
    fallback = shutil.get_terminal_size((72, 24))
    columns = _env_int("COLUMNS") or fallback.columns
    lines = _env_int("LINES") or fallback.lines
    return TerminalProfile(term=term, columns=columns, lines=lines)


def _env_int(name: str) -> int | None:
    value = os.getenv(name)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def build_system_prompt(profile: TerminalProfile) -> str:
    return _SYSTEM_PROMPT_TEMPLATE.format(term=profile.term, columns=profile.columns)


def parse_hermes_output(stdout: str) -> tuple[str, str | None]:
    text = stdout.rstrip()
    match = re.search(r"(?:^|\n)session_id:\s*(\S+)\s*$", text)
    session_id = match.group(1) if match else None
    if match:
        text = text[: match.start()].rstrip()
    return text, session_id


def run_turn(
    prompt: str,
    session_id: str | None,
    hermes_bin: str,
    profile: TerminalProfile,
    max_turns: int,
    model: str | None = None,
    provider: str | None = None,
    toolsets: str | None = None,
    skills: Iterable[str] | None = None,
) -> tuple[str, str | None]:
    cmd = [hermes_bin, "chat", "-Q"]
    if session_id:
        cmd.extend(["--resume", session_id])
    if max_turns is not None:
        cmd.extend(["--max-turns", str(max_turns)])
    if model:
        cmd.extend(["--model", model])
    if provider:
        cmd.extend(["--provider", provider])
    if toolsets:
        cmd.extend(["--toolsets", toolsets])
    if skills:
        for skill in skills:
            cmd.extend(["--skills", skill])
    cmd.extend(["-q", prompt])

    env = os.environ.copy()
    env["TERM"] = profile.term
    env["COLUMNS"] = str(profile.columns)
    env["LINES"] = str(profile.lines)
    env["NO_COLOR"] = "1"
    env["HERMES_EPHEMERAL_SYSTEM_PROMPT"] = build_system_prompt(profile)

    result = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        detail = stderr or stdout or f"hermes exited with status {result.returncode}"
        raise RuntimeError(detail)
    return parse_hermes_output(result.stdout)


def ascii_sanitize(text: str) -> str:
    return unicodedata.normalize("NFKD", text).translate(_ASCII_TABLE)


def wrap_text(text: str, width: int) -> str:
    width = max(8, width)
    out_lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.rstrip()
        if len(stripped) <= width:
            out_lines.append(stripped)
            continue
        indent = stripped[: len(stripped) - len(stripped.lstrip())]
        body = stripped.lstrip()
        usable = width - len(indent)
        if usable < 8:
            usable = width
            indent = ""
        wrapped = textwrap.fill(
            body, width=usable, break_long_words=True, break_on_hyphens=False,
            initial_indent=indent, subsequent_indent=indent,
        )
        out_lines.append(wrapped)
    while out_lines and out_lines[-1] == "":
        out_lines.pop()
    return "\n".join(out_lines)


def parse_segments(text: str) -> list[tuple[str, str]]:
    """Split text into ('text', ...), ('binary', base64), and ('file', path) segments."""
    segments: list[tuple[str, str]] = []
    rest = text
    while rest:
        best_pos = len(rest)
        best_marker = None
        best_kind = None
        for marker, kind in _MARKERS.items():
            pos = rest.find(marker)
            if pos != -1 and pos < best_pos:
                best_pos = pos
                best_marker = marker
                best_kind = kind
        if best_kind is None:
            segments.append(("text", rest))
            break
        if best_pos > 0:
            segments.append(("text", rest[:best_pos]))
        rest = rest[best_pos + len(best_marker) :]
        end = rest.find(SEGMENT_END)
        if end == -1:
            segments.append((best_kind, rest))
            break
        segments.append((best_kind, rest[:end]))
        rest = rest[end + len(SEGMENT_END) :]
    return segments


def emit_text(text: str, width: int = 72, out=None) -> None:
    out = out or sys.stdout
    prepared = wrap_text(ascii_sanitize(text), width)
    out.write(prepared)
    out.write("\n")
    out.flush()


def _write_raw(data: bytes, out=None) -> None:
    buf = getattr(out, "buffer", None) if out else None
    buf = buf or sys.stdout.buffer
    for offset in range(0, len(data), _WRITE_CHUNK):
        buf.write(data[offset : offset + _WRITE_CHUNK])
        buf.flush()


def emit_file(path: str, out=None) -> None:
    """Write a file's raw bytes to stdout, preserving CR overstrike and all content."""
    try:
        data = Path(path).read_bytes()
    except Exception as exc:
        text_out = out or sys.stdout
        msg = str(exc).split("\n", 1)[0]
        text_out.write(f"error: {msg}\n")
        text_out.flush()
        return
    _write_raw(data, out=out)


def emit_output(text: str, width: int = 72, out=None) -> None:
    out = out or sys.stdout
    for kind, content in parse_segments(text):
        if kind == "text":
            stripped = content.strip()
            if stripped:
                emit_text(stripped, width=width, out=out)
        elif kind == "file":
            emit_file(content.strip(), out=out)
        elif kind == "binary":
            try:
                raw = base64.b64decode(content.strip())
            except Exception:
                out.write("error: invalid binary data\n")
                out.flush()
                continue
            _write_raw(raw, out=out)


def run_shell_loop(
    hermes_bin: str = "hermes",
    profile: TerminalProfile | None = None,
    max_turns: int = 90,
    model: str | None = None,
    provider: str | None = None,
    toolsets: str | None = None,
    skills: Iterable[str] | None = None,
) -> int:
    profile = profile or detect_terminal_profile()
    session_id = None

    signal.signal(signal.SIGHUP, lambda _sig, _frame: sys.exit(0))

    while True:
        try:
            line = input("> ")
        except EOFError:
            return 0
        except KeyboardInterrupt:
            _interrupted()
            continue

        prompt = line.strip()
        if not prompt:
            continue
        if prompt in {".exit", ".quit"}:
            return 0
        if prompt in {".reset", ".new"}:
            session_id = None
            continue

        try:
            if prompt.startswith(".print "):
                emit_file(prompt[7:].strip())
                continue

            response, new_sid = run_turn(
                prompt=prompt,
                session_id=session_id,
                hermes_bin=hermes_bin,
                profile=profile,
                max_turns=max_turns,
                model=model,
                provider=provider,
                toolsets=toolsets,
                skills=skills,
            )
        except KeyboardInterrupt:
            _interrupted()
            continue
        except Exception as exc:
            msg = str(exc).split("\n", 1)[0][:profile.columns]
            sys.stdout.write(f"error: {msg}\n")
            sys.stdout.flush()
            continue

        try:
            session_id = new_sid
            emit_output(response, width=profile.columns)
        except KeyboardInterrupt:
            _interrupted()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Minimal teletype-safe shell wrapper for Hermes Agent")
    parser.add_argument("--hermes-bin", default=os.getenv("HERMES_BIN", "hermes"))
    parser.add_argument("--columns", type=int, default=None)
    parser.add_argument("--lines", type=int, default=None)
    parser.add_argument("--term", default=None)
    parser.add_argument("--max-turns", type=int, default=90)
    parser.add_argument("--model", default=None)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--toolsets", default=None)
    parser.add_argument("--skills", action="append", default=None)
    args = parser.parse_args(argv)

    detected = detect_terminal_profile()
    profile = TerminalProfile(
        term=args.term or detected.term,
        columns=args.columns or detected.columns,
        lines=args.lines or detected.lines,
    )
    return run_shell_loop(
        hermes_bin=args.hermes_bin,
        profile=profile,
        max_turns=args.max_turns,
        model=args.model,
        provider=args.provider,
        toolsets=args.toolsets,
        skills=args.skills,
    )


if __name__ == "__main__":
    raise SystemExit(main())
