#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# ==============================================================================
# SDMA Vector Search Extension - Common Functions
# ==============================================================================

set -e

# Colors. Consumed by the scripts that source this file, which shellcheck
# cannot see, so it reports them as unused.
# shellcheck disable=SC2034
RED='\033[0;31m'
# shellcheck disable=SC2034
GREEN='\033[0;32m'
# shellcheck disable=SC2034
YELLOW='\033[1;33m'
# shellcheck disable=SC2034
NC='\033[0m'

# Get configuration from environment or AWS CLI config
get_region() {
    local region="${AWS_REGION:-$(aws configure get region 2>/dev/null)}"
    if [ -z "$region" ]; then
        echo -e "${RED}AWS region not configured. Set AWS_REGION or run 'aws configure'${NC}" >&2
        exit 1
    fi
    echo "$region"
}

get_environment() {
    echo "${ENVIRONMENT:-dev}"
}

get_stack_name() {
    echo "sdma-vector-search-extension-$(get_environment)"
}

get_extensions_dir() {
    echo "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
}

# Check prerequisites
check_prerequisites() {
    local missing=()
    command -v aws >/dev/null || missing+=("aws")
    command -v sam >/dev/null || missing+=("sam")
    if [ ${#missing[@]} -gt 0 ]; then
        echo -e "${RED}Missing: ${missing[*]}${NC}"
        exit 1
    fi
}

check_cli() {
    if ! command -v spatial-data-mgmt >/dev/null; then
        echo -e "${RED}Missing: spatial-data-mgmt CLI${NC}"
        exit 1
    fi
}

check_docker() {
    if ! command -v docker >/dev/null; then
        echo -e "${RED}Missing: docker${NC}"
        exit 1
    fi
    if ! docker info >/dev/null 2>&1; then
        echo -e "${RED}Docker not running${NC}"
        exit 1
    fi
}

# Get stack output
get_stack_output() {
    local key=$1
    aws cloudformation describe-stacks \
        --stack-name "$(get_stack_name)" \
        --region "$(get_region)" \
        --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue" \
        --output text 2>/dev/null
}

# Get SDMA resources (auto-discovery)
get_sdma_cognito_pool_id() {
    aws cognito-idp list-user-pools --max-results 20 --region "$(get_region)" \
        --query 'UserPools[?contains(Name, `spatial-data-management`) || contains(Name, `sdma`)].Id | [0]' \
        --output text 2>/dev/null | grep -v '^None$' || echo ""
}



get_sdma_s3_bucket() {
    aws s3 ls 2>/dev/null | grep -E 'spatialdatamanagement.*assetencrypteds3' | grep -v 'log' | sort -k1,2 | tail -1 | awk '{print $3}'
}

get_sdma_connector_role_arn() {
    aws iam list-roles --query 'Roles[?contains(RoleName, `SpatialDataManagement`) && contains(RoleName, `ConnectorI`)].Arn' --output text 2>/dev/null | head -1 | grep -v '^None$' || echo ""
}

get_sdma_api_endpoint() {
    local api_id
    api_id=$(aws apigateway get-rest-apis --region "$(get_region)" \
        --query 'items[?description!=null && contains(description, `Spatial Data Management`)].id | [0]' \
        --output text 2>/dev/null | grep -v '^None$')
    if [ -z "$api_id" ]; then echo ""; return; fi
    local stage
    stage=$(aws apigateway get-stages --rest-api-id "$api_id" --region "$(get_region)" \
        --query 'item[0].stageName' --output text 2>/dev/null | grep -v '^None$')
    # Without this check an unresolved stage yielded a trailing-slash URL that
    # looked valid and failed later as a 403 from API Gateway.
    if [ -z "$stage" ]; then echo ""; return; fi
    echo "https://${api_id}.execute-api.$(get_region).amazonaws.com/${stage}"
}

# Every SDMA API path is scoped to a library, and a deployment has one, so the
# functions take it as an environment variable rather than looking it up on
# every invocation. Resolved here because the SDMA CLI already handles the
# API's SigV4 signing, which a shell script otherwise cannot do.
get_sdma_library_id() {
    spatial-data-mgmt library list --output json 2>/dev/null \
        | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
libraries = data if isinstance(data, list) else data.get("libraries", [])
# A deployment has a single library. If that ever changes, failing loudly here
# is better than silently picking one and indexing against the wrong scope.
if len(libraries) == 1:
    print(libraries[0].get("libraryId", ""))
elif len(libraries) > 1:
    print("MULTIPLE")
' 2>/dev/null
}

# Wait for AWS resource to reach target status
# Usage: wait_for_status "aws cmd --query status" "ACTIVE" "FAILED" "label"
wait_for_status() {
    local cmd="$1" target="$2" fail="$3" label="$4"
    echo "    Waiting for $label..."
    while true; do
        local status
        status=$(eval "$cmd")
        [ "$status" = "$target" ] && return 0
        [ "$status" = "$fail" ] && { echo -e "    ${RED}$label failed${NC}"; return 1; }
        sleep 5
    done
}

# Render JSON template: replace ${VAR} placeholders with values
# Usage: render_template file.json KEY1 val1 KEY2 val2
render_template() {
    local file="$1"
    shift
    local content
    content=$(cat "$file")
    while [[ $# -gt 0 ]]; do
        local key="$1" val="$2"
        content="${content//\$\{$key\}/$val}"
        shift 2
    done
    echo "$content"
}

# Print header
print_header() {
    echo "=============================================="
    echo "$1"
    echo "=============================================="
    echo "Region:      $(get_region)"
    echo "Environment: $(get_environment)"
    echo "Stack:       $(get_stack_name)"
    echo "=============================================="
}

# Print a script's own header comment block as its --help text.
#
# Call as: print_help "$0"
#
# Every script documents itself in a comment block delimited by two `# ====`
# rules. Extract that block by those markers rather than by line offsets:
# each script used to slice its header with `head -N | tail -M`, and every
# edit to a header shifted the numbers, so --help printed from the middle of
# the description instead of from "Usage:".
print_help() {
    local script="$1"
    awk '
        /^# ={10,}$/ { rule++; next }
        rule == 1    { sub(/^#[[:space:]]?/, ""); print }
        rule >= 2    { exit }
    ' "$script"
}
