# Tests

Unit tests for the Lambda backend. They run offline: AWS clients are replaced
with fakes, so no credentials or deployed stack are required.

```bash
pip install -r tests/requirements.txt
pytest
```

## What is covered

| File | Covers |
|------|--------|
| `test_vector_utils.py` | Embedding text composition and the Bedrock `InvokeModel` request |
| `test_vector_indexer.py` | Shape of the DynamoDB item written for each asset |
| `test_search_api.py` | Filter expression building, the `SearchVectors` request, score conversion, browse mode, and handler routing |
| `test_sdma_client.py` | The SDMA REST client: library-scoped paths, project resolution, and the presigned URLs the render and tagging paths read content through |
| `test_prepare_render.py` | The render plan handed to the Map state, and the failures that must not return neutrally |
| `test_blender_render.py` | Model download via the presigned URL, and the S3 fallback |
| `test_ai_tagging.py` | Rendered-view download via SDMA, and which path each view actually took |
| `test_finalize_render.py` | The manifest update, where a thumbnail is won or lost |
| `test_asset_jobs.py` | Processing state in this stack's own table, including reserved-word aliasing |
| `test_category_config.py` | Correction of the model's output against the filter vocabulary, for the list fields the indexer actually reads |
| `test_log_utils.py` | Redaction of SDMA credentials before they reach CloudWatch |
| `test_dependencies.py` | Every import in a function resolves to a declared distribution, following the shared modules the Makefile bundles |

The emphasis is on contracts that fail **silently** rather than raising:

- The embedding is written as a **List of Numbers** (`L` of `N`) in an item, but
  passed to `SearchVectors` as a **bare array** of `{"N": ...}`. Conflating the
  two shapes is easy and only fails at call time.
- An asset missing a vector index attribute is still written to the base table
  but is excluded from or unfilterable in search results.
- If the written vector length disagrees with the index `Dimensions`, the write
  is rejected; if the *model* disagrees while the length matches, search quietly
  returns meaningless results.
- `SearchVectors` returns a COSINE **distance** (lower is closer) where the API
  documents a 0-1 similarity. Passing it through inverts the ranking without
  failing.
- Asset content is read through presigned URLs SDMA issues, with a direct S3
  read as a fallback. The fallback succeeds quietly, so a regression that routes
  every download back to S3 would widen the permission boundary with nothing to
  notice. The tests assert *which* path was taken.
- SDMA derives a thumbnail from an entry in its manifest, so a manifest update
  that skips silently produces an asset that renders correctly and shows no
  thumbnail.

## Adding tests

Each Lambda function directory has its own `system_defaults.py` and
`aws_clients.py` with different contents, so they cannot all sit on `sys.path`
at once. Use `load_function_module()` for a function module and
`load_shared_module()` for one under `backend/lambda/shared`; both put exactly
what is needed on the path and unload it afterwards, so one test's patching
cannot leak into the next.

## Not covered

Blender itself runs in a subprocess, so the rendering is not unit tested -- only
the download that feeds it. Rendering is exercised by `scripts/test-upload.sh`
against a deployed stack.

## The rest of the checks

`.github/workflows/ci.yml` runs all of these on every push and pull request.
Running them locally first is still worth it — each has caught a defect the
others missed, and finding it before the push is cheaper.

```bash
pytest                                                     # offline, no AWS
sam validate -t infra/cloudformation/template.yaml --lint   # runs cfn-lint
python -c "import json; json.load(open('infra/stepfunctions/asset-processing-pipeline.asl.json'))"
shellcheck -S warning scripts/*.sh scripts/lib/*.sh
for s in scripts/*.sh; do bash "$s" --help >/dev/null; done

cd frontend/search-demo
npm ci
npm audit --audit-level=high
npm run build        # runs tsc; `npm run dev` does not, so type errors hide there
```

Why each one earns its place:

- **`--lint`** runs cfn-lint. The vector index carries resource-scoped
  suppressions for E3002/E3039, false positives from a published resource spec
  that does not describe `VectorIndexes` yet. Remove them when it catches up.
- **`shellcheck -S warning`** is what catches a variable left behind by a
  refactor. A stale `STACK_NAME` survived one this way.
- **`--help` on every script** catches a header that documents an option the
  script does not have, or a path that does not exist.
- **`npm audit`** is worth running by hand because nothing else here checks the
  demo's dependencies against advisories.
- **`npm run build`** runs `tsc`. A type error is invisible to `npm run dev`,
  and honestly declaring an environment variable's type once exposed a latent
  hole that an implicit `any` had been hiding.

A passing suite does not prove a change reached the deployed stack. See the
verification notes in [`../docs/design.md`](../docs/design.md): deploy, exercise
the path, and read the result back out of DynamoDB or the API. A filter that is
silently ignored returns the same result count as one that works, so assert on
the values, never on the count.
