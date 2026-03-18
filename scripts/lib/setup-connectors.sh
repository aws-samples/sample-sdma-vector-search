#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# ==============================================================================
# SDMA Connector Setup Script
#
# Creates the LambdaRenderPipeline connector using SDMA CLI.
# Requires: spatial-data-mgmt CLI installed and authenticated
#
# Usage:
#   ./scripts/lib/setup-connectors.sh [--dry-run] [--delete]
#                                     [--region REGION] [--stack-name STACK]
#
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONNECTORS_DIR="$SCRIPT_DIR/connectors"
source "$SCRIPT_DIR/_common.sh"

# Default values
DRY_RUN=false
DELETE=false
REGION=$(get_region)
STACK_NAME=$(get_stack_name)
CONNECTOR_NAME="LambdaRenderPipeline"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --delete)
            DELETE=true
            shift
            ;;
        --region)
            REGION="$2"
            shift 2
            ;;
        --stack-name)
            STACK_NAME="$2"
            shift 2
            ;;
        --help|-h)
            print_help "$0"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

echo "=============================================="
echo "SDMA Connector Setup"
echo "=============================================="

# Check CLI authentication
echo ""
echo "[1/4] Checking CLI authentication..."
if ! spatial-data-mgmt auth status 2>/dev/null | grep -q "AUTHENTICATED"; then
    echo "Error: SDMA CLI not authenticated"
    echo "Run: spatial-data-mgmt auth login"
    exit 1
fi
echo "  CLI authenticated"

# Get Lambda ARN from CloudFormation
echo ""
echo "[2/4] Getting Lambda ARN from CloudFormation..."

PREPARE_RENDER_ARN=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`PrepareRenderFunctionArn`].OutputValue' \
    --output text 2>/dev/null || echo "")

if [ -z "$PREPARE_RENDER_ARN" ] || [ "$PREPARE_RENDER_ARN" = "None" ]; then
    echo "Error: Could not find PrepareRenderFunctionArn in stack $STACK_NAME"
    exit 1
fi

echo "  Prepare Render: $PREPARE_RENDER_ARN"

# Handle delete
if [ "$DELETE" = true ]; then
    echo ""
    echo "[3/4] Deleting existing connector..."

    CONNECTOR_ID=$(spatial-data-mgmt connector list --output json 2>/dev/null | \
        jq -r ".[] | select(.connectorName==\"$CONNECTOR_NAME\") | .connectorId" 2>/dev/null || echo "")

    if [ -n "$CONNECTOR_ID" ] && [ "$CONNECTOR_ID" != "null" ]; then
        if [ "$DRY_RUN" = true ]; then
            echo "  [DRY RUN] Would delete: $CONNECTOR_ID"
        else
            spatial-data-mgmt connector delete --connector-id "$CONNECTOR_ID" --yes
            echo "  Deleted: $CONNECTOR_ID"
        fi
    else
        echo "  No existing connector found"
    fi

    echo ""
    echo "Done!"
    exit 0
fi

# Generate connector config
echo ""
echo "[3/4] Generating connector config..."

# The template deliberately carries no top-level "description". SDMA v1.6.0
# rejects it with "Parameter description is not supported" and a 400, which
# breaks connector creation outright. A description on each trigger inside
# connectorConfig is still accepted, so only the connector-level one was
# removed. JSON takes no comments, hence the note here.
TEMPLATE_FILE="$CONNECTORS_DIR/lambda_render_pipeline.template.json"
CONFIG_FILE="/tmp/connector_config_$$.json"

if [ ! -f "$TEMPLATE_FILE" ]; then
    echo "Error: Template file not found: $TEMPLATE_FILE"
    exit 1
fi

# Replace placeholders
sed -e "s|\${PREPARE_RENDER_ARN}|$PREPARE_RENDER_ARN|g" \
    -e "s|\${AWS_REGION}|$REGION|g" \
    "$TEMPLATE_FILE" > "$CONFIG_FILE"

echo "  Config generated: $CONFIG_FILE"

if [ "$DRY_RUN" = true ]; then
    echo ""
    echo "[DRY RUN] Would create connector with config:"
    cat "$CONFIG_FILE" | jq .
    rm -f "$CONFIG_FILE"
    exit 0
fi

# Check if connector already exists
echo ""
echo "[4/4] Creating/updating connector..."

EXISTING_ID=$(spatial-data-mgmt connector list --output json 2>/dev/null | \
    jq -r ".[] | select(.connectorName==\"$CONNECTOR_NAME\") | .connectorId" 2>/dev/null || echo "")

if [ -n "$EXISTING_ID" ] && [ "$EXISTING_ID" != "null" ]; then
    echo "  Connector already exists: $EXISTING_ID"
    echo "  Updating..."
    spatial-data-mgmt connector update \
        --connector-id "$EXISTING_ID" \
        --connector-config-file "$CONFIG_FILE" \
        --yes
    echo "  Updated successfully"
else
    echo "  Creating new connector..."
    spatial-data-mgmt connector create \
        --connector-name "$CONNECTOR_NAME" \
        --connector-config-file "$CONFIG_FILE" \
        --yes
    echo "  Created successfully"
fi

# Cleanup
rm -f "$CONFIG_FILE"

echo ""
echo "=============================================="
echo "Connector setup complete!"
echo "=============================================="
echo ""
echo "Verify with: spatial-data-mgmt connector list"
