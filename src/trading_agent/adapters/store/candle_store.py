"""Local candle cache: data/market/<exchange>/<BASE>_<QUOTE>__<timeframe>.jsonl, deduped by ts.

Feeds backtests (so proposals are judged on real recent data) and offline analysis.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from trading_agent.core.models import Candle


class CandleStore:
    def __init__(self, market_dir: Path) -> None:
        self.dir = market_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, instrument: str, timeframe: str) -> Path:
        ex, sym = instrument.split(":", 1)
        return self.dir / ex / f"{sym.replace('/', '_')}__{timeframe}.jsonl"

    def instruments(self, timeframe: str) -> list[str]:
        out = []
        for p in self.dir.glob(f"*/*__{timeframe}.jsonl"):
            ex = p.parent.name
            sym = p.name[: -len(f"__{timeframe}.jsonl")].replace("_", "/", 1)
            out.append(f"{ex}:{sym}")
        return sorted(out)

    def read(self, instrument: str, timeframe: str, since: datetime | None = None) -> list[Candle]:
        p = self.path(instrument, timeframe)
        if not p.exists():
            return []
        seen: dict[datetime, Candle] = {}
        with p.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    c = Candle.model_validate_json(line)
                except ValueError:
                    continue
                if since and c.ts < since:
                    continue
                seen[c.ts] = c
        return [seen[k] for k in sorted(seen)]

    def append(self, instrument: str, timeframe: str, candles: list[Candle]) -> int:
        """Append candles newer than the last stored one. Returns number written."""
        p = self.path(instrument, timeframe)
        p.parent.mkdir(parents=True, exist_ok=True)
        last_ts = self._last_ts(p)
        new = [c for c in candles if last_ts is None or c.ts > last_ts]
        if not new:
            return 0
        with p.open("a", encoding="utf-8") as fh:
            for c in new:
                fh.write(c.model_dump_json() + "\n")
        return len(new)

    @staticmethod
    def _last_ts(p: Path) -> datetime | None:
        if not p.exists() or p.stat().st_size == 0:
            return None
        with p.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            chunk = min(size, 4096)
            fh.seek(size - chunk)
            tail = fh.read().decode("utf-8", errors="ignore").strip().splitlines()
        for line in reversed(tail):
            try:
                return Candle.model_validate_json(line).ts
            except ValueError:
                continue
        return None
