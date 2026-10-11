"""FleetView tests — assert-based, run with: python test_fleetview.py
TDD discipline: every test below was observed RED before its implementation
existed (recorded per step in docs/plans/2026-08-08-fleetview.md)."""
import base64
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import fleetview
from http.server import BaseHTTPRequestHandler


class _OkHandler(BaseHTTPRequestHandler):
    """Minimal responder for test_localhost_is_not_slow — the point of that
    test is the connect, not what comes back."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass

from fleetview import parse_transcript, read_tail_text, pid_alive, find_transcript, discover_sessions, rule_advice, Summarizer, GROWTH_BYTES, build_data, RECENT_TTL, PAGE, CONTEXT_LIMIT, snapshot_sessions, host_allowed, send_to_session, context_limit_for, origin_allowed


def test_detail_data():
    """The detail view exists to show MORE than the card — wider caps, and the
    same not-live guard /send has."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.jsonl"
        long_text = "x" * 5000
        p.write_text("\n".join(
            json.dumps({"type": "assistant", "timestamp": "2026-08-08T21:06:01.687Z",
                        "message": {"content": [{"type": "text", "text": f"{i}"}]}})
            for i in range(40)
        ) + "\n" + json.dumps({
            "type": "assistant", "timestamp": "2026-08-08T21:07:00.000Z",
            "message": {"content": [{"type": "text", "text": long_text}]}}) + "\n",
            encoding="utf-8")
        prev = fleetview.STATE["latest"]
        fleetview.STATE["latest"] = ([
            {"sessionId": "s1", "name": "n", "cwd": "C:\\x", "status": "idle",
             "transcript": p},
            {"sessionId": "s2", "name": "n2", "cwd": "C:\\y", "status": "idle"},
        ], {})
        try:
            got = fleetview.detail_data("s1")
            card = parse_transcript(p)          # card caps: 12 events, 300 chars
            assert len(card["events"]) == 12, len(card["events"])
            assert len(got["events"]) == 41, len(got["events"])
            assert len(got["events"][-1]["t"]) == 5000, len(got["events"][-1]["t"])
            assert len(card["events"][-1]["t"]) == 300, len(card["events"][-1]["t"])
            assert got["name"] == "n" and got["status"] == "idle", got
            assert "not live" in fleetview.detail_data("ghost")["error"]
            assert "transcript" in fleetview.detail_data("s2")["error"]
        finally:
            fleetview.STATE["latest"] = prev


def test_child_processes_get_no_console_window():
    """A summariser or send popping up a console window steals focus — from a
    game, a call, anything. Both spawn paths must pass CREATE_NO_WINDOW."""
    if os.name != "nt":
        return
    seen = []

    class _R:
        returncode, stdout, stderr = 0, '{"result": "{}"}', ""

    real = fleetview.subprocess.run
    fleetview.subprocess.run = lambda *a, **k: (seen.append(k.get("creationflags")), _R())[1]
    try:
        fleetview._call_claude("x")
        fleetview._send_keystrokes(1, "x")
    finally:
        fleetview.subprocess.run = real
    assert seen == [subprocess.CREATE_NO_WINDOW] * 2, seen


def test_context_limit_uses_model_table():
    """A real bug: a 1M-window opus session at 150k rendered as 75% of
    200k and pinned the advice chip amber. The model id decides the window."""
    assert context_limit_for(150_000, "claude-opus-5") == 1_000_000
    assert context_limit_for(150_000, "claude-haiku-4-5-20251001") == 200_000
    assert context_limit_for(None, None) == 200_000          # unknown -> default
    assert context_limit_for(400_000, "mystery") == 1_000_000  # never over 100%


class _FakeK32:
    """Enough of kernel32 to watch what type_into_console writes to CONIN$."""
    def __init__(self):
        self.writes, self.attached, self.freed = [], None, 0

    def FreeConsole(self):
        self.freed += 1
        return 1

    def AttachConsole(self, pid):
        self.attached = pid
        return 1

    def CreateFileW(self, *a):
        return 7

    def WriteConsoleInputW(self, h, recs, n, written):
        # key-down and key-up per character, so every other record is the text
        self.writes.append("".join(
            recs[i].Event.KeyEvent.uChar.UnicodeChar for i in range(0, n, 2)))
        return 1


class _FakeConsoleOut:
    """kernel32 stand-in that serves a fixed screen out of CONOUT$."""
    def __init__(self, rows, width=40):
        self.rows, self.width, self.attached = rows, width, None

    def FreeConsole(self):
        return 1

    def AttachConsole(self, pid):
        self.attached = pid
        return 1

    def CreateFileW(self, *a):
        return 7

    def GetConsoleScreenBufferInfo(self, h, ref):
        info = ref._obj
        info.srWindow.Left, info.srWindow.Right = 0, self.width - 1
        info.srWindow.Top, info.srWindow.Bottom = 0, len(self.rows) - 1
        return 1

    def ReadConsoleOutputCharacterW(self, h, buf, width, coord, read_ref):
        text = self.rows[coord.Y][:width].ljust(width)
        buf[0:width] = text
        read_ref._obj.value = width
        return 1


def test_read_console_screen():
    """The mirror is what makes blind keystrokes safe — it has to show the
    picker rows verbatim, and stop at the last real line rather than padding
    the pane with the TUI's empty rows."""
    rows = ["Select a model:", "> 1. Opus", "  2. Sonnet", "", "", ""]
    k = _FakeConsoleOut(rows)
    got, err = fleetview.read_console_screen(777, k32=k)
    assert err == "", err
    assert got == ["Select a model:", "> 1. Opus", "  2. Sonnet"], got
    assert k.attached == 777


def test_read_console_screen_attach_failure():
    class _NoAttach(_FakeConsoleOut):
        def AttachConsole(self, pid):
            return 0

    got, err = fleetview.read_console_screen(5, k32=_NoAttach([]))
    assert got == [] and "cannot attach" in err, (got, err)


def test_detail_data_carries_the_screen():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.jsonl"
        p.write_text(json.dumps({
            "type": "assistant", "timestamp": "2026-08-09T00:00:00.000Z",
            "message": {"content": [{"type": "text", "text": "hi"}]}}) + "\n",
            encoding="utf-8")
        prev, real = fleetview.STATE["latest"], fleetview._read_screen
        fleetview.STATE["latest"] = ([
            {"sessionId": "s1", "name": "n", "pid": 31337, "status": "idle",
             "transcript": p},
            {"sessionId": "s2", "name": "n2", "status": "idle", "transcript": p},
        ], {})
        fleetview._read_screen = lambda pid: (f"screen of {pid}", "")
        try:
            got = fleetview.detail_data("s1")
            assert got["screen"] == "screen of 31337", got
            # a session with no pid must not spawn a child with pid "None"
            assert fleetview.detail_data("s2")["screen"] == ""
            assert "no pid" in fleetview.detail_data("s2")["screen_error"]
        finally:
            fleetview.STATE["latest"] = prev
            fleetview._read_screen = real


def test_type_into_console_sends_text_then_enter():
    """Enter must be its own write after the text — verified live 2026-08-08:
    the message sat unsubmitted in the TUI until a bare CR followed it. Without
    the CR the session is left with a half-typed prompt and no reply, which is
    worse than a refused send."""
    k = _FakeK32()
    assert fleetview.type_into_console(4242, "hello there", k32=k) == ""
    assert k.writes == ["hello there", "\r"], k.writes
    assert k.attached == 4242 and k.freed == 2, (k.attached, k.freed)


def test_typing_payload_is_submittable():
    """A newline inside the message would submit it half-typed, and without the
    trailing Enter nothing is submitted at all — both leave a wedged TUI."""
    seen = {}

    class _R:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(cmd, **k):
        seen["argv"] = cmd
        return _R()

    prev = fleetview.STATE["latest"]
    fleetview.STATE["latest"] = ([{"sessionId": "s1", "pid": 4242,
                                   "status": "busy"}], {})
    def typer(pid, text):
        seen["typed"] = (pid, text)
        return ""

    try:
        ok, err = send_to_session("s1", "line one\nline two   \n", typer=typer)
        assert ok and err == "", (ok, err)
        # busy is NOT a refusal any more. Newlines used to be flattened to
        # spaces because a bare Enter submits; since D36 they are carried as
        # the TUI's backslash continuation instead, so a paragraph arrives as
        # a paragraph. The trailing blank line is still dropped.
        assert seen["typed"] == (4242, "line one\\\nline two"), seen["typed"]
    finally:
        fleetview.STATE["latest"] = prev

    # and the child is handed exactly (--type, pid, text)
    assert fleetview._send_keystrokes(99, "hi", run=fake_run) == ""
    assert seen["argv"][1:] == [str(Path(fleetview.__file__).resolve()),
                                "--type", "99", "hi"], seen["argv"]


def test_send_to_session():
    sent = []
    prev = fleetview.STATE["latest"]
    fleetview.STATE["latest"] = ([
        {"sessionId": "idle-1", "cwd": "C:\\x", "status": "idle"},
        {"sessionId": "busy-1", "cwd": "C:\\y", "status": "busy"},
    ], {})
    try:
        assert send_to_session("idle-1", "hello",
                               typer=lambda p, t: sent.append(1) or "") == (True, "")
        assert sent == [1]
        ok, err = send_to_session("busy-1", "hello", typer=lambda p, t: sent.append(2) or "")
        assert ok and err == "", (ok, err)   # busy is not a refusal: the TUI queues
        ok, err = send_to_session("ghost", "hello", typer=lambda p, t: sent.append(3) or "")
        assert not ok and "not live" in err, (ok, err)
        ok, err = send_to_session("idle-1", "   ", typer=lambda p, t: sent.append(4) or "")
        assert not ok and "empty" in err, (ok, err)
        assert sent == [1, 2], sent  # busy now types too; empty/ghost still do not
    finally:
        fleetview.STATE["latest"] = prev


# Captured verbatim 2026-08-09 from a real blocking prompt in a live session
# (scratchpad permission test) — the widget every choice in Claude Code uses.
REAL_PROMPT = """ Quick safety check: Is this a project you created or one you trust?

 Security guide

 \u276f 1. Yes, I trust this folder
   2. No, exit

 Enter to confirm \u00b7 Esc to cancel"""

# ...and the ordinary idle screen from the same machine, which must NOT trip it
REAL_IDLE = """\u276f Reply with exactly: MESHED

\u25cf MESHED

\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
\u276f
\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
  [PONYTAIL]
  \u23f5\u23f5 bypass permissions on (shift+tab to cycle) \u00b7 \u2190 1 agent"""


def test_needs_attention():
    """Both halves are required: prose containing a numbered list must not read
    as a prompt, and a footer alone is not a choice."""
    assert fleetview.needs_attention(REAL_PROMPT)
    assert not fleetview.needs_attention(REAL_IDLE)
    assert not fleetview.needs_attention("")
    # a transcript quoting a numbered list, no widget footer -> not blocked
    assert not fleetview.needs_attention("Here is the plan:\n 1. do a thing\n 2. do another")
    # footer with no options -> not a choice either
    assert not fleetview.needs_attention("Enter to confirm")
    # The real 2026-08-09 false positive: a terminal whose output happens to
    # QUOTE a prompt verbatim, with its own output drawn afterwards. Matching
    # anywhere in the screen flagged a healthy session as blocked.
    quoted = REAL_PROMPT + """

=== needs_attention: True
$ python -c "print(1)"
1
\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
\u276f
\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
  [PONYTAIL]
  \u23f5\u23f5 bypass permissions on
  more output
  and more
  and more again"""
    assert not fleetview.needs_attention(quoted), "quoted prompt must not count"


# The other real widget, captured 2026-08-09 from a live tool-permission
# prompt. Note its footer says "Esc to cancel · Tab to amend" with no
# "Enter to confirm" at all — which is why the footer pattern needs both.
REAL_PERMISSION = """ Opened changes in Visual Studio Code

 demo.txt
 Save file to continue\u2026

 Do you want to create demo.txt?
 \u276f 1. Yes
   2. Yes, allow all edits during this session (shift+tab)
   3. No

 Esc to cancel \u00b7 Tab to amend"""


def test_prompt_block():
    """Seeing that a session is blocked is useless without the question and
    the choices — that is what you answer from the phone."""
    got = fleetview.prompt_block(REAL_PERMISSION)
    assert got["question"] == "Do you want to create demo.txt?", got
    assert [o["n"] for o in got["options"]] == ["1", "2", "3"], got
    assert got["options"][0]["label"] == "Yes", got
    assert got["options"][2]["label"] == "No", got

    trust = fleetview.prompt_block(REAL_PROMPT)
    assert "trust" in trust["question"], trust
    assert [o["n"] for o in trust["options"]] == ["1", "2"], trust
    assert trust["options"][0]["label"] == "Yes, I trust this folder", trust

    # nothing to answer -> nothing offered
    assert fleetview.prompt_block(REAL_IDLE) is None
    assert fleetview.prompt_block("") is None


def test_session_state():
    """Precedence matters: a busy session showing a permission prompt is
    blocked on a human, not working, and that is the whole point of the glyph."""
    done = [{"status": "completed"}, {"status": "completed"}]
    part = [{"status": "completed"}, {"status": "in_progress"}]
    assert fleetview.session_state("busy", done, REAL_PROMPT)[0] == "attention"
    assert fleetview.session_state("busy", [], "")[0] == "working"
    assert fleetview.session_state("idle", done, "")[0] == "done"
    assert "clear" in fleetview.session_state("idle", done, "")[1]
    assert fleetview.session_state("idle", part, "")[0] == "waiting"
    assert fleetview.session_state("idle", [], "")[0] == "waiting"
    assert fleetview.session_state(None, None, None)[0] == "waiting"


def test_build_data_carries_state():
    class NoSummarizer:
        def get(self, sid):
            return None

    prev_screens = fleetview.STATE["screens"]
    fleetview.STATE["screens"] = {"s1": REAL_PROMPT}
    try:
        d = build_data([{"sessionId": "s1", "name": "n", "status": "busy"}],
                       {"s1": {"todos": [], "size": 1}}, NoSummarizer(), {},
                       now=1000.0)
        assert d["cards"][0]["state"] == "attention", d["cards"][0]
        assert d["cards"][0]["prompt"]["options"][0]["n"] == "1"
        assert "answer the prompt" in d["cards"][0]["state_text"]
    finally:
        fleetview.STATE["screens"] = prev_screens


def test_new_session():
    """/new takes a FOLDER, never a command — it must not become a way to run
    arbitrary things on the box. And the child must not inherit
    CLAUDE_CODE_CHILD_SESSION: a session that does runs with transcript saving
    off, never writes a session file, and never appears in FleetView (learned
    the hard way 2026-08-09)."""
    seen = {}

    def spawn(cwd, env):
        seen["cwd"], seen["env"] = cwd, env

    with tempfile.TemporaryDirectory() as d:
        os.environ["CLAUDE_CODE_CHILD_SESSION"] = "poison"
        try:
            ok, err = fleetview.new_session(f'"{d}"', spawn=spawn)
            assert ok and err == "", (ok, err)
            assert seen["cwd"] == str(Path(d)), seen["cwd"]
            assert "CLAUDE_CODE_CHILD_SESSION" not in seen["env"]
            assert seen["env"]["CLAUDE_CODE_FORCE_SESSION_PERSISTENCE"] == "1"
        finally:
            os.environ.pop("CLAUDE_CODE_CHILD_SESSION", None)

        seen.clear()
        ok, err = fleetview.new_session("", spawn=spawn)
        assert not ok and "no folder" in err, (ok, err)
        ok, err = fleetview.new_session(str(Path(d) / "nope"), spawn=spawn)
        assert not ok and "not a folder" in err, (ok, err)
        # a command line is not a folder, so it is refused rather than run
        ok, err = fleetview.new_session("claude & calc.exe", spawn=spawn)
        assert not ok and "not a folder" in err, (ok, err)
        assert seen == {}, seen


def test_prompt_rendered_once():
    """The picker was drawn twice on every blocked card (2026-08-09).

    It was interpolated both inside .row and as a direct child of .card, and
    `.card.compact>.prompt` unhides the direct child — so compact mode showed
    two identical widgets. The block must appear exactly once in the card
    template."""
    assert fleetview.PAGE.count("${pr}") == 1, fleetview.PAGE.count("${pr}")
    assert "small?pr" not in fleetview.PAGE


def test_new_chat_needs_no_browser_dialogs():
    """"+ New" did nothing at all when clicked (2026-10-11).

    It asked for the folder with window.prompt(), which VS Code's Simple
    Browser and other embedded webviews answer with null without ever showing
    a dialog — and null read as Cancel. alert() is dropped the same way, so
    its errors were invisible too. The folder box and its errors must live on
    the page."""
    import re
    script = fleetview.PAGE.split("<script>", 1)[1]
    calls = re.findall(r"(?<![\w.])(prompt|alert)\(", script)
    assert not calls, calls
    assert 'id="newcwd"' in fleetview.PAGE and "startNew()" in fleetview.PAGE


def test_close_uses_the_send_path():
    """Closing a session out is typing /exit at it, not a new endpoint.

    /send already types into the live console (D19), so /exit needs no server
    change at all — and must not grow one. Guards that: the button posts
    /exit through /send, and POST still accepts exactly /send and /new."""
    assert 'text:"/exit"' in fleetview.PAGE
    assert "closeSession" in fleetview.PAGE
    src = Path(fleetview.__file__).read_text(encoding="utf-8")
    # closing must not have grown an endpoint of its own; /upload is D36's
    assert 'self.path not in ("/send", "/new", "/upload", "/dictate")' in src


def test_localhost_is_not_slow():
    """"localhost" resolves to ::1 first, so a v4-only listener costs seconds.

    Measured 2026-08-09 against the running service: 2004 ms per request to
    http://localhost:7936, 8 ms to http://127.0.0.1:7936 — on a page that
    polls every 3 s. This binds both a v4 and a v6 listener on one port and
    asserts localhost answers promptly; drop the ::1 listener and the same
    request takes ~2 s (or fails), which is the regression."""
    import http.server
    import urllib.request

    v4 = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _OkHandler)
    port = v4.server_address[1]
    threading.Thread(target=v4.serve_forever, daemon=True).start()
    old = fleetview.PORT
    fleetview.PORT = port
    try:
        if not socket.has_ipv6:              # box with IPv6 off — nothing to prove
            return
        v6 = fleetview._serve_v6_too()
        assert v6 is not None, "no ::1 listener bound — localhost will be slow"
        t = time.time()
        urllib.request.urlopen(f"http://localhost:{port}/", timeout=15).read()
        took = time.time() - t
        assert took < 0.5, f"localhost took {took*1000:.0f}ms — v6 listener missing?"
        v6.shutdown()
    finally:
        fleetview.PORT = old
        v4.shutdown()


def test_multiline_payload():
    """Every line but the last ends with a backslash — the TUI's continuation.

    Verified against the transcript on 2026-08-09 (not the screen, which
    echoes both the box and the sent message): typing AAA\\ ⏎ BBB\\ ⏎ CCC ⏎
    into a live session landed in the jsonl as exactly 'AAA\\nBBB\\nCCC'."""
    assert fleetview.multiline_payload("one") == "one"
    assert fleetview.multiline_payload("a\nb\nc") == "a\\\nb\\\nc"
    assert fleetview.multiline_payload("a\r\nb") == "a\\\nb"
    # a textarea collects trailing newlines; each would be a keystroke for nothing
    assert fleetview.multiline_payload("a\nb\n\n\n") == "a\\\nb"
    assert fleetview.multiline_payload("\n\na") == "a"
    assert fleetview.multiline_payload("   \n  ") == ""
    # blank lines *inside* the message are content and must survive
    assert fleetview.multiline_payload("a\n\nb") == "a\\\n\\\nb"


def test_type_into_console_presses_enter_per_line():
    """A "\\n" in the payload means "press Enter here", so a three-line message
    is three text writes and three Enters — not one run-on line."""
    k = _FakeK32()
    err = fleetview.type_into_console(99, "a\\\nb", k32=k)
    assert err == "", err
    assert k.writes == ["a\\", "\r", "b", "\r"], k.writes


def test_save_upload_only_trusts_the_extension():
    """This endpoint writes files to disk from a request body, so the
    client's filename is used for nothing but its extension, checked against a
    whitelist. The stored name is ours: timestamp + content hash."""
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 50).decode()
    real = fleetview.UPLOADS
    with tempfile.TemporaryDirectory() as d:
        fleetview.UPLOADS = Path(d) / "uploads"
        try:
            path, err = fleetview.save_upload("holiday.PNG", "data:image/png;base64," + png)
            assert err == "" and path.endswith(".png"), (path, err)
            assert Path(path).read_bytes().startswith(b"\x89PNG")
            assert Path(path).parent == fleetview.UPLOADS

            # same bytes -> same name, so re-sending a photo doesn't litter
            again, _ = fleetview.save_upload("other.png", png)
            assert again == path, (again, path)

            # a non-image extension is refused outright
            bad, err = fleetview.save_upload("../../../../evil.exe", png)
            assert bad == "" and "not an image" in err, (bad, err)

            # a traversal dressed as an image is *accepted* — and lands inside
            # UPLOADS under our own name, because the client's name is never
            # used to build the path. That containment is the actual guarantee.
            other = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"y" * 50).decode()
            trav, err = fleetview.save_upload("x\\..\\..\\evil.png", other)
            assert err == "", err
            assert Path(trav).parent == fleetview.UPLOADS, trav
            assert "evil" not in trav and ".." not in trav, trav
            assert len(list(fleetview.UPLOADS.iterdir())) == 2

            assert "decode" in fleetview.save_upload("a.png", "not base64!!")[1]
            big = base64.b64encode(b"z" * (13 * 1024 * 1024)).decode()
            assert "12MB" in fleetview.save_upload("a.png", big)[1]
        finally:
            fleetview.UPLOADS = real


def test_blocked_sessions_float_to_the_top():
    """A session waiting on a prompt is the only one costing time by sitting
    still; an ended one can't be acted on at all. Everything else keeps
    discovery order — a fleet that reshuffles every 3s is worse than one you
    have to scan."""
    class Quiet:
        def get(self, sid):
            return {}

    # blocked LAST in discovery order, so a passing result can only come from
    # build_data actually sorting — sorting it in the test would prove nothing
    sessions = [_session("s1", "plain", status="busy"),
                _session("s2", "idle-one", status="idle"),
                _session("s3", "blocked", status="idle")]
    parsed = {s["sessionId"]: {"goal_raw": "", "model": "m", "context_tokens": 1,
                               "output_tokens_recent": 0, "last_tool": "",
                               "todos": [], "size": 1} for s in sessions}
    screens = fleetview.STATE["screens"]
    fleetview.STATE["screens"] = {
        "s3": "Do you want to proceed?\n  1. Yes\n  2. No\nEnter to confirm"}
    recent = {}
    try:
        d = build_data(sessions, parsed, Quiet(), recent, now=1000.0)
        assert [c["name"] for c in d["cards"]] == ["blocked", "plain", "idle-one"], \
            [(c["name"], c["state"]) for c in d["cards"]]
        assert d["cards"][0]["state"] == "attention", d["cards"][0]["state"]

        # and an ended card sinks below every live one
        fleetview.STATE["screens"] = {}
        d2 = build_data([sessions[0]], {"s1": parsed["s1"]}, Quiet(), recent,
                        now=1000.0 + 1)
        assert [c["ended"] for c in d2["cards"]] == [False, True, True], \
            [(c["name"], c["ended"]) for c in d2["cards"]]
    finally:
        fleetview.STATE["screens"] = screens


def test_transcribe_audio():
    """Dictation posts the clip to Whisper and returns the text.

    The filename extension must follow the *recorded* mime type: iOS Safari
    records audio/mp4, and Whisper rejects mp4 bytes called .webm. The phone is
    the only place FleetView gets dictated into, so getting this wrong would
    mean the feature never worked where it matters."""
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"text": "  hello fleet  "}'

    def opener(req, timeout=None):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        seen["ctype"] = req.get_header("Content-type")
        seen["ua"] = req.get_header("User-agent")
        seen["body"] = req.data
        return _Resp()

    clip = base64.b64encode(b"OggS-fake-audio").decode()
    os.environ["GROQ_API_KEY"] = "test-key"
    try:
        text, err = fleetview.transcribe_audio(clip, "audio/mp4", opener=opener)
        assert err == "" and text == "hello fleet", (text, err)
        assert seen["url"] == fleetview.GROQ_URL
        assert seen["auth"] == "Bearer test-key"
        assert seen["ctype"].startswith("multipart/form-data; boundary=")
        # Cloudflare 403s urllib's default UA with "error code: 1010" — proven
        # live 2026-08-09, the request only succeeds once a UA is set
        assert seen["ua"] and "urllib" not in seen["ua"].lower(), seen["ua"]
        body = seen["body"]
        assert b'filename="dictation.m4a"' in body, body[:400]
        assert b"whisper-large-v3" in body
        assert b"OggS-fake-audio" in body

        # webm from Android/desktop keeps its own extension
        fleetview.transcribe_audio(clip, "audio/webm;codecs=opus", opener=opener)
        assert b'filename="dictation.webm"' in seen["body"]

        assert "unsupported" in fleetview.transcribe_audio(clip, "video/mp4", opener=opener)[1]
        assert "no audio" in fleetview.transcribe_audio("", "audio/mp4", opener=opener)[1]

        # a Groq failure must not leak the request (it carries the key)
        def boom(req, timeout=None):
            raise RuntimeError("401 Bearer test-key rejected")

        text, err = fleetview.transcribe_audio(clip, "audio/mp4", opener=boom)
        assert text == "" and "test-key" not in err, err
        assert err == "transcription failed (RuntimeError)", err
    finally:
        os.environ.pop("GROQ_API_KEY", None)

    text, err = fleetview.transcribe_audio(clip, "audio/mp4", opener=opener)
    assert text == "" and "GROQ_API_KEY" in err, err


def test_origin_allowed():
    """The bug that made sends silently do nothing: VS Code's Simple Browser
    posts from vscode-webview://, which the old guard 403'd while curl from a
    shell sailed through."""
    assert origin_allowed("vscode-webview://01h2x3")   # Simple Browser (D2!)
    assert origin_allowed("vscode-file://vscode-app")
    assert origin_allowed("null")                      # sandboxed frame
    assert origin_allowed("")                          # same-origin, no header
    assert origin_allowed("http://localhost:7936")
    assert origin_allowed("https://my-desktop.tailnet-name.ts.net")
    # an http(s) origin still has to be somewhere we serve from
    assert not origin_allowed("https://evil.example.com")
    assert not origin_allowed("http://127.0.0.1.evil.com")


def test_host_allowed():
    assert host_allowed("localhost:7936")
    assert host_allowed("127.0.0.1:7936")
    assert host_allowed("other-box.tailnet-name.ts.net")
    assert not host_allowed("evil.example.com")
    assert not host_allowed("fakets.net")          # suffix must include the dot
    assert not host_allowed("ts.net.evil.com")     # .ts.net must be the suffix
    assert not host_allowed("")

FIXTURE = Path(__file__).parent / "fixtures" / "sample.jsonl"


def test_parse_transcript_fixture():
    r = parse_transcript(FIXTURE)
    assert r["goal_raw"] == "Build me a fitness tracker", r
    assert r["model"] == "claude-fable-5", r
    # context = usage of the LATEST assistant line: 2 + 0 + 120500
    assert r["context_tokens"] == 120502, r
    # output summed across tailed window: 50 + 30 + 20
    assert r["output_tokens_recent"] == 100, r
    assert r["last_tool"] == "Run test suite", r
    # last assistant *text* — the two tool_use-only messages after it must not
    # blank it out, or every mid-tool-run card would show nothing.
    assert r["last_message"] == "ok", r
    # feed: user turns, assistant text and tool calls, in transcript order.
    # TodoWrite counts as a tool event too — it is still something it did.
    assert [(e["k"], e["t"]) for e in r["events"]] == [
        ("you", "Build me a fitness tracker"),
        ("say", "ok"),
        ("tool", "TodoWrite"),
        ("tool", "Bash: Run test suite"),  # description beats the raw command
    ], r["events"]
    assert r["todos"] == [
        {"text": "write tests", "status": "completed"},
        {"text": "build UI", "status": "in_progress"},
    ], r
    assert r["size"] == FIXTURE.stat().st_size, r


def test_events_carry_timestamps():
    """The feed's whole point is when things happened — ts must survive parsing."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.jsonl"
        p.write_text(json.dumps({
            "type": "assistant", "timestamp": "2026-08-08T21:06:01.687Z",
            "message": {"content": [
                {"type": "text", "text": "hi"},
                {"type": "tool_use", "name": "Read",
                 "input": {"file_path": "C:\\x\\y.py"}}]},
        }) + "\n", encoding="utf-8")
        evs = parse_transcript(p)["events"]
    assert [e["ts"] for e in evs] == ["2026-08-08T21:06:01.687Z"] * 2, evs
    assert evs[1]["t"] == "Read: C:\\x\\y.py", evs


def test_parse_transcript_skips_meta_user_lines():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.jsonl"
        lines = [
            json.dumps({"type": "user", "message": {"role": "user",
                "content": "<system-reminder>injected context</system-reminder>"}}),
            json.dumps({"type": "user", "message": {"role": "user",
                "content": "Caveat: local command output"}}),
            json.dumps({"type": "user", "message": {"role": "user",
                "content": "the real request"}}),
        ]
        p.write_text("\n".join(lines), encoding="utf-8")
        assert parse_transcript(p)["goal_raw"] == "the real request"


def test_parse_transcript_list_content():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.jsonl"
        line = json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "ignored"},
            {"type": "text", "text": "build the"},
            {"type": "text", "text": "dashboard"},
        ]}})
        p.write_text(line, encoding="utf-8")
        assert parse_transcript(p)["goal_raw"] == "build the dashboard"


def test_parse_transcript_missing_file():
    r = parse_transcript(Path("does") / "not" / "exist.jsonl")
    assert r["goal_raw"] is None and r["context_tokens"] is None and r["size"] == 0


def test_read_tail_text():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.txt"
        p.write_text("A" * 50 + "B" * 10, encoding="utf-8")
        assert read_tail_text(p, n=10) == "B" * 10
        assert read_tail_text(Path(d) / "missing.txt") == ""


def _dead_pid():
    pr = subprocess.Popen(["cmd", "/c", "rem"]) if os.name == "nt" else \
         subprocess.Popen(["true"])
    pr.wait()
    return pr.pid


def test_pid_alive():
    assert pid_alive(os.getpid()) is True
    assert pid_alive(_dead_pid()) is False


def test_discover_sessions_filters():
    with tempfile.TemporaryDirectory() as d:
        sdir = Path(d) / "sessions"
        pdir = Path(d) / "projects" / "C--proj"
        sdir.mkdir()
        pdir.mkdir(parents=True)
        (pdir / "sid-live.jsonl").write_text("", encoding="utf-8")

        def put(fname, **kw):
            (sdir / fname).write_text(json.dumps(kw), encoding="utf-8")

        put("1.json", pid=os.getpid(), sessionId="sid-live",
            kind="interactive", name="alive", status="busy")
        put("2.json", pid=_dead_pid(), sessionId="sid-dead",
            kind="interactive", name="dead", status="idle")
        put("3.json", pid=os.getpid(), sessionId="sid-headless",
            kind="print", name="headless", status="busy")
        (sdir / "4.json").write_text("{corrupt", encoding="utf-8")

        got = discover_sessions(sdir, Path(d) / "projects")
        assert [s["name"] for s in got] == ["alive"], got
        assert got[0]["transcript"] == pdir / "sid-live.jsonl", got


def test_discover_sessions_excludes_headless_entrypoint():
    with tempfile.TemporaryDirectory() as d:
        sdir = Path(d) / "sessions"
        sdir.mkdir()
        (Path(d) / "projects").mkdir()
        (sdir / "1.json").write_text(json.dumps(
            {"pid": os.getpid(), "sessionId": "sid-h", "kind": "interactive",
             "entrypoint": "sdk-cli", "name": "headless", "status": "busy"}),
            encoding="utf-8")
        (sdir / "2.json").write_text(json.dumps(
            {"pid": os.getpid(), "sessionId": "sid-i", "kind": "interactive",
             "entrypoint": "cli", "name": "real", "status": "idle"}),
            encoding="utf-8")
        got = discover_sessions(sdir, Path(d) / "projects")
        assert [s["name"] for s in got] == ["real"], got


def test_snapshot_sessions_missing_session_id():
    # F2: refresh_loop used to build this dict inline with s["sessionId"],
    # which raised KeyError for a session dict without that key. Extracted
    # into snapshot_sessions(), which must degrade to key "" instead.
    got = snapshot_sessions([{"name": "no-id", "transcript": None}])
    assert got == {"": {"size": 0}}, got


def test_rule_advice_thresholds():
    assert rule_advice(0) == (0, "")
    assert rule_advice(None) == (0, "")
    assert rule_advice(119_999) == (0, "")            # 59.9995% green
    assert rule_advice(120_000)[0] == 1               # exactly 60% amber
    assert rule_advice(170_000)[0] == 1               # exactly 85% amber
    assert rule_advice(170_001)[0] == 2               # just past 85% red
    assert rule_advice(170_001)[1] == "Compact or clear now"


def _fake_call_factory(counter):
    def fake_call(prompt):
        counter.append(prompt)
        return {"goal": "Ship the tracker", "activity": "Writing tests",
                "done_tasks": ["parser done"], "advice": {"level": 2, "text": "Task done — /clear"}}
    return fake_call


def test_summarizer_growth_gate_and_cache():
    with tempfile.TemporaryDirectory() as d:
        cache = Path(d) / "summaries.json"
        t = Path(d) / "t.jsonl"
        t.write_text("x" * 100, encoding="utf-8")
        calls = []
        s = Summarizer(cache, call=_fake_call_factory(calls))

        parsed = {"size": 100, "context_tokens": 50_000, "goal_raw": "raw goal"}
        e1 = s.maybe_summarize("sid", parsed, t)
        assert len(calls) == 1
        assert e1["goal"] == "Ship the tracker" and e1["ai_level"] == 2

        # unchanged size -> no new call
        assert s.maybe_summarize("sid", parsed, t) == e1
        assert len(calls) == 1

        # grown less than GROWTH_BYTES -> still no call
        parsed2 = dict(parsed, size=100 + GROWTH_BYTES - 1)
        s.maybe_summarize("sid", parsed2, t)
        assert len(calls) == 1

        # grown past the gate -> new call
        parsed3 = dict(parsed, size=100 + GROWTH_BYTES)
        s.maybe_summarize("sid", parsed3, t)
        assert len(calls) == 2

        # cache survives restart: new instance, no growth -> no call
        s2 = Summarizer(cache, call=_fake_call_factory(calls))
        assert s2.get("sid")["goal"] == "Ship the tracker"
        s2.maybe_summarize("sid", parsed3, t)
        assert len(calls) == 2


def test_summarizer_failure_keeps_last_good():
    with tempfile.TemporaryDirectory() as d:
        cache = Path(d) / "summaries.json"
        t = Path(d) / "t.jsonl"
        t.write_text("x", encoding="utf-8")
        calls = []
        s = Summarizer(cache, call=_fake_call_factory(calls))
        good = s.maybe_summarize("sid", {"size": 1, "context_tokens": 0,
                                         "goal_raw": "g"}, t)

        def boom(prompt):
            raise RuntimeError("cli exploded")
        s.call = boom
        got = s.maybe_summarize("sid", {"size": 1 + GROWTH_BYTES,
                                        "context_tokens": 0, "goal_raw": "g"}, t)
        assert got == good  # kept last good entry, no crash


def test_summarizer_failure_backoff():
    # F3: a failed call used to retry every sweep forever (re-billing on every
    # poll). A failure must be "covered" like a success, until the transcript
    # grows GROWTH_BYTES past the size that failed.
    with tempfile.TemporaryDirectory() as d:
        cache = Path(d) / "summaries.json"
        t = Path(d) / "t.jsonl"
        t.write_text("x", encoding="utf-8")
        calls = []

        def boom(prompt):
            calls.append(prompt)
            raise RuntimeError("cli exploded")

        s = Summarizer(cache, call=boom)
        parsed = {"size": 100, "context_tokens": 0, "goal_raw": "g"}

        s.maybe_summarize("sid", parsed, t)
        assert len(calls) == 1  # fires once

        # same size retry -> covered by the failure, no new call
        s.maybe_summarize("sid", parsed, t)
        assert len(calls) == 1

        # grown, but less than GROWTH_BYTES past the failure size -> still covered
        parsed2 = dict(parsed, size=100 + GROWTH_BYTES - 1)
        s.maybe_summarize("sid", parsed2, t)
        assert len(calls) == 1

        # grown GROWTH_BYTES past the failure size -> calls again
        parsed3 = dict(parsed, size=100 + GROWTH_BYTES)
        s.maybe_summarize("sid", parsed3, t)
        assert len(calls) == 2


def test_summarizer_prompt_uses_adaptive_limit():
    # N1: maybe_summarize computed pct against the fixed 200k CONTEXT_LIMIT,
    # so a 440k-context session told Haiku it was at "220% capacity" while
    # build_data's chip (adaptive 1M window once observed > 200k) already
    # showed 44% green -- contradictory advice from the same data.
    with tempfile.TemporaryDirectory() as d:
        cache = Path(d) / "summaries.json"
        t = Path(d) / "t.jsonl"
        t.write_text("x", encoding="utf-8")
        captured = []

        def fake_call(prompt):
            captured.append(prompt)
            return {"goal": "g", "activity": "a", "done_tasks": [],
                    "advice": {"level": 0, "text": ""}}

        s = Summarizer(cache, call=fake_call)
        parsed = {"size": 1, "context_tokens": 440_992, "goal_raw": "g"}
        s.maybe_summarize("sid", parsed, t)
        assert len(captured) == 1
        assert "44%" in captured[0], captured[0]
        assert "220" not in captured[0], captured[0]


def test_summarizer_write_failure_keeps_cache_clean():
    with tempfile.TemporaryDirectory() as d:
        t = Path(d) / "t.jsonl"
        t.write_text("x", encoding="utf-8")
        calls = []
        # cache path is a DIRECTORY -> write_text raises
        s = Summarizer(Path(d), call=_fake_call_factory(calls))
        got = s.maybe_summarize("sid", {"size": 1, "context_tokens": 0,
                                        "goal_raw": "g"}, t)
        assert got is None
        assert s.get("sid") is None  # failed persist must not poison the cache


def _session(sid, name, status="busy"):
    return {"sessionId": sid, "name": name, "cwd": "C:\\Claude\\proj",
            "status": status, "startedAt": 0, "transcript": None}


def test_build_data_merge_and_advice_escalation():
    class StubSummarizer:
        def get(self, sid):
            return {"goal": "AI goal", "activity": "AI activity",
                    "done_tasks": ["a"], "ai_level": 2, "ai_advice": "Task done - /clear"}
    parsed = {"s1": {"goal_raw": "raw", "model": "m", "context_tokens": 10_000,
                     "output_tokens_recent": 5, "last_tool": "Run tests",
                     "todos": [], "size": 1}}
    d = build_data([_session("s1", "one")], parsed, StubSummarizer(), {}, now=1000.0)
    c = d["cards"][0]
    assert c["goal"] == "AI goal" and c["activity"] == "AI activity"
    # context green (5%) but AI says level 2 -> escalates
    assert c["advice"] == {"level": 2, "text": "Task done - /clear"}
    # no TodoWrite list -> falls back to AI done_tasks, shown completed
    assert c["todos"] == [{"text": "a", "status": "completed"}]
    assert d["total"] == 1 and d["busy"] == 1


def test_build_data_rule_floor_not_suppressed():
    class QuietSummarizer:
        def get(self, sid):
            return {"goal": "g", "activity": "a", "done_tasks": [],
                    "ai_level": 0, "ai_advice": ""}
    parsed = {"s1": {"goal_raw": None, "model": None, "context_tokens": 180_000,
                     "output_tokens_recent": 0, "last_tool": None,
                     "todos": [], "size": 1}}
    d = build_data([_session("s1", "one")], parsed, QuietSummarizer(), {}, now=1000.0)
    assert d["cards"][0]["advice"] == {"level": 2, "text": "Compact or clear now"}


def test_build_data_adaptive_context_limit():
    # F1: real sessions exceed 200k (smoke saw 440_992) -> fixed 200k limit
    # misreports. Once observed context > CONTEXT_LIMIT, assume a 1M window.
    class QuietSummarizer:
        def get(self, sid):
            return None
    parsed = {"s1": {"goal_raw": None, "model": None, "context_tokens": 440_992,
                     "output_tokens_recent": 0, "last_tool": None,
                     "todos": [], "size": 1}}
    d = build_data([_session("s1", "one")], parsed, QuietSummarizer(), {}, now=1000.0)
    c = d["cards"][0]
    assert c["context_limit"] == 1_000_000, c
    assert c["context_tokens"] == 440_992, c
    # 440992/1_000_000 = 44.1% -> below the 60% amber threshold -> level 0
    assert c["advice"]["level"] == 0, c["advice"]


def test_build_data_blank_ai_text_does_not_escalate_chip():
    # F7: AI escalating (ai_level=2) with an empty advice text used to leave
    # the chip visible at level 2 but blank (or showing wrong-level text) --
    # a level must not display without text behind it.
    class EmptyTextEscalator:
        def get(self, sid):
            return {"goal": "g", "activity": "a", "done_tasks": [],
                    "ai_level": 2, "ai_advice": ""}
    parsed = {"s1": {"goal_raw": None, "model": None, "context_tokens": 0,
                     "output_tokens_recent": 0, "last_tool": None,
                     "todos": [], "size": 1}}
    d = build_data([_session("s1", "one")], parsed, EmptyTextEscalator(), {}, now=1000.0)
    assert d["cards"][0]["advice"]["level"] == 0, d["cards"][0]["advice"]


def test_build_data_chip_level_matches_rule_when_ai_text_blank():
    # N4: rl=1 (amber "Consider /compact") with al=2 but blank ai_advice used
    # to leave the chip at level 2 (red) while still showing the amber rule
    # wording -- level and text must describe the same advice.
    class BlankTextEscalator:
        def get(self, sid):
            return {"goal": "g", "activity": "a", "done_tasks": [],
                    "ai_level": 2, "ai_advice": ""}
    parsed = {"s1": {"goal_raw": None, "model": None, "context_tokens": 120_000,
                     "output_tokens_recent": 0, "last_tool": None,
                     "todos": [], "size": 1}}
    d = build_data([_session("s1", "one")], parsed, BlankTextEscalator(), {}, now=1000.0)
    assert d["cards"][0]["advice"] == {"level": 1, "text": "Consider /compact"}, \
        d["cards"][0]["advice"]


def test_build_data_grey_then_drop():
    class NoSummarizer:
        def get(self, sid):
            return None
    recent = {}
    parsed = {"s1": {"goal_raw": "g", "model": None, "context_tokens": 1,
                     "output_tokens_recent": 0, "last_tool": None,
                     "todos": [], "size": 1}}
    build_data([_session("s1", "one")], parsed, NoSummarizer(), recent, now=1000.0)
    # session vanished within TTL -> greyed card remains
    d2 = build_data([], {}, NoSummarizer(), recent, now=1000.0 + RECENT_TTL - 1)
    assert len(d2["cards"]) == 1 and d2["cards"][0]["ended"] is True
    assert d2["total"] == 0
    # past TTL -> gone
    d3 = build_data([], {}, NoSummarizer(), recent, now=1000.0 + RECENT_TTL + 1)
    assert d3["cards"] == []


def test_page_is_html():
    assert PAGE.lstrip().lower().startswith("<!doctype html")
    assert "/data.json" in PAGE


def test_build_data_returned_cards_not_aliased_to_recent_cache():
    class NoSummarizer:
        def get(self, sid):
            return None
    recent = {}
    parsed = {"s1": {"goal_raw": "g", "model": None, "context_tokens": 1,
                     "output_tokens_recent": 0, "last_tool": None,
                     "todos": [{"text": "a", "status": "pending"}], "size": 1}}
    d = build_data([_session("s1", "one")], parsed, NoSummarizer(), recent,
                   now=1000.0)
    d["cards"][0]["goal"] = "MUTATED"
    # F9: todos/advice are nested mutable members. A shallow dict(card) copy
    # into the grey-out cache still shares the same list/dict objects with the
    # caller's card, so mutating them (e.g. TodoWrite growing the list) would
    # leak into the cached replay. Nested members must be copied too.
    d["cards"][0]["todos"].append({"text": "MUTATED", "status": "pending"})
    d["cards"][0]["advice"]["text"] = "MUTATED"
    d2 = build_data([], {}, NoSummarizer(), recent, now=1000.1)
    assert d2["cards"][0]["goal"] == "g"  # cache must not see caller's edit
    assert d2["cards"][0]["todos"] == [{"text": "a", "status": "pending"}], d2["cards"][0]["todos"]
    assert d2["cards"][0]["advice"]["text"] != "MUTATED", d2["cards"][0]["advice"]


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  {name} ok")
    print("ALL OK")
