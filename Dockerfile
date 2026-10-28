FROM python:3.12-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/
# The image's Python, never one uv downloads.
ENV UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev
# A fixed uid, so the host directory mounted at /data can be owned by it.
RUN groupadd --system --gid 8700 corral && useradd --system --uid 8700 --gid corral --no-create-home corral
USER corral
# The venv's binary, not `uv run`, so the server is PID 1 and gets docker's SIGTERM.
ENTRYPOINT ["/app/.venv/bin/corral", "server", "--db", "/data/corral.sqlite"]
CMD ["--host", "0.0.0.0"]
