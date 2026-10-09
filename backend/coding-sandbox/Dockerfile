FROM python:3.12-slim

# Maintainer-controlled dependencies only. Imported requirements/pyproject files
# are never installed automatically by the application.
RUN pip install --no-cache-dir \
    pytest==8.3.5 \
    fastapi==0.141.1 \
    httpx==0.28.1 \
    && mkdir -p /workspace \
    && chown 10001:10001 /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1

USER 10001:10001
WORKDIR /workspace
CMD ["python", "--version"]
