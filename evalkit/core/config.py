"""evalkit.toml: which app to test, how to score it, and when to fail the build."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from evalkit.llm import build_embedder, build_llm
from evalkit.scorers import Scorer, Services, build_scorers
from evalkit.targets import Target, build_target

DEFAULT_SCORERS = [{"name": "contains"}]


@dataclass
class Config:
    name: str = "eval"
    version: str = ""
    testset: str | None = None
    target: dict = field(default_factory=dict)
    scorers: list[dict] = field(default_factory=lambda: list(DEFAULT_SCORERS))
    llm: dict | None = None
    embedder: dict | None = None
    run: dict = field(default_factory=dict)
    gate: dict = field(default_factory=dict)
    path: Path | None = None

    def build_target(self) -> Target:
        if not self.target:
            raise ValueError("the config has no [target] section")
        return build_target(self.target)

    def build_scorers(self) -> list[Scorer]:
        return build_scorers(self.scorers)

    def build_services(self, cache_path: str | Path | None = None) -> Services:
        return Services(llm=build_llm(self.llm, cache_path), embedder=build_embedder(self.embedder))

    def dump(self) -> dict:
        """What is stored with a run (no secrets live in the config, keys come from the environment)."""
        return {"name": self.name, "version": self.version, "target": self.target, "scorers": self.scorers,
                "llm": self.llm, "embedder": self.embedder, "run": self.run}  # fmt: skip


def parse_config(data: dict, path: Path | None = None) -> Config:
    known = {"name", "version", "testset", "target", "scorers", "llm", "embedder", "run", "gate"}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ValueError(f"unknown config section(s): {', '.join(unknown)}")
    cfg = Config(path=path)
    for key in known:
        if key in data:
            setattr(cfg, key, data[key])
    base = path.parent if path else Path.cwd()
    if cfg.testset and not Path(cfg.testset).is_absolute():
        cfg.testset = str((base / cfg.testset).resolve())
    return cfg


def load_config(path: str | Path = "evalkit.toml") -> Config:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"no config file at {p}. Create one (see examples/evalkit.toml) or pass --config")
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{p} is not valid TOML: {exc}") from None
    return parse_config(data, p.resolve())
