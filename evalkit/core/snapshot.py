"""Make a safe, read-only copy of an app's data so evaluations never touch the real thing.

Three kinds of source are understood:
  * a SQLite file          -> copied with SQLite's own backup API (consistent even if the app is running)
  * a folder or other file -> plain copy
  * a Postgres URL         -> pg_dump of the source, restored into a *different* database (needs pg tools,
                              or a running Docker container name via ``docker_container``)

The source is only ever read. The copy refuses to overwrite the source.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import urlparse


class SnapshotError(Exception):
    pass


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def snapshot_sqlite(source: Path, dest: Path) -> dict:
    # mode=ro: the connection cannot write to the source even by mistake
    uri = f"file:{source.resolve().as_posix()}?mode=ro"
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(uri, uri=True)
    try:
        with sqlite3.connect(dest) as dst:
            src.backup(dst)
        tables = src.execute("select count(*) from sqlite_master where type='table'").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        raise SnapshotError(f"{source} is not a readable SQLite database: {exc}") from None
    finally:
        src.close()
    return {"kind": "sqlite", "source": str(source), "copy": str(dest), "tables": tables, "bytes": dest.stat().st_size}


def snapshot_path(source: Path, dest: Path) -> dict:
    if source.is_dir():
        shutil.copytree(source, dest)
        size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        size = dest.stat().st_size
    return {"kind": "files", "source": str(source), "copy": str(dest), "bytes": size}


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(16).startswith(b"SQLite format 3")
    except OSError:
        return False


def snapshot_postgres(source_url: str, copy_name: str, docker_container: str | None = None) -> dict:
    """Dump ``source_url`` and restore it into a new database called ``copy_name`` on the same server."""
    u = urlparse(source_url)
    src_db = u.path.lstrip("/")
    if not src_db:
        raise SnapshotError("the Postgres URL must include a database name, e.g. postgresql://user:pw@host:5432/mydb")
    if copy_name == src_db:
        raise SnapshotError("the copy must have a different name from the source database")
    if not copy_name.replace("_", "").isalnum():
        raise SnapshotError("the copy name may only contain letters, numbers and underscores")

    host_args = ["-h", u.hostname or "localhost", "-p", str(u.port or 5432), "-U", u.username or "postgres"]
    env_pw = {"PGPASSWORD": u.password} if u.password else {}

    def pg(*cmd: str, stdin: bytes | None = None) -> subprocess.CompletedProcess:
        if docker_container:
            args = ["docker", "exec", "-i"] + [x for k, v in env_pw.items() for x in ("-e", f"{k}={v}")]
            args += [docker_container, *cmd]
            env = None
        else:
            if not shutil.which(cmd[0]):
                raise SnapshotError(
                    f"'{cmd[0]}' is not installed here. Install the Postgres client tools, or pass "
                    "--docker-container with the name of your running Postgres container."
                )
            args, env = list(cmd), {**__import__("os").environ, **env_pw}
        proc = subprocess.run(args, input=stdin, capture_output=True, env=env, check=False)
        if proc.returncode != 0:
            raise SnapshotError(f"{cmd[0]} failed: {proc.stderr.decode(errors='replace').strip()[:400]}")
        return proc

    exists = pg("psql", *host_args, "-d", "postgres", "-tAc", f"select 1 from pg_database where datname='{copy_name}'")
    if exists.stdout.strip():
        raise SnapshotError(f"database '{copy_name}' already exists, so nothing was changed. Pick another name.")
    dump = pg("pg_dump", *host_args, "-d", src_db, "--no-owner", "--no-privileges")  # read-only on the source
    pg("psql", *host_args, "-d", "postgres", "-c", f'create database "{copy_name}"')
    pg("psql", *host_args, "-d", copy_name, "-v", "ON_ERROR_STOP=1", stdin=dump.stdout)
    copy_url = u._replace(path=f"/{copy_name}").geturl()
    return {"kind": "postgres", "source": f"{u.hostname}/{src_db}", "copy": copy_url, "bytes": len(dump.stdout)}


def snapshot(source: str, dest: str, docker_container: str | None = None) -> dict:
    if source.startswith(("postgres://", "postgresql://")):
        return snapshot_postgres(source, dest, docker_container)
    src = Path(source)
    if not src.exists():
        raise SnapshotError(f"{source} does not exist")
    out = Path(dest)
    if _same(src, out):
        raise SnapshotError("the copy must not be the same as the source")
    if out.exists():
        raise SnapshotError(f"{dest} already exists, so nothing was changed. Choose another name or delete it first.")
    return snapshot_sqlite(src, out) if src.is_file() and _is_sqlite(src) else snapshot_path(src, out)
