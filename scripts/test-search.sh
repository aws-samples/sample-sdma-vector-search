#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# ==============================================================================
# SDMA Vector Search Extension - Test Search
#
# Launch the semantic search demo UI
#
# Usage: ./scripts/test-search.sh [OPTIONS]
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

EXTENSIONS_DIR=$(get_extensions_dir)
FRONTEND_DIR="$EXTENSIONS_DIR/frontend/search-demo"

print_header "SDMA Vector Search Extension - Search Demo"

# Check prerequisites
echo ""
echo "[1/3] Checking prerequisites..."
check_prerequisites

if ! command -v npm >/dev/null; then
    echo -e "${RED}Missing: npm${NC}"
    echo "Install Node.js: https://nodejs.org/"
    exit 1
fi
echo -e "  ${GREEN}Prerequisites OK${NC}"

# Get configuration
echo ""
echo "[2/3] Getting configuration..."
REGION=$(get_region)
API_ENDPOINT=$(get_stack_output "APIGatewayEndpoint")

if [ -z "$API_ENDPOINT" ] || [ "$API_ENDPOINT" = "None" ]; then
    echo -e "${RED}API endpoint not found. Run ./scripts/deploy.sh${NC}"
    exit 1
fi

# Get Cognito config from SDMA
COGNITO_POOL_ID=$(get_sdma_cognito_pool_id)

# Get Cognito client ID (first client in pool)
COGNITO_CLIENT_ID=$(aws cognito-idp list-user-pool-clients \
    --user-pool-id "$COGNITO_POOL_ID" \
    --region "$REGION" \
    --query 'UserPoolClients[0].ClientId' \
    --output text 2>/dev/null || echo "")

echo "  API: $API_ENDPOINT"
echo "  Cognito Pool: $COGNITO_POOL_ID"
echo "  Cognito Client: $COGNITO_CLIENT_ID"

# Update .env file
ENV_FILE="$FRONTEND_DIR/.env"
cat > "$ENV_FILE" << EOF
VITE_COGNITO_USER_POOL_ID=$COGNITO_POOL_ID
VITE_COGNITO_CLIENT_ID=$COGNITO_CLIENT_ID
VITE_AWS_REGION=$REGION
VITE_API_ENDPOINT=$API_ENDPOINT
EOF
echo "  Updated: .env"

# Install dependencies if needed
echo ""
echo "[3/3] Starting demo UI..."
cd "$FRONTEND_DIR"

if [ ! -d "node_modules" ] || [ "package.json" -nt "node_modules" ]; then
    echo "  Installing dependencies..."
    npm install --silent
fi

echo ""
echo "=============================================="
echo -e "${GREEN}Starting search demo...${NC}"
echo "=============================================="
echo ""
echo "  Login with SDMA Cognito credentials"
echo "  (URL will be shown below - port may vary)"
echo ""
echo "Press Ctrl+C to stop"
echo ""

# Start dev server and capture URL for browser opening
# Vite outputs: "Local: http://localhost:XXXX/"
npm run dev 2>&1 | while IFS= read -r line; do
    echo "$line"
    # Extract and open URL when Vite outputs it
    if echo "$line" | grep -q "Local:"; then
        URL=$(echo "$line" | grep -oE 'http://[^ ]+' | head -1)
        if [ -n "$URL" ]; then
            (sleep 1 && open "$URL" 2>/dev/null || xdg-open "$URL" 2>/dev/null || true) &
        fi
    fi
done
