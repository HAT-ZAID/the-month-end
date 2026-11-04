# The Month-End runtime image.
#
#   docker build -t month-end:test .
#   docker run --rm month-end:test --help          # usage
#   docker run --rm month-end:test                 # deterministic batch run
#   docker run --rm month-end:test --agent --agent-limit 3   # agentic batch
#
# The batch writes /app/output.csv inside the container; mount it to keep it:
#   docker run --rm -v "${PWD}/output.csv:/app/output.csv" month-end:test
#
# No provider is configured by default: the image runs fully offline and needs
# no API key.  Pass Kilo/OpenRouter variables with `docker run -e ...` to opt in.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Tesseract keeps the optional image-evidence path working inside the image;
# without a binary the pipeline fails those rows closed with OCR_UNAVAILABLE.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so a source edit does not invalidate the install layer.
COPY requirements.txt requirements-agent.txt ./
RUN python -m pip install --no-cache-dir \
        -r requirements.txt \
        -r requirements-agent.txt

# Application code, the dataset it reads, and the entry points documented in the
# README.  Nothing here is generated at build time.
COPY code ./code
COPY mock_dataset ./mock_dataset
COPY evaluation ./evaluation
COPY tools ./tools
COPY tests ./tests
COPY README.md problem_statement.md ./

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin buywait \
    && mkdir -p /app/traces \
    && chown -R buywait:buywait /app

USER buywait

# The entrypoint takes the CLI flags directly: `docker run IMAGE` runs the
# deterministic batch, `docker run IMAGE --help` prints usage and writes nothing.
ENTRYPOINT ["python", "-m", "code.main"]
