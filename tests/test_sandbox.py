import pytest

from trading_agent.evolution.sandbox import check_source

GOOD = '''
"""A fine strategy."""
from typing import Any, ClassVar

from trading_agent.core.models import Signal
from trading_agent.strategies.base import Strategy, StrategyContext
from trading_agent.strategies.indicators import sma

LOOKBACK = 20


class Momo(Strategy):
    id: ClassVar[str] = "momo"
    description: ClassVar[str] = "toy"
    default_params: ClassVar[dict[str, Any]] = {"n": LOOKBACK}

    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        out = []
        for sym, cs in ctx.candles.items():
            closes = [c.close for c in cs]
            m = sma(closes, int(self.params["n"]))[-1]
            if m is not None and closes[-1] > m and not ctx.has_position(sym):
                out.append(self.buy(sym, 0.5, "above mean", 300.0))
            elif m is not None and closes[-1] < m and ctx.has_position(sym):
                out.append(self.sell(sym, "below mean"))
        return out
'''


def test_good_strategy_passes():
    assert check_source(GOOD) == []


@pytest.mark.parametrize(
    "snippet, needle",
    [
        ("import os\n", "import of 'os'"),
        ("from subprocess import run\n", "import from 'subprocess'"),
        ("from trading_agent.strategies.base import *\n", "import *"),
        ("X = eval('1')\n", "forbidden name 'eval'"),
        ("X = open('/etc/passwd')\n", "forbidden name 'open'"),
        ("X = Strategy.__subclasses__()\n", "dunder attribute"),
        ("def f():\n    import socket\n", "nested import"),
        ("async def g():\n    return 1\n", "AsyncFunctionDef"),
        ("print('hi')\n", "top-level statement not allowed"),
        (
            "import math\nclass Other(Strategy):\n    id = 'other'\n",
            "exactly one Strategy subclass",
        ),
        ("def h():\n    global LOOKBACK\n", "Global"),
    ],
)
def test_violations_are_caught(snippet, needle):
    violations = check_source(GOOD + "\n" + snippet)
    assert any(needle in v for v in violations), violations


def test_syntax_error_and_size_limit():
    assert "syntax error" in check_source("def (:")[0]
    assert any("too long" in v for v in check_source(GOOD + "#" * 30_000))
