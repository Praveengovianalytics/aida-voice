#!/usr/bin/env bash
# AIDA Voice — Azure Container Apps Deployment Script
# Usage: ./scripts/deploy_azure.sh
set -euo pipefail

# ─── Configuration ──────────────────────────────────────────────────────
RG="aida-rg"
LOCATION="eastus"
ACR_NAME="aidaacrpoc"
IMAGE_NAME="aida-voice"
IMAGE_TAG="0.1.0"
FULL_IMAGE="${ACR_NAME}.azurecr.io/${IMAGE_NAME}:${IMAGE_TAG}"
CONTAINER_APP_NAME="aida-voice"
CONTAINER_ENV="aida-env-poc"
IDENTITY_NAME="aida-bot-identity"
KV_NAME="aida-kv-poc"
OPENAI_NAME="aida-openai-poc"
ACS_NAME="aida-acs-poc"

echo "============================================"
echo "  AIDA Voice — Azure Deployment"
echo "  $(date)"
echo "============================================"

# ─── Step 1: Verify Azure login ────────────────────────────────────────
echo ""
echo ">>> Step 1: Verify Azure login"
az account show --query "{subscription: name, user: user.name}" --output table
echo "Azure login verified"

# ─── Step 2: Build Docker image ────────────────────────────────────────
echo ""
echo ">>> Step 2: Build Docker image"
cd "$(dirname "$0")/.."
docker build --platform linux/amd64 --build-arg APP_VERSION=${IMAGE_TAG} -t ${FULL_IMAGE} .
echo "Docker image built: ${FULL_IMAGE}"

# ─── Step 3: Push to ACR ───────────────────────────────────────────────
echo ""
echo ">>> Step 3: Push to Azure Container Registry"
az acr login --name ${ACR_NAME}
docker push ${FULL_IMAGE}
docker tag ${FULL_IMAGE} ${ACR_NAME}.azurecr.io/${IMAGE_NAME}:latest
docker push ${ACR_NAME}.azurecr.io/${IMAGE_NAME}:latest
echo "Image pushed to ACR"

# ─── Step 4: Collect endpoints ──────────────────────────────────────────
echo ""
echo ">>> Step 4: Collecting resource endpoints"
OPENAI_ENDPOINT=$(az cognitiveservices account show --name ${OPENAI_NAME} -g ${RG} --query properties.endpoint -o tsv)
IDENTITY_ID=$(az identity show --name ${IDENTITY_NAME} -g ${RG} --query id -o tsv)
IDENTITY_CLIENT_ID=$(az identity show --name ${IDENTITY_NAME} -g ${RG} --query clientId -o tsv)
APPINSIGHTS_CS=$(az monitor app-insights component show -g ${RG} --query "[0].connectionString" -o tsv 2>/dev/null || echo "")

# ACS
ACS_CONNECTION_STRING=$(az communication list-key --name ${ACS_NAME} -g ${RG} --query primaryConnectionString -o tsv 2>/dev/null || echo "")
ACS_RESOURCE_ID=$(az communication show --name ${ACS_NAME} -g ${RG} --query "immutableResourceId" -o tsv 2>/dev/null || echo "")

# ACS callback host (voice service is external for WebSocket)
EXISTING_FQDN=$(az containerapp show --name ${CONTAINER_APP_NAME} -g ${RG} --query properties.configuration.ingress.fqdn -o tsv 2>/dev/null || echo "")
ACS_CALLBACK_HOST="https://${EXISTING_FQDN:-aida-voice.victoriousstone-0926fdf3.eastus.azurecontainerapps.io}"

# Media Bridge (.NET VM)
MEDIA_BRIDGE_URL="http://aida-media-bridge.eastus.cloudapp.azure.com:8080"

# Inter-service URLs (Container Apps internal FQDNs)
DATA_SERVICE_URL="https://aida-data.internal.victoriousstone-0926fdf3.eastus.azurecontainerapps.io"
INTELLIGENCE_SERVICE_URL="https://aida-intelligence.internal.victoriousstone-0926fdf3.eastus.azurecontainerapps.io"

echo "  OpenAI:       ${OPENAI_ENDPOINT}"
echo "  ACS:          ${ACS_CONNECTION_STRING:0:40}..."
echo "  ACS ID:       ${ACS_RESOURCE_ID}"
echo "  Callback:     ${ACS_CALLBACK_HOST}"
echo "  Media Bridge: ${MEDIA_BRIDGE_URL}"
echo "  Data SVC:     ${DATA_SERVICE_URL}"
echo "  Intel SVC:    ${INTELLIGENCE_SERVICE_URL}"
echo "Endpoints collected"

# ─── Step 5: Deploy to Container Apps ───────────────────────────────────
echo ""
echo ">>> Step 5: Deploy to Azure Container Apps"

APP_EXISTS=$(az containerapp show --name ${CONTAINER_APP_NAME} -g ${RG} --query name -o tsv 2>/dev/null || echo "")

if [ -z "$APP_EXISTS" ]; then
    echo "  Creating new Container App..."
    az containerapp create \
        --name ${CONTAINER_APP_NAME} \
        --resource-group ${RG} \
        --environment ${CONTAINER_ENV} \
        --image ${FULL_IMAGE} \
        --registry-server ${ACR_NAME}.azurecr.io \
        --registry-identity "${IDENTITY_ID}" \
        --user-assigned "${IDENTITY_ID}" \
        --target-port 3979 \
        --ingress external \
        --min-replicas 0 \
        --max-replicas 3 \
        --cpu 1.0 \
        --memory 2.0Gi \
        --env-vars \
            KEY_VAULT_NAME="${KV_NAME}" \
            AZURE_CLIENT_ID="${IDENTITY_CLIENT_ID}" \
            ACS_CONNECTION_STRING="${ACS_CONNECTION_STRING}" \
            ACS_RESOURCE_ID="${ACS_RESOURCE_ID}" \
            ACS_CALLBACK_HOST="${ACS_CALLBACK_HOST}" \
            AZURE_OPENAI_ENDPOINT="${OPENAI_ENDPOINT}" \
            AZURE_OPENAI_CHAT_DEPLOYMENT="gpt-4o" \
            AZURE_OPENAI_API_VERSION="2024-10-21" \
            AZURE_OPENAI_REALTIME_DEPLOYMENT="gpt-realtime" \
            AZURE_OPENAI_REALTIME_API_VERSION="2025-04-01-preview" \
            VOICE_MODE="realtime" \
            AIDA_VOICE="sage" \
            AIDA_VAD_THRESHOLD="0.4" \
            AIDA_SILENCE_DURATION_MS="300" \
            AIDA_PREFIX_PADDING_MS="300" \
            AIDA_MAX_RESPONSE_TOKENS="4096" \
            AIDA_VOICE_TEMPERATURE="0.6" \
            VOICE_FALLBACK_ENABLED="true" \
            MEDIA_BRIDGE_URL="${MEDIA_BRIDGE_URL}" \
            GRAPH_TENANT_ID="98fb8319-b410-4961-ab13-adb406303873" \
            GRAPH_CLIENT_ID="4ec1b314-9024-4314-ad11-7a53e2040f38" \
            DATA_SERVICE_URL="${DATA_SERVICE_URL}" \
            INTELLIGENCE_SERVICE_URL="${INTELLIGENCE_SERVICE_URL}" \
            EMPLOYEE_NAME="Praveen Govindaraj" \
            EMPLOYEE_ID="emp-001" \
            COMPANY_NAME="NCS" \
            JOB_TITLE="Engineer" \
            PORT="3979" \
            APPINSIGHTS_CONNECTION_STRING="${APPINSIGHTS_CS}"
else
    echo "  Updating existing Container App..."
    az containerapp update \
        --name ${CONTAINER_APP_NAME} \
        --resource-group ${RG} \
        --image ${FULL_IMAGE} \
        --min-replicas 0 \
        --max-replicas 3 \
        --set-env-vars \
            KEY_VAULT_NAME="${KV_NAME}" \
            AZURE_CLIENT_ID="${IDENTITY_CLIENT_ID}" \
            ACS_CONNECTION_STRING="${ACS_CONNECTION_STRING}" \
            ACS_RESOURCE_ID="${ACS_RESOURCE_ID}" \
            ACS_CALLBACK_HOST="${ACS_CALLBACK_HOST}" \
            AZURE_OPENAI_ENDPOINT="${OPENAI_ENDPOINT}" \
            AZURE_OPENAI_CHAT_DEPLOYMENT="gpt-4o" \
            AZURE_OPENAI_API_VERSION="2024-10-21" \
            AZURE_OPENAI_REALTIME_DEPLOYMENT="gpt-realtime" \
            AZURE_OPENAI_REALTIME_API_VERSION="2025-04-01-preview" \
            VOICE_MODE="realtime" \
            AIDA_VOICE="sage" \
            AIDA_VAD_THRESHOLD="0.4" \
            AIDA_SILENCE_DURATION_MS="300" \
            AIDA_PREFIX_PADDING_MS="300" \
            AIDA_MAX_RESPONSE_TOKENS="4096" \
            AIDA_VOICE_TEMPERATURE="0.6" \
            VOICE_FALLBACK_ENABLED="true" \
            MEDIA_BRIDGE_URL="${MEDIA_BRIDGE_URL}" \
            GRAPH_TENANT_ID="98fb8319-b410-4961-ab13-adb406303873" \
            GRAPH_CLIENT_ID="4ec1b314-9024-4314-ad11-7a53e2040f38" \
            DATA_SERVICE_URL="${DATA_SERVICE_URL}" \
            INTELLIGENCE_SERVICE_URL="${INTELLIGENCE_SERVICE_URL}" \
            EMPLOYEE_NAME="Praveen Govindaraj" \
            EMPLOYEE_ID="emp-001" \
            COMPANY_NAME="NCS" \
            JOB_TITLE="Engineer" \
            PORT="3979" \
            APPINSIGHTS_CONNECTION_STRING="${APPINSIGHTS_CS}"
fi

echo "Container App deployed"

# ─── Step 6: Health check ──────────────────────────────────────────────
echo ""
echo ">>> Step 6: Health check"
FQDN=$(az containerapp show --name ${CONTAINER_APP_NAME} -g ${RG} --query properties.configuration.ingress.fqdn -o tsv)
echo "  Container App FQDN: ${FQDN}"
echo "  Waiting 30s for container to start..."
sleep 30

HEALTH_URL="https://${FQDN}/health"
echo "  Checking: ${HEALTH_URL}"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${HEALTH_URL}" 2>/dev/null || echo "000")

if [ "$HTTP_CODE" = "200" ]; then
    echo "Health check passed (HTTP ${HTTP_CODE})"
    curl -s "${HEALTH_URL}" | python3 -m json.tool 2>/dev/null || true
else
    echo "WARNING: Health check returned HTTP ${HTTP_CODE}"
    echo "  Container may still be starting. Check logs with:"
    echo "  az containerapp logs show --name ${CONTAINER_APP_NAME} -g ${RG} --type console"
fi

# ─── Summary ────────────────────────────────────────────────────────────
echo ""
echo "============================================"
echo "  DEPLOYMENT COMPLETE — aida-voice"
echo "============================================"
echo ""
echo "  Container App: https://${FQDN}"
echo "  Health Check:  ${HEALTH_URL}"
echo "  WebSocket:     wss://${FQDN}/ws/audio"
echo ""
echo "  View logs:"
echo "  az containerapp logs show --name ${CONTAINER_APP_NAME} -g ${RG} --type console"
echo "============================================"
