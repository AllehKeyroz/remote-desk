FROM python:3.12-slim

WORKDIR /app

COPY relay.py .
COPY packages ./packages

RUN pip install --no-cache-dir aiohttp

EXPOSE 8765

CMD ["python", "relay.py", "--host", "0.0.0.0", "--port", "8765"]
