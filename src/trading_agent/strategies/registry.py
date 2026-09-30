"""Strategy registry: which strategies exist, their status and capital allocation.

State lives in `data/state/strategy_manifest.json` (the manifest). Built-in strategies are
imported by dotted path; generated ones are loaded from a file inside the mutable zone and
must pass the sandbox policy check *before* import - importing is executing.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field

from trading_agent.strategies.base import Strategy

log = structlog.get_logger(__name__)

Status = Literal["champion", "challenger", "retired"]
Origin = Literal["builtin", "generated"]


class StrategyEntry(BaseModel):
    id: str
    origin: Origin
    module: str = Field(description="dotted module path (builtin) or file name (generated)")
    class_name: str
    status: Status
    allocation_pct: float = Field(ge=0, le=100)
    params: dict[str, Any] = Field(default_factory=dict)
    since: datetime
    version: int = 1
    proposal_id: str | None = None
    notes: str = ""


class Manifest(BaseModel):
    entries: list[StrategyEntry] = Field(default_factory=list)

    def get(self, strategy_id: str) -> StrategyEntry | None:
        return next((e for e in self.entries if e.id == strategy_id), None)


def default_manifest(now: datetime) -> Manifest:
    return Manifest(
        entries=[
            StrategyEntry(
                id="ema_crossover",
                origin="builtin",
                module="trading_agent.strategies.builtin.ema_crossover",
                class_name="EmaCrossover",
                status="champion",
                allocation_pct=50,
                since=now,
            ),
            StrategyEntry(
                id="new_listing_momentum",
                origin="builtin",
                module="trading_agent.strategies.builtin.new_listing_momentum",
                class_name="NewListingMomentum",
                status="challenger",
                allocation_pct=20,
                since=now,
            ),
            StrategyEntry(
                id="mean_reversion",
                origin="builtin",
                module="trading_agent.strategies.builtin.mean_reversion",
                class_name="MeanReversion",
                status="challenger",
                allocation_pct=15,
                since=now,
            ),
        ]
    )


class StrategyRegistry:
    def __init__(
        self,
        manifest_path: Path,
        generated_dir: Path,
        policy_check: Callable[[str], list[str]],
    ) -> None:
        """`policy_check(source) -> list of violations` is the sandbox static check."""
        self.manifest_path = manifest_path
        self.generated_dir = generated_dir.resolve()
        self._policy_check = policy_check
        self.manifest = self._load()

    # ----- persistence -----
    def _load(self) -> Manifest:
        if self.manifest_path.exists():
            return Manifest.model_validate_json(self.manifest_path.read_text())
        manifest = default_manifest(datetime.now(UTC))
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self._write(manifest)
        return manifest

    def _write(self, manifest: Manifest) -> None:
        tmp = self.manifest_path.with_suffix(".tmp")
        tmp.write_text(manifest.model_dump_json(indent=2))
        tmp.replace(self.manifest_path)

    def save(self) -> None:
        self._write(self.manifest)

    def reload(self) -> None:
        self.manifest = self._load()

    # ----- queries -----
    def active_entries(self) -> list[StrategyEntry]:
        return [e for e in self.manifest.entries if e.status != "retired"]

    def champion(self) -> StrategyEntry | None:
        return next((e for e in self.manifest.entries if e.status == "champion"), None)

    # ----- mutations (all go through save()) -----
    def add_generated(self, entry: StrategyEntry) -> None:
        if entry.origin != "generated":
            raise ValueError("add_generated only accepts generated entries")
        if self.manifest.get(entry.id):
            raise ValueError(f"strategy id already exists: {entry.id}")
        self.manifest.entries.append(entry)
        self.save()

    def remove(self, strategy_id: str) -> None:
        self.manifest.entries = [e for e in self.manifest.entries if e.id != strategy_id]
        self.save()

    def set_status(
        self, strategy_id: str, status: Status, allocation_pct: float | None = None
    ) -> None:
        e = self._require(strategy_id)
        e.status = status
        if allocation_pct is not None:
            e.allocation_pct = allocation_pct
        self.save()

    def set_params(self, strategy_id: str, params: dict[str, Any], note: str = "") -> None:
        e = self._require(strategy_id)
        e.params = params
        e.version += 1
        if note:
            e.notes = note
        self.save()

    def _require(self, strategy_id: str) -> StrategyEntry:
        e = self.manifest.get(strategy_id)
        if not e:
            raise KeyError(f"unknown strategy: {strategy_id}")
        return e

    # ----- loading code -----
    def instantiate(self, entry: StrategyEntry) -> Strategy:
        cls = self._load_class(entry)
        strategy = cls(entry.params)
        if strategy.id != entry.id:
            raise ValueError(
                f"class {entry.class_name} has id '{strategy.id}' but manifest says '{entry.id}'"
            )
        return strategy

    def instantiate_active(self) -> dict[str, Strategy]:
        """Returns id -> Strategy for all non-retired entries; broken entries are logged and skipped."""
        out: dict[str, Strategy] = {}
        for e in self.active_entries():
            try:
                out[e.id] = self.instantiate(e)
            except Exception as exc:  # noqa: BLE001 - one bad strategy must not stop the book
                log.error("strategy_load_failed", strategy_id=e.id, error=str(exc))
        return out

    def _load_class(self, entry: StrategyEntry) -> type[Strategy]:
        if entry.origin == "builtin":
            module = importlib.import_module(entry.module)
        else:
            module = self._import_generated(entry.module)
        cls = getattr(module, entry.class_name, None)
        if cls is None or not (isinstance(cls, type) and issubclass(cls, Strategy)):
            raise TypeError(f"{entry.module}.{entry.class_name} is not a Strategy subclass")
        return cls

    def _import_generated(self, file_name: str) -> Any:
        path = (self.generated_dir / file_name).resolve()
        if path.parent != self.generated_dir or path.suffix != ".py":
            raise PermissionError(f"refusing to import outside the mutable zone: {path}")
        source = path.read_text()
        violations = self._policy_check(source)
        if violations:
            raise PermissionError(f"{file_name} violates sandbox policy: {violations}")
        mod_name = f"trading_agent.strategies.generated.{path.stem}"
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        return module


def load_class_from_source(source: str, class_name: str | None = None) -> type[Strategy]:
    """Compile strategy source in a throw-away module and return its Strategy subclass.

    Only used inside the validator subprocess AFTER the sandbox check passed.
    """
    import types

    mod = types.ModuleType("_candidate_strategy")
    exec(compile(source, "<candidate>", "exec"), mod.__dict__)  # noqa: S102 - sandboxed by caller
    candidates = [
        v
        for v in vars(mod).values()
        if isinstance(v, type) and issubclass(v, Strategy) and v is not Strategy
    ]
    if class_name:
        candidates = [c for c in candidates if c.__name__ == class_name]
    if len(candidates) != 1:
        raise ValueError(f"expected exactly one Strategy subclass, found {len(candidates)}")
    return candidates[0]
