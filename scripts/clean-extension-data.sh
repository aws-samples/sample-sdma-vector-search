#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# ==============================================================================
# SDMA Vector Search Extension - Clean Extension Data
#
# Removes Extension-specific data:
#   - S3: config/ (rendering/tagging configs)
#   - ECR Repository (Blender image)
#   - Legacy /aws/lambda/<function> log groups (see note below)
#
# The DynamoDB vector table and the current log groups are managed by the
# CloudFormation stack and are removed by ./scripts/uninstall.sh.
#
# WARNING: This is destructive and cannot be undone.
#
# Usage: ./scripts/clean-extension-data.sh
# ==============================================================================

set +e  # Don't exit on error — deletions are idempotent
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/_common.sh"

while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help) print_help "$0"; exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

print_header "Clean Extension Data"

REGION=$(get_region)
ENV=$(get_environment)
S3_BUCKET=$(get_sdma_s3_bucket 2>/dev/null || echo "")

# Deployments that predate stack-managed log groups left Lambda's auto-created
# /aws/lambda/<function> groups behind. They have no retention policy, are no
# longer written to, and are not owned by the stack, so nothing else removes
# them.
LEGACY_LOG_GROUPS=(
    "/aws/lambda/ai-tag-generation-$ENV"
    "/aws/lambda/vector-search-api-$ENV"
    "/aws/lambda/blender-render-$ENV"
    "/aws/lambda/prepare-render-$ENV"
    "/aws/lambda/finalize-render-$ENV"
)

echo ""
echo -e "${RED}WARNING: This permanently deletes:${NC}"
echo "  - S3: config/ and assets/ left in SDMA's bucket by earlier versions"
echo "  - ECR repository (Blender container image)"
echo "  - Legacy /aws/lambda/<function>-$ENV log groups, if any remain"
echo ""
echo "The Extension's own data bucket, the DynamoDB tables and the current log"
echo "groups are deleted by ./scripts/uninstall.sh."
echo ""
read -p "Type 'yes' to confirm: " RESPONSE
[ "$RESPONSE" != "yes" ] && { echo "Aborted."; exit 0; }

# ── 1. S3 data ───────────────────────────────────────────────────────────────
echo ""
echo "[1/3] Deleting Extension data left in SDMA's bucket..."
# Earlier versions wrote the Extension's configs and per-asset intermediates
# into SDMA's asset bucket. They now live in a bucket this stack owns, which
# uninstall.sh removes with the stack -- but an environment upgraded from an
# earlier version still has the old copies here.
if [ -n "$S3_BUCKET" ]; then
    for PREFIX in config/ assets/; do
        if aws s3 ls "s3://$S3_BUCKET/$PREFIX" --region "$REGION" >/dev/null 2>&1; then
            aws s3 rm "s3://$S3_BUCKET/$PREFIX" --recursive --region "$REGION" >/dev/null 2>&1 && \
                echo "  Removed $PREFIX" || echo -e "  ${YELLOW}Failed to remove $PREFIX${NC}"
        else
            echo "  $PREFIX not present"
        fi
    done
else
    echo "  SDMA S3 bucket not found, skipping"
fi

# ── 2. ECR Repository ────────────────────────────────────────────────────────
echo ""
echo "[2/3] Deleting ECR repository..."
aws ecr delete-repository --repository-name "sdma-blender-render-$ENV" --force --region "$REGION" 2>/dev/null && \
    echo "  Deleted ECR repo: sdma-blender-render-$ENV" || echo "  No ECR repo found"

# ── 3. Legacy log groups ─────────────────────────────────────────────────────
echo ""
echo "[3/3] Deleting legacy Lambda log groups..."
FOUND_LEGACY=0
for LG in "${LEGACY_LOG_GROUPS[@]}"; do
    # Only delete an exact name match, so the stack-managed groups under
    # /aws/lambda/sdma-vector-search/ are never touched.
    EXISTS=$(aws logs describe-log-groups --log-group-name-prefix "$LG" --region "$REGION" \
        --query "logGroups[?logGroupName=='$LG'].logGroupName" --output text 2>/dev/null)
    if [ -n "$EXISTS" ] && [ "$EXISTS" != "None" ]; then
        FOUND_LEGACY=1
        aws logs delete-log-group --log-group-name "$LG" --region "$REGION" 2>/dev/null && \
            echo "  Deleted: $LG" || echo "  Failed to delete: $LG"
    fi
done
[ "$FOUND_LEGACY" -eq 0 ] && echo "  None found"

echo ""
echo "=============================================="
echo -e "${GREEN}Extension data cleaned!${NC}"
echo ""
echo "Next: ./scripts/clean-sdma-resources.sh  (remove SDMA assets/connector/template)"
echo "=============================================="
