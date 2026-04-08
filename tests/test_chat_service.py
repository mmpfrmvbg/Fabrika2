from types import SimpleNamespace

from factory.chat_service import ChatService


def _service() -> ChatService:
    # Для unit-тестов _run_qwen_chat нам не нужен init c БД.
    return object.__new__(ChatService)


def test_run_qwen_chat_passes_prompt_via_stdin(monkeypatch):
    service = _service()
    session = {
        "prompt": "Проверь\nспецсимволы --channel CI && ;\nи кавычки \"ok\"",
        "context": {
            "work_item_id": "WI-1",
            "title": "Title with\nnewlines",
            "description": "Desc --yolo",
            "status": "open",
        },
        "response": "",
        "status": "pending",
    }
    chunks: list[str] = []
    called: dict = {}

    def fake_run(cmd, **kwargs):
        called["cmd"] = cmd
        called["kwargs"] = kwargs
        return SimpleNamespace(stdout="line1\nline2\n", stderr="", returncode=0)

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/qwen" if name == "qwen" else None)
    monkeypatch.setattr("subprocess.run", fake_run)

    service._run_qwen_chat(session, chunks.append)

    assert called["cmd"] == ["/usr/bin/qwen", "-p", "-", "--channel", "CI", "--yolo"]
    assert isinstance(called["kwargs"]["input"], str)
    assert called["kwargs"]["input"] == service._build_chat_prompt(session["prompt"], session["context"])
    assert called["kwargs"]["text"] is True
    assert chunks == ["line1\n", "line2\n"]
    assert session["status"] == "done"


def test_run_qwen_chat_falls_back_to_qwen_code(monkeypatch):
    service = _service()
    session = {
        "prompt": "hello",
        "context": {},
        "response": "",
        "status": "pending",
    }

    def fake_which(name):
        if name == "qwen":
            return None
        if name == "qwen-code":
            return "/usr/local/bin/qwen-code"
        return None

    called: dict = {}

    def fake_run(cmd, **kwargs):
        called["cmd"] = cmd
        called["kwargs"] = kwargs
        return SimpleNamespace(stdout="ok\n", stderr="", returncode=0)

    monkeypatch.setattr("shutil.which", fake_which)
    monkeypatch.setattr("subprocess.run", fake_run)

    service._run_qwen_chat(session, lambda _: None)

    assert called["cmd"][0] == "/usr/local/bin/qwen-code"
    assert called["cmd"][1:3] == ["-p", "-"]
    assert called["kwargs"]["input"] == service._build_chat_prompt("hello", {})
