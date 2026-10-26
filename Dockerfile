FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev
# The venv's binary, not `uv run`, so the server is PID 1 and gets docker's SIGTERM.
ENTRYPOINT ["/app/.venv/bin/corral", "server", "--db", "/data/corral.sqlite"]
CMD ["--host", "0.0.0.0"]
