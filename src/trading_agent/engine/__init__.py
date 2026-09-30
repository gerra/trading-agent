"""Application service that turns market data + strategies into paper trades.

    engine.py   TradingEngine.tick(): the one function that moves money (paper money)
    risk.py     RiskManager: every Signal passes through here before it can become an Order
    state.py    EngineState + repository: sleeves, reserve cash, daily stats, breakers, persistence

Instrument keys are '<exchange>:<BASE>/<QUOTE>' e.g. 'kraken:BTC/USD'. Quote currencies are
assumed to be USD-equivalent stablecoins so equity can be summed across sleeves.
"""
