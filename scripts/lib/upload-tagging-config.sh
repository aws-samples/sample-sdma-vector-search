#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# ==============================================================================
# Upload tagging config to S3
#
# Supports both YAML and JSON formats (YAML preferred)
#
# Usage: ./lib/upload-tagging-config.sh [config-file]
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_common.sh"

EXTENSIONS_DIR=$(get_extensions_dir)
# The Extension's own bucket, not SDMA's. Writing configs into SDMA's asset
# bucket claimed a prefix in a namespace this solution does not own.
S3_BUCKET=$(get_stack_output "ExtensionDataBucketName")

if [ -z "$S3_BUCKET" ]; then
    echo -e "  ${RED}Extension data bucket not found. Deploy the stack first.${NC}"
    exit 1
fi

# Find config file (YAML preferred, then JSON)
if [ -n "$1" ]; then
    CONFIG_FILE="$1"
elif [ -f "$EXTENSIONS_DIR/config/tagging/default.yaml" ]; then
    CONFIG_FILE="$EXTENSIONS_DIR/config/tagging/default.yaml"
elif [ -f "$EXTENSIONS_DIR/config/tagging/default.json" ]; then
    CONFIG_FILE="$EXTENSIONS_DIR/config/tagging/default.json"
else
    echo -e "  ${YELLOW}No tagging config found (skipped)${NC}"
    exit 0
fi

if [ ! -f "$CONFIG_FILE" ]; then
    echo -e "  ${YELLOW}Config not found: $CONFIG_FILE (skipped)${NC}"
    exit 0
fi

# Validate based on file extension
BASENAME=$(basename "$CONFIG_FILE")
if [[ "$CONFIG_FILE" == *.yaml ]] || [[ "$CONFIG_FILE" == *.yml ]]; then
    # YAML validation (requires python3 with yaml)
    if command -v python3 &> /dev/null; then
        if ! python3 -c "import yaml; yaml.safe_load(open('$CONFIG_FILE'))" 2>/dev/null; then
            echo -e "  ${RED}Invalid YAML: $CONFIG_FILE${NC}"
            exit 1
        fi
    fi
    # Count categories from YAML
    CAT_COUNT=$(python3 -c "import yaml; c=yaml.safe_load(open('$CONFIG_FILE')); fa=c.get('filter_attributes',c); print(len(fa.get('categories',{})))" 2>/dev/null || echo "?")
else
    # JSON validation
    if ! jq empty "$CONFIG_FILE" 2>/dev/null; then
        echo -e "  ${RED}Invalid JSON: $CONFIG_FILE${NC}"
        exit 1
    fi
    # Count categories from JSON
    CAT_COUNT=$(jq '.filter_attributes.categories // .categories | keys | length' "$CONFIG_FILE" 2>/dev/null || echo "?")
fi

# Upload
aws s3 cp "$CONFIG_FILE" "s3://$S3_BUCKET/config/tagging/$BASENAME" --quiet
echo -e "  ${GREEN}Uploaded: s3://$S3_BUCKET/config/tagging/$BASENAME${NC}"
echo "  Categories: $CAT_COUNT"
