#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# ==============================================================================
# SDMA Vector Search Extension - Test Upload
#
# Upload 3D asset(s) and wait for processing (rendering + AI tagging)
#
# Usage:
#   ./scripts/test-upload.sh <file.glb>
#   ./scripts/test-upload.sh -d <dir>
#   ./scripts/test-upload.sh --project "MyProject" <file.glb>
#
# Options:
#   -d, --dir DIR          Upload all 3D files in directory
#   -p, --project NAME     Use or create project with this name
#   -h, --help             Show this help
#
# Supported formats: .glb, .gltf, .fbx, .obj, .blend
# ==============================================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/_common.sh"

# Parse arguments
FOLDER_MODE=false
TARGET=""
PROJECT_ARG=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --dir|-d) FOLDER_MODE=true; TARGET="$2"; shift 2 ;;
        --project|-p) PROJECT_ARG="$2"; shift 2 ;;
        --help|-h) print_help "$0"; exit 0 ;;
        *) TARGET="$1"; shift ;;
    esac
done

if [ -z "$TARGET" ]; then
    echo "Usage: $0 <file.glb> or $0 -d <dir>"
    exit 1
fi

print_header "SDMA Vector Search Extension - Test Upload"

check_prerequisites
check_cli

# ── 1. Check deployment ──────────────────────────────────────────────────────
echo ""
echo "[1/4] Checking deployment..."
STACK_STATUS=$(aws cloudformation describe-stacks \
    --stack-name "$(get_stack_name)" \
    --region "$(get_region)" \
    --query 'Stacks[0].StackStatus' \
    --output text 2>/dev/null || echo "NOT_FOUND")

if [[ ! "$STACK_STATUS" =~ ^(CREATE_COMPLETE|UPDATE_COMPLETE)$ ]]; then
    echo -e "${RED}Stack not ready: $STACK_STATUS${NC}"
    echo "Run ./scripts/deploy.sh first"
    exit 1
fi

CONNECTOR_OUTPUT=$(spatial-data-mgmt connector list --output json 2>&1)
if echo "$CONNECTOR_OUTPUT" | grep -q "error"; then
    echo -e "${RED}SDMA CLI error. Run: spatial-data-mgmt auth login${NC}"
    exit 1
fi
CONNECTOR_ID=$(echo "$CONNECTOR_OUTPUT" | jq -r '.[] | select(.connectorName=="LambdaRenderPipeline") | .connectorId' 2>/dev/null || echo "")
if [ -z "$CONNECTOR_ID" ]; then
    echo -e "${RED}Connector not found. Run ./scripts/deploy.sh${NC}"
    exit 1
fi
echo -e "  ${GREEN}Stack: $STACK_STATUS, Connector: $CONNECTOR_ID${NC}"

# ── 2. Project & template setup ─────────────────────────────────────────────
echo ""
echo "[2/4] Checking project & template..."

# Ensure template exists
TEMPLATE_NAME="Render Pipeline"
TEMPLATE_ID=$(spatial-data-mgmt asset-template list --output json 2>/dev/null | \
    jq -r ".[] | select(.assetTemplateName==\"$TEMPLATE_NAME\") | .assetTemplateId" 2>/dev/null || echo "")
if [ -z "$TEMPLATE_ID" ]; then
    echo -e "${RED}Template '$TEMPLATE_NAME' not found. Run ./scripts/deploy.sh${NC}"
    exit 1
fi
echo "  Template: $TEMPLATE_ID"

# Select or create project with template
# Strategy:
#   1. --project arg: find by name or create with template
#   2. Auto-select: pick project that already has the template
#   3. Multiple candidates: let user choose
#   4. No candidates: ask for name and create
PROJECTS_JSON=$(spatial-data-mgmt project list --output json 2>/dev/null)

# Find projects that have the template associated
READY_PROJECTS=$(echo "$PROJECTS_JSON" | jq -r "[.[] | select(.permittedTemplateIds != null and (.permittedTemplateIds | index(\"$TEMPLATE_ID\")))]" 2>/dev/null)
READY_COUNT=$(echo "$READY_PROJECTS" | jq 'length' 2>/dev/null || echo "0")

if [ -n "$PROJECT_ARG" ]; then
    # --project specified: find existing or create
    PROJECT_ID=$(echo "$PROJECTS_JSON" | jq -r ".[] | select(.projectName==\"$PROJECT_ARG\") | .projectId" 2>/dev/null || echo "")
    if [ -n "$PROJECT_ID" ] && [ "$PROJECT_ID" != "null" ]; then
        HAS_TPL=$(echo "$PROJECTS_JSON" | jq -r ".[] | select(.projectId==\"$PROJECT_ID\") | .permittedTemplateIds // [] | if index(\"$TEMPLATE_ID\") then \"yes\" else \"no\" end" 2>/dev/null || echo "no")
        if [ "$HAS_TPL" != "yes" ]; then
            echo -e "  ${YELLOW}Project '$PROJECT_ARG' exists but lacks template. Creating new project...${NC}"
            PROJECT_ARG="${PROJECT_ARG}-ext"
            PROJECT_ID=""
        fi
    fi
    if [ -z "$PROJECT_ID" ] || [ "$PROJECT_ID" = "null" ]; then
        PROJECT_ID=$(spatial-data-mgmt project create \
            --project-name "$PROJECT_ARG" \
            --project-config-json "{\"permittedTemplateIds\": [\"$TEMPLATE_ID\"]}" \
            --output json --yes 2>/dev/null | grep -o '"projectId": "[^"]*"' | head -1 | sed 's/"projectId": "\([^"]*\)"/\1/')
        echo -e "  ${GREEN}Created project '$PROJECT_ARG' ($PROJECT_ID)${NC}"
    else
        echo "  Using project: $PROJECT_ARG ($PROJECT_ID)"
    fi
elif [ "$READY_COUNT" = "1" ]; then
    PROJECT_ID=$(echo "$READY_PROJECTS" | jq -r '.[0].projectId')
    PROJECT_NAME=$(echo "$READY_PROJECTS" | jq -r '.[0].projectName')
    echo "  Using project: $PROJECT_NAME ($PROJECT_ID)"
elif [ "$READY_COUNT" -gt 1 ] 2>/dev/null; then
    echo "  Projects with render pipeline template:"
    echo "$READY_PROJECTS" | jq -r 'to_entries[] | "    [\(.key+1)] \(.value.projectName) (\(.value.projectId))"'
    read -p "  Select [1]: " choice
    choice=${choice:-1}
    PROJECT_ID=$(echo "$READY_PROJECTS" | jq -r ".[$((choice-1))].projectId")
    echo "  Using project: $PROJECT_ID"
else
    # No project with template — create one
    # Create project with template association
    echo -e "  ${YELLOW}No project with render pipeline template found.${NC}"
    read -p "  Enter new project name [SDMA-Extension]: " project_name
    project_name=${project_name:-SDMA-Extension}
    PROJECT_ID=$(spatial-data-mgmt project create \
        --project-name "$project_name" \
        --project-config-json "{\"permittedTemplateIds\": [\"$TEMPLATE_ID\"]}" \
        --output json --yes 2>/dev/null | grep -o '"projectId": "[^"]*"' | head -1 | sed 's/"projectId": "\([^"]*\)"/\1/')
    if [ -z "$PROJECT_ID" ]; then
        echo -e "  ${RED}Failed to create project${NC}"
        exit 1
    fi
    echo -e "  ${GREEN}Created project '$project_name' ($PROJECT_ID)${NC}"
fi

# ── 3. Upload files ──────────────────────────────────────────────────────────
# Collect files
FILES=()
if [ "$FOLDER_MODE" = true ]; then
    if [ ! -d "$TARGET" ]; then
        echo -e "${RED}Directory not found: $TARGET${NC}"
        exit 1
    fi
    while IFS= read -r -d '' file; do
        FILES+=("$file")
    done < <(find "$TARGET" -type f \( -name "*.glb" -o -name "*.gltf" -o -name "*.fbx" -o -name "*.obj" -o -name "*.blend" \) -print0)
    if [ ${#FILES[@]} -eq 0 ]; then
        echo -e "${RED}No 3D files found in: $TARGET${NC}"
        exit 1
    fi
else
    if [ ! -f "$TARGET" ]; then
        echo -e "${RED}File not found: $TARGET${NC}"
        exit 1
    fi
    FILES+=("$TARGET")
fi

echo ""
echo "[3/4] Uploading ${#FILES[@]} file(s)..."
ASSET_IDS=()

# Upload in parallel (up to 10 concurrent)
UPLOAD_PARALLEL=10
ASSET_IDS_FILE=$(mktemp)
# Failures are recorded so the summary can distinguish "all uploaded" from
# "uploaded everything that did not fail", and so the script can exit non-zero.
FAILED_FILE=$(mktemp)

upload_one() {
    local FILE="$1"
    local PROJECT_ID="$2"
    local TEMPLATE_ID="$3"
    local BASENAME
    BASENAME=$(basename "$FILE")
    local ASSET_NAME="${BASENAME%.*}"

    # Check if asset already exists
    local EXISTING_ID
    EXISTING_ID=$(spatial-data-mgmt asset list --project-id "$PROJECT_ID" --output json 2>/dev/null | \
        jq -r ".[] | select(.assetName==\"$ASSET_NAME\") | .assetId" 2>/dev/null || echo "")

    if [ -n "$EXISTING_ID" ] && [ "$EXISTING_ID" != "null" ]; then
        echo "$EXISTING_ID" >> "$ASSET_IDS_FILE"
        echo -e "    ${YELLOW}Exists: $BASENAME ($EXISTING_ID)${NC}"
        return
    fi

    local RESULT
    RESULT=$(spatial-data-mgmt asset create \
        --project-id "$PROJECT_ID" \
        --template-id "$TEMPLATE_ID" \
        --asset-name "$ASSET_NAME" \
        --file "$FILE" --output json --yes 2>&1)
    local ASSET_ID
    ASSET_ID=$(echo "$RESULT" | grep -o '"AssetId": "[^"]*"' | tail -1 | sed 's/"AssetId": "\([^"]*\)"/\1/')
    if [ -n "$ASSET_ID" ]; then
        echo "$ASSET_ID" >> "$ASSET_IDS_FILE"
        echo -e "    ${GREEN}Uploaded: $BASENAME ($ASSET_ID)${NC}"
    else
        # Report why. This used to send the CLI's output to /dev/null and print
        # only "Failed: <file>", so diagnosing a failure meant re-running the
        # command by hand. The CLI reports the reason on stdout -- not stderr --
        # as a JSON line with messageType=error.
        local REASON
        REASON=$(printf '%s' "$RESULT" | python3 -c '
import json, sys
reason = ""
for line in sys.stdin:
    line = line.strip()
    if line.startswith("{"):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("messageType") == "error" and entry.get("value"):
            reason = entry["value"]
    elif not reason and ("Error:" in line or "Exception" in line):
        # Fall back to a traceback line if the CLI failed before it could
        # emit structured output.
        reason = line.split(": ", 1)[-1]
print(reason)
' 2>/dev/null)
        [ -z "$REASON" ] && REASON="no reason reported by the SDMA CLI"
        echo -e "    ${RED}Failed: $BASENAME${NC} — $REASON" >&2
        echo "$BASENAME: $REASON" >> "$FAILED_FILE"
    fi
}
export -f upload_one
export ASSET_IDS_FILE FAILED_FILE PROJECT_ID TEMPLATE_ID GREEN RED YELLOW NC

printf '%s\n' "${FILES[@]}" | xargs -P "$UPLOAD_PARALLEL" -I{} bash -c 'upload_one "{}" "'"$PROJECT_ID"'" "'"$TEMPLATE_ID"'"'

# Collect asset IDs
while IFS= read -r id; do
    [ -n "$id" ] && ASSET_IDS+=("$id")
done < "$ASSET_IDS_FILE"
rm -f "$ASSET_IDS_FILE"

# ── 4. Wait for processing ───────────────────────────────────────────────────
echo ""
echo "[4/4] Waiting for processing..."
VECTOR_TABLE=$(get_stack_output "VectorTableName")
SFN_ARN=$(get_stack_output "AssetProcessingStateMachineArn")
TOTAL=${#ASSET_IDS[@]}
COMPLETED=0
PENDING=("${ASSET_IDS[@]}")

# Check DynamoDB vector table for indexed assets
_check_completed() {
    local table="$1"
    shift
    local pending_ids=("$@")
    local still_pending=()
    for aid in "${pending_ids[@]}"; do
        local exists
        exists=$(aws dynamodb get-item --table-name "$table" \
            --key "{\"assetId\": {\"S\": \"$aid\"}}" \
            --projection-expression "assetId" \
            --region "$(get_region)" \
            --query 'Item.assetId.S' --output text 2>/dev/null || echo "None")
        if [ "$exists" != "None" ] && [ -n "$exists" ]; then
            ((COMPLETED++))
        else
            still_pending+=("$aid")
        fi
    done
    PENDING=("${still_pending[@]}")
}

# Poll with real-time stall detection (180s)
STALL_START=$(date +%s)
LAST_COMPLETED=0
while [ ${#PENDING[@]} -gt 0 ]; do
    _check_completed "$VECTOR_TABLE" "${PENDING[@]}"
    [ ${#PENDING[@]} -eq 0 ] && break

    if [ "$COMPLETED" -gt "$LAST_COMPLETED" ]; then
        STALL_START=$(date +%s)
        LAST_COMPLETED=$COMPLETED
    fi

    ELAPSED=$(( $(date +%s) - STALL_START ))
    [ "$ELAPSED" -ge 180 ] && break

    printf "    %d/%d complete, %d pending\r" "$COMPLETED" "$TOTAL" "${#PENDING[@]}"
    sleep 5
done
echo ""

# Retry: start SFN directly for assets that were not processed
if [ ${#PENDING[@]} -gt 0 ] && [ -n "$SFN_ARN" ]; then
    echo -e "  ${YELLOW}Retrying ${#PENDING[@]} unprocessed asset(s) via Step Functions...${NC}"
    for ASSET_ID in "${PENDING[@]}"; do
        aws stepfunctions start-execution \
            --state-machine-arn "$SFN_ARN" \
            --input "{\"assetId\": \"$ASSET_ID\"}" \
            --region "$(get_region)" >/dev/null 2>&1
    done

    STALL_START=$(date +%s)
    LAST_COMPLETED=$COMPLETED
    while [ ${#PENDING[@]} -gt 0 ]; do
        _check_completed "$VECTOR_TABLE" "${PENDING[@]}"
        [ ${#PENDING[@]} -eq 0 ] && break

        if [ "$COMPLETED" -gt "$LAST_COMPLETED" ]; then
            STALL_START=$(date +%s)
            LAST_COMPLETED=$COMPLETED
        fi

        ELAPSED=$(( $(date +%s) - STALL_START ))
        [ "$ELAPSED" -ge 180 ] && break

        printf "    %d/%d complete, %d retrying\r" "$COMPLETED" "$TOTAL" "${#PENDING[@]}"
        sleep 5
    done
    echo ""
fi

FAILED_COUNT=0
[ -s "$FAILED_FILE" ] && FAILED_COUNT=$(wc -l < "$FAILED_FILE" | tr -d ' ')

echo ""
echo "=============================================="
# Report against the number of files asked for, not the number that uploaded.
# Reporting "138/138 processed" for 140 files hid two failures completely.
if [ "$FAILED_COUNT" -eq 0 ] && [ "$COMPLETED" -eq "${#ASSET_IDS[@]}" ]; then
    echo -e "${GREEN}Upload complete: $COMPLETED/${#FILES[@]} processed${NC}"
else
    echo -e "${YELLOW}Upload finished with problems: $COMPLETED/${#FILES[@]} processed${NC}"
    [ "$FAILED_COUNT" -gt 0 ] && {
        echo -e "${RED}  $FAILED_COUNT file(s) failed to upload:${NC}"
        sed 's/^/    - /' "$FAILED_FILE"
    }
    UNPROCESSED=$(( ${#ASSET_IDS[@]} - COMPLETED ))
    [ "$UNPROCESSED" -gt 0 ] && \
        echo -e "${RED}  $UNPROCESSED asset(s) uploaded but never indexed${NC}"
fi
echo "=============================================="
echo ""
echo "Asset IDs:"
for ASSET_ID in "${ASSET_IDS[@]}"; do
    echo "  - $ASSET_ID"
done
echo ""
echo "Next: ./scripts/test-search.sh to open search demo"

rm -f "$FAILED_FILE"
# Exit non-zero so a caller -- a person scrolling past, or CI -- cannot mistake
# a partial run for a clean one.
if [ "$FAILED_COUNT" -gt 0 ] || [ "$COMPLETED" -ne "${#ASSET_IDS[@]}" ]; then
    exit 1
fi
