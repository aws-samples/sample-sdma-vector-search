#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# ==============================================================================
# Blender Image Build
#
# Builds and pushes the Blender render container to ECR, then reports an
# immutable tag derived from the pushed digest. Deploying that tag rather than a
# fixed one is what makes CloudFormation actually update the function: a tag
# whose string never changes leaves the resource unchanged, and the function
# keeps running its previous image.
#
# Called by deploy.sh; run it directly only to rebuild the image alone.
#
# Usage:
#   ./scripts/lib/build-image.sh [environment]
#
# Example:
#   ./scripts/lib/build-image.sh dev
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXTENSIONS_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Handle --help before treating the argument as an environment name: without
# this, `--help` becomes the environment and the script builds and pushes an
# image tagged `--help`.
case "${1:-}" in
    -h|--help)
        # shellcheck source=_common.sh
        source "$SCRIPT_DIR/_common.sh"
        print_help "$0"
        exit 0
        ;;
esac

ENV=${1:-dev}

# Get AWS account and region
AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
AWS_REGION=${AWS_REGION:-$(aws configure get region)}

if [ -z "$AWS_REGION" ]; then
    AWS_REGION="us-east-1"
fi

REPO_NAME="sdma-blender-render-${ENV}"
IMAGE_TAG="latest"
FULL_IMAGE_URI="${AWS_ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com/${REPO_NAME}:${IMAGE_TAG}"

echo "======================================"
echo "Building Blender Lambda Container"
echo "======================================"
echo "Environment: $ENV"
echo "Region: $AWS_REGION"
echo "Account: $AWS_ACCOUNT"
echo "Repository: $REPO_NAME"
echo "Image URI: $FULL_IMAGE_URI"
echo "======================================"

# Check if ECR repository exists, create if not
echo "Checking ECR repository..."
if ! aws ecr describe-repositories --repository-names "$REPO_NAME" --region "$AWS_REGION" 2>/dev/null; then
    echo "Creating ECR repository: $REPO_NAME"
    aws ecr create-repository \
        --repository-name "$REPO_NAME" \
        --region "$AWS_REGION" \
        --image-scanning-configuration scanOnPush=true
fi

# Login to ECR
echo "Logging in to ECR..."
aws ecr get-login-password --region "$AWS_REGION" | \
    docker login --username AWS --password-stdin "${AWS_ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com"

# Build Docker image
echo "Building Docker image..."

# The build context is backend/lambda/ rather than the function directory, so
# the Dockerfile can COPY shared/ directly. Docker cannot reach outside its
# context, which is why the shared modules previously had to be copied into the
# function directory before this ran.
BUILD_CONTEXT="$EXTENSIONS_DIR/backend/lambda"
cd "$BUILD_CONTEXT"

# Build for x86_64 (required for Lambda with Blender)
docker build \
    --platform linux/amd64 \
    -f functions/blender-render/Dockerfile \
    -t "$REPO_NAME:$IMAGE_TAG" \
    -t "$FULL_IMAGE_URI" \
    .

# Push to ECR
echo "Pushing image to ECR..."
docker push "$FULL_IMAGE_URI"

echo "  Image pushed: $FULL_IMAGE_URI"

# Also publish an immutable tag derived from the pushed manifest's digest, and
# report it so the caller can deploy that tag instead of "latest". Deploying by
# a mutable tag leaves ImageUri byte-identical between builds, so CloudFormation
# detects no change and the function keeps running the previous image -- a code
# change then looks deployed when it is not. The second push only uploads a
# manifest; the layers are already in the registry.
IMAGE_DIGEST=$(aws ecr describe-images \
    --repository-name "$REPO_NAME" \
    --image-ids imageTag="$IMAGE_TAG" \
    --region "$AWS_REGION" \
    --query 'imageDetails[0].imageDigest' \
    --output text 2>/dev/null)

DIGEST_TAG=""
if [ -n "$IMAGE_DIGEST" ] && [ "$IMAGE_DIGEST" != "None" ]; then
    DIGEST_TAG="sha-${IMAGE_DIGEST#sha256:}"
    DIGEST_TAG="${DIGEST_TAG:0:16}"
    docker tag "$FULL_IMAGE_URI" "${FULL_IMAGE_URI%:*}:${DIGEST_TAG}"
    docker push "${FULL_IMAGE_URI%:*}:${DIGEST_TAG}" >/dev/null
    echo "  Immutable tag: $DIGEST_TAG"
fi

if [ -z "$DIGEST_TAG" ]; then
    echo "  Warning: no digest available; deployment falls back to $IMAGE_TAG" >&2
    DIGEST_TAG="$IMAGE_TAG"
fi

# Write where deploy.sh can read it without parsing this output.
echo "$DIGEST_TAG" > "${IMAGE_TAG_FILE:-/tmp/blender_image_tag}"
