#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# ==============================================================================
# SDMA Vector Search Extension - Clean SDMA Resources
#
# Removes Extension-related SDMA resources (assets, templates, connectors,
# projects, IAM policy). Idempotent: safe to re-run if previous run failed.
#
# Usage: ./scripts/clean-sdma-resources.sh [OPTIONS]
#
# Options:
#   -P, --parallel N   Number of parallel asset deletions (default: 10)
#   -h, --help         Show this help
# ==============================================================================

set +e  # Don't exit on error — deletions are idempotent
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/_common.sh"

# Individual deletions are retried and non-fatal, but a run that gave up on any
# of them has not cleaned the resources it reports on. Collect that here so the
# exit status says so -- otherwise uninstall.sh runs next against resources that
# still exist and CloudFormation fails less obviously.
FAILED=0

PARALLEL=10

while [[ $# -gt 0 ]]; do
    case $1 in
        -P|--parallel) PARALLEL="$2"; shift 2 ;;
        -h|--help) print_help "$0"; exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

print_header "Clean SDMA Resources"

# Discovered rather than configured, and needed to enumerate assets through the
# API because the CLI cannot page. Deliberately after option parsing: --help
# must work without a deployed SDMA stack, and putting this first made it exit 1
# on a torn-down environment.
SDMA_API_ENDPOINT=$(get_sdma_api_endpoint)
SDMA_LIBRARY_ID=$(get_sdma_library_id)
if [ -z "$SDMA_API_ENDPOINT" ] || [ -z "$SDMA_LIBRARY_ID" ]; then
    echo -e "${RED}Could not discover the SDMA API endpoint or library id.${NC}"
    echo "Is the SpatialDataManagement stack deployed in this region?"
    exit 1
fi

# No region needed: every operation goes through the SDMA CLI, which resolves it
# from the profile. The AWS CLI call that needed it read SDMA's own DynamoDB
# table, which this script no longer does.
CONNECTOR_NAME="LambdaRenderPipeline"
TEMPLATE_NAME="Render Pipeline"

# ── 1. Check auth ────────────────────────────────────────────────────────────
echo ""
echo "[1/6] Checking CLI authentication..."
if ! spatial-data-mgmt auth status 2>/dev/null | grep -q "AUTHENTICATED"; then
    echo -e "  ${RED}Not authenticated. Run: spatial-data-mgmt auth login${NC}"
    exit 1
fi
# Verify token is still valid with an actual API call
if ! spatial-data-mgmt library list --output json >/dev/null 2>&1; then
    echo -e "  ${RED}Token expired. Run: spatial-data-mgmt auth login${NC}"
    exit 1
fi
echo -e "  ${GREEN}Authenticated${NC}"

# ── 2. Discover resources ────────────────────────────────────────────────────
echo ""
echo "[2/6] Discovering Extension resources..."
CONNECTORS_JSON=$(spatial-data-mgmt connector list --output json 2>/dev/null)
if echo "$CONNECTORS_JSON" | jq -e 'type == "array"' >/dev/null 2>&1; then
    CONNECTOR_ID=$(echo "$CONNECTORS_JSON" | jq -r ".[] | select(.connectorName==\"$CONNECTOR_NAME\") | .connectorId // empty")
else
    CONNECTOR_ID=""
fi

TEMPLATES_JSON=$(spatial-data-mgmt asset-template list --output json 2>/dev/null)
if echo "$TEMPLATES_JSON" | jq -e 'type == "array"' >/dev/null 2>&1; then
    TEMPLATE_ID=$(echo "$TEMPLATES_JSON" | jq -r ".[] | select(.assetTemplateName==\"$TEMPLATE_NAME\") | .assetTemplateId // empty")
else
    TEMPLATE_ID=""
fi

PROJECT_IDS=""
if [ -n "$TEMPLATE_ID" ]; then
    PROJECTS_JSON=$(spatial-data-mgmt project list --output json 2>/dev/null)
    if echo "$PROJECTS_JSON" | jq -e 'type == "array"' >/dev/null 2>&1; then
        PROJECT_IDS=$(echo "$PROJECTS_JSON" | jq -r ".[] | select(.permittedTemplateIds != null) | select(.permittedTemplateIds[] == \"$TEMPLATE_ID\") | .projectId")
    fi
fi

echo "  Connector: ${CONNECTOR_ID:-not found}"
echo "  Template:  ${TEMPLATE_ID:-not found}"
echo "  Projects:  ${PROJECT_IDS:-none}"

# ── 3. Delete assets (parallel) ──────────────────────────────────────────────
echo ""
echo "[3/6] Deleting assets (${PARALLEL} parallel workers)..."

for PROJECT_ID in $PROJECT_IDS; do
    [ -z "$PROJECT_ID" ] && continue

    # Enumerate through the API rather than `spatial-data-mgmt asset list`. The
    # CLI fetches one page and ignores the nextToken, and because the API applies
    # maxResults *before* filtering out assets in a transient state, that page
    # comes back empty once the first batch is PENDING_DELETE -- while later
    # pages still hold assets. Relying on the CLI left assets behind and the
    # project delete then failed with "still contains assets".
    #
    # This does not read SDMA's DynamoDB tables; list-assets.py walks the same
    # REST API the Extension's Lambda functions use.
    TOTAL=0
    for _ in $(seq 1 20); do
        if ! ASSET_IDS=$(python3 "$SCRIPT_DIR/lib/list-assets.py" \
                "$SDMA_API_ENDPOINT" "$SDMA_LIBRARY_ID" "$PROJECT_ID" 2>/dev/null); then
            echo -e "  ${RED}Could not list assets for $PROJECT_ID${NC}"
            FAILED=1
            break
        fi
        [ -z "$(echo "$ASSET_IDS" | tr -d '[:space:]')" ] && break

        COUNT=$(echo "$ASSET_IDS" | grep -c .)
        TOTAL=$((TOTAL + COUNT))
        echo "  Project $PROJECT_ID: deleting $COUNT assets..."
        echo "$ASSET_IDS" | xargs -P "$PARALLEL" -I{} \
            spatial-data-mgmt asset delete --asset-id {} --project-id "$PROJECT_ID" --yes 2>/dev/null
        # xargs exits non-zero if any invocation did. Record it and keep going:
        # the template delete below retries for exactly this reason.
        if [ "${PIPESTATUS[1]}" -ne 0 ]; then
            echo -e "  ${RED}Some assets could not be deleted${NC}"
            FAILED=1
            break
        fi
    done

    if [ "$TOTAL" -eq 0 ]; then
        echo "  Project $PROJECT_ID: no assets"
    else
        echo "  Project $PROJECT_ID: $TOTAL deleted"
    fi
done

if [ -n "$PROJECT_IDS" ]; then
    echo "  Waiting 30s for async deletions..."
    sleep 30
fi

# ── 4. Delete template (with retry) ──────────────────────────────────────────
echo ""
echo "[4/6] Deleting asset template..."
if [ -n "$TEMPLATE_ID" ]; then
    for i in 1 2 3 4 5; do
        spatial-data-mgmt asset-template delete --template-id "$TEMPLATE_ID" --yes 2>/dev/null && \
            { echo -e "  ${GREEN}Deleted: $TEMPLATE_ID${NC}"; break; }
        [ "$i" -eq 5 ] && { echo -e "  ${RED}Failed after 5 attempts${NC}"; FAILED=1; break; }
        echo "  Attempt $i failed (assets still pending). Retrying in 30s..."
        sleep 30
    done
else
    echo "  Nothing to delete"
fi

# ── 5. Delete connector (with retry) ─────────────────────────────────────────
echo ""
echo "[5/6] Deleting connector..."
if [ -n "$CONNECTOR_ID" ]; then
    for i in 1 2 3; do
        spatial-data-mgmt connector delete --connector-id "$CONNECTOR_ID" --yes 2>/dev/null && \
            { echo -e "  ${GREEN}Deleted: $CONNECTOR_ID${NC}"; break; }
        [ "$i" -eq 3 ] && { echo -e "  ${RED}Failed after 3 attempts${NC}"; FAILED=1; break; }
        echo "  Attempt $i failed (template may still exist). Retrying in 10s..."
        sleep 10
    done
else
    echo "  Nothing to delete"
fi

# ── 6. Delete projects (with retry) & IAM policy ────────────────────────────
echo ""
echo "[6/6] Deleting projects and IAM policy..."

for PROJECT_ID in $PROJECT_IDS; do
    [ -z "$PROJECT_ID" ] && continue
    for i in 1 2 3 4 5; do
        spatial-data-mgmt project delete --project-id "$PROJECT_ID" --yes 2>/dev/null && \
            { echo "  Deleted project: $PROJECT_ID"; break; }
        [ "$i" -eq 5 ] && { echo "  Failed to delete project: $PROJECT_ID"; FAILED=1; break; }
        echo "  Attempt $i failed. Retrying in 30s..."
        sleep 30
    done
done

# Remove IAM policy from SDMA connector role
CONNECTOR_ROLE=$(get_sdma_connector_role_arn 2>/dev/null || echo "")
if [ -n "$CONNECTOR_ROLE" ]; then
    ROLE_NAME=$(echo "$CONNECTOR_ROLE" | sed 's/.*\///')
    aws iam delete-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-name "ExtensionLambdaInvokePolicy" 2>/dev/null && \
        echo "  Removed IAM policy from $ROLE_NAME" || echo "  No IAM policy to remove"
fi

# Reset CLI defaults
spatial-data-mgmt config set defaults.project_id "" 2>/dev/null

echo ""
echo "=============================================="
if [ "$FAILED" -ne 0 ]; then
    echo -e "${RED}Some SDMA resources could not be deleted.${NC}"
    echo ""
    echo "Re-run this script: the deletions are idempotent, and SDMA rejects a"
    echo "delete while dependent resources are still being removed."
    echo "=============================================="
    exit 1
fi
echo -e "${GREEN}SDMA resources cleaned!${NC}"
echo ""
echo "Next: ./scripts/uninstall.sh  (delete CloudFormation stack)"
echo "=============================================="
