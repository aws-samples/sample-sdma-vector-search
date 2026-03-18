#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# ==============================================================================
# SDMA Vector Search Extension - Deploy
#
# Full deployment: Blender image + SAM deploy + Connector
# Idempotent: Existing resources are skipped
#
# Usage:
#   ./scripts/deploy.sh [OPTIONS]
#
# Options:
#   -h, --help         Show this help
# ==============================================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/_common.sh"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help) print_help "$0"; exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

print_header "SDMA Vector Search Extension - Deploy"

EXTENSIONS_DIR=$(get_extensions_dir)
REGION=$(get_region)
STACK_NAME=$(get_stack_name)
ACCOUNT_ID=$(aws sts get-caller-identity --query 'Account' --output text)
TEMPLATE_FILE="$EXTENSIONS_DIR/infra/cloudformation/template.yaml"
CONFIG_FILE="$EXTENSIONS_DIR/infra/samconfig.toml"

# ── 1. Prerequisites ─────────────────────────────────────────────────────────
echo ""
echo "[1/7] Checking prerequisites..."
check_prerequisites
check_cli
check_docker
echo -e "  ${GREEN}All prerequisites met${NC}"

# ── 2. SDMA CLI Login ─────────────────────────────────────────────────────────
echo ""
echo "[2/7] Checking SDMA CLI..."
SDMA_API_ENDPOINT=$(get_sdma_api_endpoint)
if [ -n "$SDMA_API_ENDPOINT" ]; then
    spatial-data-mgmt config set defaults.api_endpoint_url "$SDMA_API_ENDPOINT" 2>/dev/null
fi
if ! spatial-data-mgmt library list --output json >/dev/null 2>&1; then
    echo "  SDMA CLI session expired. Logging in..."
    spatial-data-mgmt auth login
fi
echo -e "  ${GREEN}CLI authenticated${NC}"

# ── 3. Discover SDMA Resources ───────────────────────────────────────────────
echo ""
echo "[3/7] Discovering SDMA resources..."
COGNITO_POOL_ID=$(get_sdma_cognito_pool_id)
[ -z "$COGNITO_POOL_ID" ] && { echo -e "${RED}SDMA Cognito User Pool not found${NC}"; exit 1; }
COGNITO_POOL_ARN="arn:aws:cognito-idp:${REGION}:${ACCOUNT_ID}:userpool/${COGNITO_POOL_ID}"
S3_BUCKET=$(get_sdma_s3_bucket)
[ -z "$S3_BUCKET" ] && { echo -e "${RED}SDMA S3 bucket not found${NC}"; exit 1; }
CONNECTOR_ROLE=$(get_sdma_connector_role_arn)
SDMA_API_ENDPOINT=$(get_sdma_api_endpoint)
[ -z "$SDMA_API_ENDPOINT" ] && { echo -e "${RED}SDMA API endpoint not found${NC}"; exit 1; }
SDMA_LIBRARY_ID=$(get_sdma_library_id)
case "$SDMA_LIBRARY_ID" in
    "")         echo -e "${RED}SDMA library not found. Sign in via the Spatial Data Portal, then: spatial-data-mgmt auth login${NC}"; exit 1 ;;
    MULTIPLE)   echo -e "${RED}More than one SDMA library found. This Extension assumes a single library; pass SDMALibraryId explicitly.${NC}"; exit 1 ;;
esac
echo "  Cognito: $COGNITO_POOL_ID | S3: $S3_BUCKET"
echo "  Library: $SDMA_LIBRARY_ID"

# ── 4. Build Blender Image ───────────────────────────────────────────────────
echo ""
echo "[4/7] Building Blender Lambda image..."
BLENDER_IMAGE_TAG_FILE="$(mktemp -t blender_image_tag.XXXXXX)"
IMAGE_TAG_FILE="$BLENDER_IMAGE_TAG_FILE" "$SCRIPT_DIR/lib/build-image.sh"
# Deploy the immutable, digest-derived tag rather than "latest": an unchanged
# ImageUri makes CloudFormation skip the function, silently leaving the previous
# image in place.
BLENDER_IMAGE_TAG=$(cat "$BLENDER_IMAGE_TAG_FILE" 2>/dev/null || echo latest)
rm -f "$BLENDER_IMAGE_TAG_FILE"
echo "  Deploying image tag: $BLENDER_IMAGE_TAG"

# ── 5. SAM Build ─────────────────────────────────────────────────────────────
echo ""
echo "[5/7] Building with SAM..."
# Each function's Makefile copies the shared modules it imports (see
# BuildMethod: makefile in the template), so there is nothing to stage here.
cd "$EXTENSIONS_DIR"
rm -rf .aws-sam/build .aws-sam/deps
sam build --parallel -t "$TEMPLATE_FILE" --config-file "$CONFIG_FILE"

# ── 6. SAM Deploy ────────────────────────────────────────────────────────────
echo ""
echo "[6/7] Deploying with SAM..."
cd "$EXTENSIONS_DIR"
sam deploy \
    -t .aws-sam/build/template.yaml \
    --config-file "$CONFIG_FILE" \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --resolve-s3 --resolve-image-repos \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides \
        "Environment=$(get_environment)" \
        "SDMACognitoUserPoolArn=${COGNITO_POOL_ARN}" \
        "SDMAAssetBucketName=${S3_BUCKET}" \
        "SDMAConnectorRoleArn=${CONNECTOR_ROLE:-}" \
        "SDMAApiEndpoint=${SDMA_API_ENDPOINT}" \
        "SDMALibraryId=${SDMA_LIBRARY_ID}" \
        "BlenderImageTag=${BLENDER_IMAGE_TAG}" \
    --no-confirm-changeset --no-fail-on-empty-changeset

# Update SDMA connector permissions
if [ -n "$CONNECTOR_ROLE" ]; then
    echo ""
    echo "Updating SDMA connector permissions..."
    ROLE_NAME=$(echo "$CONNECTOR_ROLE" | sed 's/.*\///')
    AI_TAG_ARN=$(get_stack_output "AITagGenerationFunctionArn")
    SFN_ARN=$(get_stack_output "AssetProcessingStateMachineArn")
    PREPARE_RENDER_ARN=$(get_stack_output "PrepareRenderFunctionArn")
    POLICY_DOC=$(render_template "$EXTENSIONS_DIR/infra/iam/connector-invoke-policy.template.json" \
        AI_TAG_ARN "$AI_TAG_ARN" PREPARE_RENDER_ARN "$PREPARE_RENDER_ARN" SFN_ARN "$SFN_ARN")
    aws iam put-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-name "ExtensionLambdaInvokePolicy" \
        --policy-document "$POLICY_DOC" 2>/dev/null || true
fi

# ── 7. Connector & Template ──────────────────────────────────────────────────
echo ""
echo "[7/7] Checking SDMA connector & template..."
CONNECTOR_NAME="LambdaRenderPipeline"
CONNECTORS_JSON=$(spatial-data-mgmt connector list --output json 2>/dev/null)
CONNECTOR_ID=$(echo "$CONNECTORS_JSON" | jq -r ".[] | select(.connectorName==\"$CONNECTOR_NAME\") | .connectorId" 2>/dev/null || echo "")

if [ -n "$CONNECTOR_ID" ] && [ "$CONNECTOR_ID" != "null" ]; then
    echo -e "  ${GREEN}Connector exists: $CONNECTOR_ID${NC}"
else
    echo "  Creating connector..."
    "$SCRIPT_DIR/lib/setup-connectors.sh"
    CONNECTOR_ID=$(spatial-data-mgmt connector list --output json 2>/dev/null | \
        jq -r ".[] | select(.connectorName==\"$CONNECTOR_NAME\") | .connectorId" 2>/dev/null || echo "")
fi

# Asset template
TEMPLATE_NAME="Render Pipeline"
TEMPLATES_JSON=$(spatial-data-mgmt asset-template list --output json 2>/dev/null)
TEMPLATE_ID=$(echo "$TEMPLATES_JSON" | jq -r ".[] | select(.assetTemplateName==\"$TEMPLATE_NAME\") | .assetTemplateId" 2>/dev/null || echo "")

if [ -n "$TEMPLATE_ID" ] && [ "$TEMPLATE_ID" != "null" ]; then
    echo -e "  ${GREEN}Template exists: $TEMPLATE_ID${NC}"
elif [ -n "$CONNECTOR_ID" ]; then
    TEMPLATE_ID=$(spatial-data-mgmt asset-template create \
        --template-name "$TEMPLATE_NAME" \
        --template-config-json "{\"permittedConnectorIds\": [\"$CONNECTOR_ID\"]}" \
        --output json --yes 2>/dev/null | jq -r '.assetTemplateId // empty' 2>/dev/null || echo "")
    [ -n "$TEMPLATE_ID" ] && echo -e "  ${GREEN}Template created: $TEMPLATE_ID${NC}" || echo -e "  ${RED}Template creation failed${NC}"
fi

# ── Upload Configs ───────────────────────────────────────────────────────────
echo ""
echo "Uploading configs..."
echo "  Tagging config:"
"$SCRIPT_DIR/lib/upload-tagging-config.sh"
echo "  Rendering config:"
"$SCRIPT_DIR/lib/upload-rendering-config.sh"

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "=============================================="
echo -e "${GREEN}Deploy complete!${NC}"
echo "=============================================="
echo ""
echo "API Endpoint:"
get_stack_output "APIGatewayEndpoint"
echo ""
echo "Next: ./scripts/test-upload.sh <file.glb>"
