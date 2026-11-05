FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data \
    && chown app:app /data
USER app

ENV REPLENISHMENT_DATA_DIR=/data
ENV REPLENISHMENT_LLM=stub
EXPOSE 8000

CMD ["python", "-m", "replenishment.cli", "serve", "--host", "0.0.0.0", "--port", "8000", "--bootstrap"]
