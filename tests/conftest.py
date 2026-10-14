import threading
import time

import pytest
import uvicorn

from evalkit.core.models import Context, Output, TestCase
from evalkit.core.storage import Storage
from evalkit.scorers import Services


@pytest.fixture
def store(tmp_path):
    with Storage(tmp_path / "t.db") as s:
        yield s


@pytest.fixture
def services():
    return Services()


def make_output(answer="", contexts=None, **kw) -> Output:
    return Output(answer=answer, contexts=[Context(text=t, id=i) for i, t in (contexts or [])], **kw)


@pytest.fixture
def case():
    return TestCase(id="q1", input="Where is the Eiffel Tower?", expected="Paris", relevant=["d1"])


@pytest.fixture(scope="session")
def rag_url():
    """The demo RAG app, served for real on a free port."""
    import socket

    from examples.demo_rag.app import build_app

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(build_app(), host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/ask"
    server.should_exit = True
    thread.join(5)
