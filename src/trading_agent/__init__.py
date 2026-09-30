"""trading_agent - a self-improving, paper-trading crypto agent.

Read ARCHITECTURE.md at the repository root before touching anything.
Layer map (dependencies only point downwards):

    cli / scheduler          -> entrypoints and job wiring
    engine / discovery /     -> application services (use ports, never adapters directly)
    evolution / reporting
    strategies               -> pluggable signal generators (pure functions of market data)
    ports                    -> Protocol interfaces the services depend on
    adapters                 -> concrete implementations of ports (ccxt, Telegram, Anthropic, files)
    core                     -> pure domain models and math, zero IO
"""

__version__ = "0.1.0"
