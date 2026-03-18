#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# ==============================================================================
# SDMA Vector Search Extension - Uninstall
#
# Delete the CloudFormation stack (Lambda, API Gateway, DynamoDB vector table).
# SDMA data (assets, connectors, templates) is preserved.
#
# For data cleanup instructions, see README.md
#
# Usage: ./scripts/uninstall.sh [OPTIONS]
#
# Options:
#   -h, --help         Show this help
# ==============================================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/_common.sh"

while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help) print_help "$0"; exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

print_header "SDMA Vector Search Extension - Uninstall"

check_prerequisites

STACK_NAME=$(get_stack_name)
REGION=$(get_region)
EXTENSIONS_DIR=$(get_extensions_dir)
CONFIG_FILE="$EXTENSIONS_DIR/infra/samconfig.toml"

# Check if stack exists
echo ""
echo "Checking stack status..."
STACK_STATUS=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query "Stacks[0].StackStatus" \
    --output text 2>/dev/null || echo "NOT_FOUND")

if [ "$STACK_STATUS" = "NOT_FOUND" ]; then
    echo -e "  ${YELLOW}Stack '$STACK_NAME' not found. Nothing to delete.${NC}"
    exit 0
fi

echo -e "  Stack: ${YELLOW}$STACK_NAME${NC}"
echo -e "  Status: $STACK_STATUS"
echo -e "  Region: $REGION"

# Confirm
echo ""
echo -e "${YELLOW}This will delete the CloudFormation stack (Lambda functions, API Gateway, DynamoDB vector table, etc.)${NC}"
echo -e "${YELLOW}SDMA data (assets, connectors, templates) will be preserved.${NC}"
echo ""
read -p "Type 'yes' to confirm: " RESPONSE
[ "$RESPONSE" != "yes" ] && { echo "Aborted"; exit 1; }

# CloudFormation cannot delete a bucket that still has objects, and versioning
# is enabled on this one, so every version and delete marker has to go too.
echo ""
echo "Emptying the Extension data bucket..."
DATA_BUCKET=$(get_stack_output "ExtensionDataBucketName")
if [ -n "$DATA_BUCKET" ]; then
    aws s3 rm "s3://$DATA_BUCKET" --recursive --region "$REGION" >/dev/null 2>&1
    # --recursive leaves noncurrent versions and delete markers behind.
    python3 - "$DATA_BUCKET" "$REGION" <<'PY'
import subprocess, sys, json
bucket, region = sys.argv[1], sys.argv[2]
for key_name in ("Versions", "DeleteMarkers"):
    while True:
        listed = json.loads(subprocess.run(
            ["aws", "s3api", "list-object-versions", "--bucket", bucket,
             "--region", region, "--max-items", "500",
             "--query", f"{{items: {key_name}[].{{Key: Key, VersionId: VersionId}}}}",
             "--output", "json"],
            capture_output=True, text=True).stdout or "{}")
        items = (listed or {}).get("items") or []
        if not items:
            break
        subprocess.run(
            ["aws", "s3api", "delete-objects", "--bucket", bucket, "--region", region,
             "--delete", json.dumps({"Objects": items, "Quiet": True})],
            capture_output=True)
PY
    echo "  Emptied s3://$DATA_BUCKET"
else
    echo -e "  ${YELLOW}Extension data bucket not found in stack outputs, skipping${NC}"
fi

# Delete stack
echo ""
echo "Deleting CloudFormation stack..."
cd "$EXTENSIONS_DIR"
sam delete \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --config-file "$CONFIG_FILE" \
    --no-prompts

echo ""
echo "=============================================="
echo -e "${GREEN}Uninstall complete!${NC}"
echo ""
echo "SDMA data (assets, connectors, templates) was preserved."
echo "To remove extension data: ./scripts/clean-extension-data.sh"
echo "To reinstall: ./scripts/deploy.sh"
echo "=============================================="
