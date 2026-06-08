# Build:
#   docker build -t praetor-cli .
#
# Run Praetor against a target repository mounted at /repo:
#   docker run --rm -it -v "$PWD:/repo" -w /repo praetor-cli praetor status
#   docker run --rm -it -v "$PWD:/repo" -w /repo praetor-cli praetor run --adapter claude
#
# Agent sandbox note:
#   This container is intended to be the trust boundary for coding-agent runs.
#   If an agent supports a permission bypass, run that bypass inside the
#   container, not on the host. For example:
#     docker run --rm -it -v "$PWD:/repo" -w /repo praetor-cli \
#       claude --dangerously-skip-permissions
#
# Agent CLIs:
#   Praetor shells out to agent binaries such as "claude" and, in future
#   versions, "codex". Install/authenticate those CLIs in a derived image or
#   bind-mount their config into this one as needed for your environment.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY praetor ./praetor

RUN pip install --no-cache-dir -e ".[dev]"

RUN mkdir -p /repo

CMD ["praetor", "--help"]
