# Remote Factory — Dev Instructions

## Build
```bash
uv sync
```

## Test
```bash
pytest -v
```

## Lint
```bash
ruff check .
```

## Style
- Python 3.11+ (use `X | Y` unions, not `Union[X, Y]`)
- Snake_case everywhere
- 100 char line length (enforced by ruff)
- All Pydantic models use `ConfigDict(strict=True)`
- Async/await by default
