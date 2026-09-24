# Contributing

## Local setup

1. Copy `.env.example` to `.env` and fill in the required model key.
2. Start the stack with `docker compose up --build`.
3. Open `http://127.0.0.1:5500`.

## Before submitting a change

Run the syntax and unit checks:

```bash
python -m compileall -q app scripts
pytest -q tests/test_state_transitions_unit.py
```

Keep pull requests focused, update tests for behavior changes, and never add
credentials, logs, local databases, generated files, or personal documents.
