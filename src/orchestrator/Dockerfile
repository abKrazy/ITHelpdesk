# IT Helpdesk Orchestrator — Foundry Hosted Agent container.
#
# Built server-side by `az acr build` (no local Docker daemon needed) and
# registered as a Foundry Hosted Agent via the Foundry SDK (see
# scripts/postprovision.py -> create_hosted_orchestrator). Serves the OpenAI
# Responses protocol on port 8088 via agent-framework-foundry-hosting.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8088

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./

EXPOSE 8088

CMD ["python", "main.py"]
