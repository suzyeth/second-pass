# The image has to carry two Python programs, not one: the server, and the
# mcp-clickhouse MCP server it spawns as a subprocess and talks to over stdio.
# Both come from requirements.txt, so there is nothing extra to install — but a
# slim base without Python on PATH would break the second one silently, with the
# server healthy and every query failing.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # The banner is noise in Cloud Logging; the per-query lines below it are the
    # record of what MCP actually executed, and those stay.
    FASTMCP_SHOW_CLI_BANNER=false \
    # ADK would otherwise try Vertex with application-default credentials.
    GOOGLE_GENAI_USE_VERTEXAI=FALSE

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py server.py ./
COPY web/ ./web/

# Cloud Run supplies PORT; 8080 is the default it uses and what server.py falls
# back to when run locally.
EXPOSE 8080
CMD ["python", "server.py"]
