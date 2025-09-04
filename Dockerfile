FROM ghcr.io/astral-sh/uv:python3.13-trixie

# overwrite the some of the uv metadata
LABEL org.opencontainers.image.title="safer-streets-apps"
LABEL org.opencontainers.image.description="Interactive web apps created by the Safer Streets @ Leeds team"
LABEL org.opencontainers.image.url="https://github.com/safer-streets/safer-streets-apps"

# Install the project into `/app`
WORKDIR /app

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy from the cache instead of linking since it's a mounted volume
ENV UV_LINK_MODE=copy

# Ensure installed tools can be executed out of the box
ENV UV_TOOL_BIN_DIR=/usr/local/bin

# ADD the main dependency
ADD safer_streets_core-0.1.0-py3-none-any.whl /app

# Install core and streamlit should satisfy the dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv --python 3.13 && \
    uv pip install safer_streets_core-0.1.0-py3-none-any.whl streamlit && \
    rm -f safer_streets_core-0.1.0-py3-none-any.whl

COPY . /app

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"

# Reset the entrypoint, don't invoke `uv`
ENTRYPOINT []

ENV SAFER_STREETS_DATA_DIR=/app/data-local

# Uses `--server.address 0.0.0.0` to allow access from outside the container
CMD ["streamlit", "run", "src/safer_streets_apps/streamlit/Main.py", "--server.port=8501", "--server.address=0.0.0.0"]