# Design

Requirements, design, and the decisions behind them. The architecture is drawn in
[`images/architecture.png`](images/architecture.png), which simplifies two edges
for readability — the table under [Components](#components) is authoritative on
which function touches which resource.

## Requirements

### What this solves

SDMA stores 3D assets and lets a user find them by name, project, and the
metadata entered at upload. It cannot answer "a modern wooden desk with drawers"
unless someone typed those words. For a library of a few hundred assets that is
already the difference between finding an asset and remodelling it.

### Functional

| # | Requirement |
|---|---|
| F1 | Find assets by natural-language description, not filename |
| F2 | Derive the searchable description automatically — no manual tagging |
| F3 | Start automatically when an asset is uploaded to SDMA, with no extra step for the user |
| F4 | Filter results by category, style, material, and colour |
| F5 | List assets without a query, so the UI is useful before the user types |
| F6 | Show a thumbnail for each result |
| F7 | Authenticate against SDMA's existing identity — no second set of credentials |

### Non-functional

| # | Requirement | Where it is met |
|---|---|---|
| N1 | Runs alongside an existing SDMA deployment without modifying it | A separate CloudFormation stack; SDMA is reached only through its public REST API |
| N2 | Adds no material cost at the scale SDMA targets | ~$15/month for 10K assets (see README) |
| N3 | Uninstalls cleanly, leaving SDMA as it was | Three idempotent scripts; the Extension's data is in a bucket this stack owns |
| N4 | Survives an SDMA version upgrade, or fails loudly | 181 offline tests, plus a documented upgrade procedure |
| N5 | Least privilege — no standing access wider than the task needs | Per-function roles; asset content is read through presigned URLs SDMA issues |

## Design

### Two journeys

**Upload** — the SDMA connector invokes `prepare-render`, which starts a Step
Functions execution: render 8 views in parallel, register them with SDMA,
generate metadata with Claude, embed it with Titan, store the vector.

**Search** — the demo UI calls API Gateway (Cognito-authorised), which embeds
the query, runs `SearchVectors`, and resolves each result's name and thumbnail
through SDMA's API.

### Components

| Component | Choice | Why |
|---|---|---|
| Vector store | DynamoDB native vector search | ~$5/month at 10K assets against ~$700+ for OpenSearch Serverless. The index lives on the same table as the metadata, so no second store to keep consistent |
| Embedding | Titan Text Embeddings v2, 1024-dim, COSINE | Matches the index; a bare 1024 dims keeps the item small |
| Tagging | Claude Haiku 4.5 | Reads 8 rendered views at once; the cheapest model that reliably returns the structured shape |
| Rendering | Blender 4.0 in a Lambda container | No render farm to operate. 8 views fan out as a Map state, so an asset finishes in about the time of one view |
| Orchestration | Step Functions | The fan-out and the ordering (render → register → tag) are the workflow, not code |
| Processing state | DynamoDB table this stack owns (`AssetJobs`) | 30-day TTL; advisory, so a write failure never fails an upload |
| Trigger | SDMA connector, `lambdaInvoke` | SDMA's own extension point. No polling, and nothing for the user to run |

### Who touches what

The diagram simplifies two of these edges — it draws one arrow where three
functions write processing state, and one where three read the Extension's
bucket. This table is the accurate version.

| | SDMA API | SDMA bucket | `AssetVectors` | `AssetJobs` | Extension bucket | Bedrock |
|---|---|---|---|---|---|---|
| `prepare-render` | resolve project, list files, presign the model, get write credentials | — | — | write | read (rendering config) | — |
| `blender-render` | — | write renders (SDMA-vended credentials); read model only as a fallback | — | — | — | — |
| `finalize-render` | read asset, register files, set thumbnail | read + write the manifest | — | write | — | — |
| `ai-tag-generation` | read asset name, presign the rendered views | read renders only as a fallback | write | write | read config, write metadata, clear intermediates | Claude, Titan |
| `vector-search-api` | resolve name and thumbnail (`GetAsset` → `GetFile`) | — | `SearchVectors` / `Scan` | read | read (filter vocabulary) | Titan |

Two properties are worth reading off it. Only `finalize-render` touches SDMA's
bucket for anything other than a fallback, and that is the manifest alone (D3).
And no function reads SDMA's DynamoDB tables — nor do the scripts (D2).

### Data

`AssetVectors` — one item per asset, keyed by `assetId`:

- `embedding` — 1024 numbers. Written as a **List of Numbers** (`L` of `N`);
  passed to `SearchVectors` as a **bare array**. Same vector, two shapes.
- `category`, `style`, `primaryMaterial`, `primaryColor` — the four
  `INLINE_FILTER` attributes, so equality filtering happens at the storage layer.
- `projectId` — resolved once at upload, because SDMA offers no way to get from
  an asset id to its project (see D4).
- Tags, description, and the structured metadata the UI displays.

`AssetJobs` — one item per asset in flight, keyed by `assetId`, with a 30-day
TTL. Render and tagging status only.

## Decisions

### D1 — DynamoDB native vector search over OpenSearch Serverless

**Context.** An earlier iteration used Bedrock Knowledge Bases over OpenSearch
Serverless.

**Decision.** DynamoDB's vector index.

**Why.** Cost dominates at this scale: OpenSearch Serverless bills a minimum
capacity whether or not anything is indexed, which is roughly two orders of
magnitude above the rest of this solution combined. The metadata and the vector
also end up in one item, so filtering and ranking are one call.

**Cost.** The index requires `PAY_PER_REQUEST`, and `SearchVectors` needs
**boto3 1.43.64+** — only the search function pins that floor.

### D2 — Reach SDMA only through its REST API

**Context.** The Extension read SDMA's DynamoDB tables directly (asset records
and the file list) and wrote six of its own attributes onto SDMA's asset record.

**Decision.** Every read and write goes through SDMA's REST API. SDMA's tables
are not referenced at all, and the Extension's processing state lives in its own
table.

**Why.** A direct table read couples this solution to SDMA's internal schema,
which is not a published interface — an SDMA upgrade that renames an attribute
breaks the Extension silently. Writing into SDMA's records is worse: it puts
this solution's data in a table it does not own, where SDMA's own validation
never sees it.

**Cost.** Resolving a project costs one API call per project at upload
(measured 0.39 s across 3). The API cannot do everything a table read could —
see D4.

### D3 — Read asset content through presigned URLs, not S3 directly

**Context.** The functions read models and rendered views from SDMA's bucket
with their own IAM role, which needs a bucket-wide `s3:GetObject`. That grant
reaches every asset in every project, regardless of what SDMA would permit.

**Decision.** `GetFile` mints a presigned URL per registered file (valid one
hour, ample for a render fan-out). Content is read through that. A direct S3
read remains as a fallback.

**Why.** The read then goes through SDMA's access control rather than a standing
grant of our own. Both roles drop to read-only, and `prepare-render` needs no
access to *SDMA's* bucket at all — it still reads the rendering config from the
Extension's own bucket, as the table above records.

**Cost.** One extra API call per file. The fallback is genuinely needed: the
tagging function runs twice, and on the first run `finalize-render` has not
registered the renders yet. Because a fallback succeeds quietly, the tests
assert **which** path each view took.

**Not migrated.** The manifest. It is asset metadata rather than a registered
file, so `GetFile` cannot address it — `finalize-render` reads and writes that
one object directly, and it is the only remaining direct access.

### D4 — Resolve `projectId` once at upload and carry it in the payload

**Context.** Every SDMA API path is project-scoped, but the connector delivers
only `assetId`.

**Decision.** `prepare-render` resolves the project once, then passes it
downstream; the tagging function stores it on the vector item, so search
resolves nothing.

**Why.** SDMA offers no lookup from an asset id alone. Verified by experiment on
v1.6.0: `GET /iam/assets/{id}` 404s, `ListAssets` ignores every filter tried and
returns the full page, no search endpoint exists, and mapping `project.projectId`
into the connector's `fieldMappings` is accepted but still delivers only
`assetId` (tested on the live connector, which is registered in the template's
`permittedConnectorIds` and therefore does fire). So each project is probed until
one has the asset — and that cost is paid once per upload rather than once per
search result.

### D5 — Keep the Extension's own data in a bucket this stack owns

**Context.** The rendering and tagging configs, and the per-asset intermediates,
were written into SDMA's asset bucket.

**Decision.** An `ExtensionDataBucket` in this stack, with a 7-day lifecycle on
the intermediates.

**Why.** Writing our own files into SDMA's bucket leaves debris that SDMA's
uninstall does not know about, and it is what forced the broad write grant that
D3 removed. Owning the bucket means `uninstall.sh` removes it with the stack.

**Cost.** One more bucket, under $1/month. Renders still go to SDMA's bucket —
they are SDMA's asset content, registered as files on the asset.

### D6 — Convert the COSINE distance to a similarity in the backend

**Context.** `SearchVectors` returns a COSINE **distance** under `Score` — lower
is closer — while `api-spec.yaml` documents `score` as a 0-1 similarity.

**Decision.** Convert once, in the search function.

**Why.** Passing the raw value through inverted the ranking: the best match
displayed last, with nothing failing. Converting in the backend means every
client agrees, rather than each reimplementing the same correction.

### D7 — Omit `score` entirely in browse mode

**Context.** With no query there is no ranking, so there is no score.

**Decision.** Browse responses omit the field; the UI derives its mode from its
absence and drops "Relevance" from the sort options.

**Why.** A placeholder score (0, or 1) would be indistinguishable from a real
one and would sort meaninglessly.

**Cost.** Browse cannot use the vector index, so it scans. `Limit` applies
**before** `FilterExpression`, so a filtered scan pages until enough matches
accumulate.

### D8 — Deploy the render container by an immutable tag

**Context.** `ImageUri` ended in `:latest`. The string never changed, so
CloudFormation skipped the function and it kept running the previous image —
which is how a credential-redaction fix reached four functions and not the fifth.

**Decision.** `build-image.sh` publishes a digest-derived tag (`sha-<12 hex>`)
and `deploy.sh` passes it as a parameter.

**Why.** A successful deployment must mean the code changed. With a fixed tag it
does not.

### D9 — Redact SDMA credentials in the logging helper

**Context.** `prepare-render` puts SDMA's short-lived write credentials in the
Step Functions payload, and three handlers logged that payload — nine plaintext
credential writes per asset, with 30-day retention.

**Decision.** Redaction lives in `log_event` itself, in
`backend/lambda/shared/log_utils.py`.

**Why.** Redacting at each call site means the next call site added does not have
it. The credentials are short-lived, which limits the impact but does not make a
plaintext credential in a log acceptable.

### D10 — One source for the filter vocabulary

**Context.** The categories endpoint read a config key nothing wrote, and its
empty fallback overwrote the UI's own defaults — so every dropdown offered only
"All".

**Decision.** `config/tagging/default.yaml` → `filter_attributes` is both the
AI tagger's vocabulary and the UI's dropdown source. The UI keeps its built-in
defaults rather than accepting an empty response.

**Why.** The tagger and the filters have to agree or a filter matches nothing.
Two copies of a vocabulary drift.

### D11 — Prefer a loud failure to a silent fallback

Applied throughout, and worth stating because most of the defects found in this
repository were silent:

- A rejected presigned URL for the model **raises** rather than falling back.
  Rendering an empty file would produce eight blank views and an asset that looks
  processed.
- An asset with no registered model file, or an unresolvable project, raises.
  A neutral return would leave the asset looking queued forever.
- A `xxhash` import failure is logged as a skipped manifest update, because it
  takes the thumbnail entry down with it.
- `test-upload.sh` exits non-zero on a partial run, and
  `clean-sdma-resources.sh` exits non-zero if it gave up on any deletion —
  otherwise `uninstall.sh` runs next against resources that still exist.

Processing status is the deliberate exception: it is advisory, so a write failure
is logged and swallowed rather than failing an upload that otherwise succeeded.

## Constraints worth knowing

| Constraint | Consequence |
|---|---|
| Lambda layers cannot attach to container-image functions | `blender-render` cannot use a layer. `backend/lambda/Makefile` is the single source of which shared modules each function bundles, and every zip function's `CodeUri` is `backend/lambda/` — one level above the function — because `sam build` copies `CodeUri` to a scratch directory and runs `make` there |
| pip resolves wheels for the build host | The Makefile must pin `--platform`, `--python-version`, `--implementation cp`, `--only-binary :all:`, or a native dependency built for the developer's machine ships to Lambda |
| A vector index requires on-demand capacity | `AssetVectors` is `PAY_PER_REQUEST`; not a choice |
| Changing the embedding model requires re-indexing every asset | Query and stored vectors must come from the same model. Changing the dimensions replaces the table |
| SDMA v1.6.0 rejects a connector-level `description` | The per-trigger one inside `connectorConfig` still works. Only bites on **first** creation, so an upgrade with an existing connector will not surface it |
| A connector fires only when registered in the asset template's `permittedConnectorIds` | A newly created connector does nothing otherwise |
| CloudFormation's published resource spec lags the service | `VectorIndexes` is supported but absent from the spec, so cfn-lint E3002/E3039 are suppressed per-resource. To decide whether a property is supported, create a change set with it and another with a deliberately fake property |

## Where things are

| Path | Contains |
|---|---|
| `backend/lambda/shared/` | Modules more than one function needs: the SDMA client, the redacting logger, processing state, embedding helpers |
| `backend/lambda/Makefile` | Which shared modules each function bundles — the single source |
| `infra/cloudformation/template.yaml` | Every resource; 24 of them |
| `infra/stepfunctions/` | The workflow definition |
| `config/` | Rendering and tagging configuration, uploaded to the Extension's bucket at deploy |
| `tests/` | 181 offline tests; see [`tests/README.md`](../tests/README.md) |
| `scripts/` | Deploy, upload, search, and three uninstall scripts |
