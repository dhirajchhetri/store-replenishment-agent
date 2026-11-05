FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/program
COPY pyproject.toml README.md /opt/program/
COPY src /opt/program/src
RUN pip install --no-cache-dir /opt/program
COPY sagemaker /opt/program/sagemaker

ENV PYTHONUNBUFFERED=1
ENV SAGEMAKER_PROGRAM=serve.py
EXPOSE 8080

WORKDIR /opt/program/sagemaker
ENTRYPOINT ["python", "serve.py"]
