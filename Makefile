.PHONY: install test lint fmt tick run evolve report check docker
install: ; uv venv && . .venv/bin/activate && uv pip install -e ".[dev]"
test:    ; . .venv/bin/activate && pytest
lint:    ; . .venv/bin/activate && ruff check src tests
fmt:     ; . .venv/bin/activate && ruff format src tests && ruff check --fix src tests
tick:    ; . .venv/bin/activate && trading-agent --pretty-logs tick
run:     ; . .venv/bin/activate && trading-agent run
evolve:  ; . .venv/bin/activate && trading-agent --pretty-logs evolve --dry-run
report:  ; . .venv/bin/activate && trading-agent report
check:   ; . .venv/bin/activate && trading-agent check
docker:  ; docker compose up -d --build
