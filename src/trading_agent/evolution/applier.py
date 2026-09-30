"""ProposalApplier: the only code that writes into the mutable zone or edits the manifest.

Every apply is atomic from the registry's point of view and is git-committed (when enabled)
so `git log -- src/trading_agent/strategies/generated` is a complete audit trail.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import structlog
from pydantic import BaseModel

from trading_agent.evolution.proposal import Proposal
from trading_agent.evolution.validator import ValidationResult
from trading_agent.ports import ClockPort
from trading_agent.settings import Settings
from trading_agent.strategies.registry import StrategyEntry, StrategyRegistry

log = structlog.get_logger(__name__)
GIT_IDENTITY = [
    "-c",
    "user.name=trading-agent",
    "-c",
    "user.email=trading-agent@localhost",
    "-c",
    "safe.directory=*",
]


class AppliedChange(BaseModel):
    file_path: str | None = None
    commit: str | None = None
    description: str


class ProposalApplier:
    def __init__(self, settings: Settings, registry: StrategyRegistry, clock: ClockPort) -> None:
        self.s = settings
        self.registry = registry
        self.clock = clock
        self.zone = settings.generated_strategies_dir.resolve()
        self.repo_root = settings.repo_root.resolve()

    def apply(self, p: Proposal, validation: ValidationResult) -> AppliedChange:
        if p.kind == "new_strategy":
            return self._apply_new(p, validation)
        if p.kind == "tune_params":
            entry = self.registry.manifest.get(p.strategy_id)
            assert entry is not None
            old = dict(entry.params)
            self.registry.set_params(
                p.strategy_id, {**entry.params, **p.params()}, note=f"{p.id}: {p.hypothesis[:120]}"
            )
            commit = self._commit(
                f"evolve: tune {p.strategy_id} params ({p.id})", [self.registry.manifest_path]
            )
            return AppliedChange(commit=commit, description=f"params {old} -> {entry.params}")
        if p.kind == "retire_strategy":
            self.registry.set_status(p.strategy_id, "retired", allocation_pct=0)
            commit = self._commit(
                f"evolve: retire {p.strategy_id} ({p.id})", [self.registry.manifest_path]
            )
            return AppliedChange(commit=commit, description=f"retired {p.strategy_id}")
        return AppliedChange(description="no change")

    def _apply_new(self, p: Proposal, validation: ValidationResult) -> AppliedChange:
        file_name = f"{p.strategy_id}.py"
        path = (self.zone / file_name).resolve()
        if path.parent != self.zone:
            raise PermissionError(f"refusing to write outside the mutable zone: {path}")
        if path.exists():
            raise FileExistsError(path)
        header = (
            f'"""Generated strategy {p.strategy_id} - proposal {p.id} - {self.clock.now().isoformat()}\n\n'
            f"Hypothesis: {p.hypothesis.strip()}\n\nRationale: {p.rationale.strip()}\n\n"
            f'Backtest gate: {validation.reason}\n"""\n\n'
        )
        path.write_text(header + p.source_code.rstrip() + "\n", encoding="utf-8")
        entry = StrategyEntry(
            id=p.strategy_id,
            origin="generated",
            module=file_name,
            class_name=p.class_name,
            status="challenger",
            allocation_pct=self.s.evolution.challenger_allocation_pct,
            since=self.clock.now(),
            proposal_id=p.id,
            notes=p.hypothesis[:200],
        )
        try:
            self.registry.add_generated(entry)
            self.registry.instantiate(
                entry
            )  # policy-checked import; proves the file loads in-process
        except Exception:
            path.unlink(missing_ok=True)
            if self.registry.manifest.get(p.strategy_id):
                self.registry.remove(p.strategy_id)
            raise
        commit = self._commit(
            f"evolve: add strategy {p.strategy_id} ({p.id})", [path, self.registry.manifest_path]
        )
        rel = path.relative_to(self.repo_root) if path.is_relative_to(self.repo_root) else path
        return AppliedChange(
            file_path=str(rel), commit=commit, description="new challenger strategy"
        )

    def rollback_new(self, p: Proposal) -> None:
        path = self.zone / f"{p.strategy_id}.py"
        path.unlink(missing_ok=True)
        if self.registry.manifest.get(p.strategy_id):
            self.registry.remove(p.strategy_id)

    def _commit(self, message: str, paths: list[Path]) -> str | None:
        """git-add and commit the given paths with a fixed identity. Never raises."""
        if not self.s.evolution.git_commit:
            return None
        root = self.repo_root
        try:
            tracked = [str(p) for p in paths if p.is_relative_to(root) and not _ignored(p, root)]
            if not tracked:
                return None
            _git(root, "add", "--", *tracked)
            _git(root, "commit", "-q", "-m", message, "--", *tracked)
            sha = _git(root, "rev-parse", "--short", "HEAD").strip()
            log.info("evolution_committed", commit=sha, message=message)
            return sha
        except (subprocess.SubprocessError, OSError) as exc:
            log.warning("git_commit_skipped", error=str(exc)[:300])
            return None


def _git(root: Path, *args: str) -> str:
    """Run one fixed git command (no shell) inside the repo root."""
    proc = subprocess.run(
        ["git", *GIT_IDENTITY, *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.stdout


def _ignored(p: Path, root: Path) -> bool:
    try:
        r = subprocess.run(
            ["git", *GIT_IDENTITY, "check-ignore", "-q", str(p)],
            cwd=root,
            capture_output=True,
            timeout=10,
        )
        return r.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return True
