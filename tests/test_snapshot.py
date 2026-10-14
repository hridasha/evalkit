import sqlite3

import pytest
from typer.testing import CliRunner

from evalkit.cli import app
from evalkit.core.snapshot import SnapshotError, snapshot


def make_db(path):
    with sqlite3.connect(path) as c:
        c.execute("create table t(x)")
        c.execute("insert into t values (1)")


def test_sqlite_copy_is_independent(tmp_path):
    src, dst = tmp_path / "a.db", tmp_path / "b.db"
    make_db(src)
    info = snapshot(str(src), str(dst))
    assert info["kind"] == "sqlite" and info["tables"] == 1
    with sqlite3.connect(dst) as c:  # writing to the copy must not change the original
        c.execute("insert into t values (2)")
    with sqlite3.connect(src) as c:
        assert c.execute("select count(*) from t").fetchone()[0] == 1


def test_folder_copy(tmp_path):
    (tmp_path / "up").mkdir()
    (tmp_path / "up" / "f.txt").write_text("hi")
    assert snapshot(str(tmp_path / "up"), str(tmp_path / "up2"))["kind"] == "files"
    assert (tmp_path / "up2" / "f.txt").read_text() == "hi"


def test_refuses_overwrite_and_same_path(tmp_path):
    src = tmp_path / "a.db"
    make_db(src)
    with pytest.raises(SnapshotError):
        snapshot(str(src), str(src))
    (tmp_path / "b.db").write_text("x")
    with pytest.raises(SnapshotError):
        snapshot(str(src), str(tmp_path / "b.db"))
    with pytest.raises(SnapshotError):
        snapshot(str(tmp_path / "nope.db"), str(tmp_path / "c.db"))


def test_postgres_guards():
    with pytest.raises(SnapshotError):
        snapshot("postgresql://u:p@h:5432/app", "app")
    with pytest.raises(SnapshotError):
        snapshot("postgresql://u:p@h:5432/app", "bad name;")


def test_cli(tmp_path):
    src = tmp_path / "a.db"
    make_db(src)
    r = CliRunner().invoke(app, ["snapshot", str(src), str(tmp_path / "copy.db")])
    assert r.exit_code == 0 and "only read" in r.output
    assert CliRunner().invoke(app, ["snapshot", str(src), str(src)]).exit_code == 2
