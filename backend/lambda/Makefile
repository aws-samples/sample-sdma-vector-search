# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
#
# Build definitions for the zip-packaged Lambda functions.
#
# Every function's CodeUri points at this directory rather than at its own
# subdirectory. That is deliberate: `sam build` copies CodeUri into a scratch
# directory and runs make there, so anything outside CodeUri is unreachable.
# Rooting the build here puts both functions/ and shared/ inside the copied
# tree, which lets each target assemble itself and removes the need for a
# caller to stage the shared modules first.
#
# blender-render is packaged as a container image and is built by
# scripts/lib/build-image.sh instead; its Dockerfile copies shared/ directly,
# for the same reason and using the same directory as its build context.
#
# Each target copies only the shared modules its function imports.

# Dependencies must be resolved for the Lambda runtime, not for the machine
# running the build. Without these flags pip picks wheels for the local
# interpreter and OS, which silently produces an unimportable package when a
# dependency ships a native extension: a macOS/Python 3.13 build of xxhash
# deployed to Linux/Python 3.11 fails to import, and finalize-render then
# skipped its manifest update -- and with it the asset thumbnail -- because
# the import was wrapped in a try/except.
#
# --only-binary :all: is required: with a cross-platform target pip cannot
# build a source distribution, so it must fail loudly instead of falling
# back to one. Keep these values in step with the Globals in
# infra/cloudformation/template.yaml.
LAMBDA_PYTHON_VERSION ?= 3.11
LAMBDA_PLATFORM ?= manylinux2014_aarch64

# $(1) function directory under functions/
# $(2) space-separated shared module names, without the .py suffix
define build_function
	cp $(1)/*.py $(ARTIFACTS_DIR)/
	$(foreach m,$(2),cp shared/$(m).py $(ARTIFACTS_DIR)/;)
	python -m pip install --no-cache-dir -r $(1)/requirements.txt -t $(ARTIFACTS_DIR) \
	    --platform $(LAMBDA_PLATFORM) \
	    --python-version $(LAMBDA_PYTHON_VERSION) \
	    --implementation cp \
	    --only-binary :all:
endef

build-AITagGenerationFunction:
	$(call build_function,functions/ai-tag-generation,common_constants vector_utils log_utils sdma_client asset_jobs)

build-VectorSearchApiFunction:
	$(call build_function,functions/vector-search-api,common_constants vector_utils log_utils sdma_client asset_jobs)

build-PrepareRenderFunction:
	$(call build_function,functions/prepare-render,common_constants rendering_constants log_utils sdma_client asset_jobs)

build-FinalizeRenderFunction:
	$(call build_function,functions/finalize-render,common_constants log_utils sdma_client asset_jobs)

.PHONY: build-AITagGenerationFunction build-VectorSearchApiFunction \
        build-PrepareRenderFunction build-FinalizeRenderFunction
