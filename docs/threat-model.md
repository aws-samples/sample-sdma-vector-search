# Threat model

Written for the Public Content Security Review of this sample. It records the
trust boundaries, what an attacker could attempt at each one, and what stops
them — including the risks this sample deliberately accepts.

This is sample code. It is not production-ready, and the mitigations below are
what a reader should verify and extend for their own requirements rather than
assume are sufficient.

## What is being protected

| Asset | Why it matters |
|---|---|
| SDMA's 3D assets | The customer's own content. The Extension reads models and writes renders |
| SDMA's asset records | Authoritative metadata. The Extension must not corrupt them |
| AI-generated metadata and embeddings | Derived, recreatable, but reveals what the assets are |
| SDMA write credentials | Short-lived, vended per render, scoped to the asset's CAS prefix |
| Bedrock and DynamoDB spend | An unbounded request costs real money |

## Trust boundaries

```
  [ Browser ]                     [ SDMA ]                [ This Extension ]
       |                             |                            |
   (1) Cognito                   (3) SDMA API              (2) API Gateway
       |                          (4) SDMA S3                     |
       +-- signed-in user            |                     (5) Bedrock
                                 (6) connector                   |
                                                          (7) Extension S3/DDB
```

### 1. Browser → Cognito

The demo UI authenticates against **SDMA's existing** Cognito user pool. This
Extension creates no identity store and holds no credentials of its own.

- **Threat**: an unauthenticated caller reads the asset catalogue.
  **Mitigation**: every API Gateway method uses the Cognito authorizer; there is
  no unauthenticated route. `GatewayResponses` returns 401/403 without leaking
  detail.
- **Accepted risk**: any signed-in SDMA user can search **every** indexed asset.
  The Extension does not re-implement SDMA's per-project authorisation on the
  search path. A deployment where users must not see each other's projects needs
  that added — see *Known gaps*.

### 2. Client → Search API

- **Threat**: an authenticated caller drives cost by requesting a huge page or a
  huge query. **Mitigation**: `limit` is bounded to 100 and `query` to 1000
  characters, matching `api-spec.yaml`; both are rejected with 400.
- **Threat**: injection through the filter values into DynamoDB.
  **Mitigation**: filters are passed as expression **attribute values**, never
  interpolated into an expression string. Attribute *names* come from a fixed
  set, and `style` is aliased as `#style` because it is a DynamoDB reserved
  word. (The sequential `#n0`, `#n1` aliasing is in `shared/asset_jobs.py`,
  which writes this stack's own job table — a different path.)
- **Threat**: prompt injection through the query, reaching Bedrock.
  **Mitigation**: the query is only ever embedded (Titan), never sent to a
  generative model. Tagging prompts are built from rendered images and the
  repository's own config, not from user input.
- **Accepted risk**: CORS is `AllowOrigin: '*'`, which suits a sample whose UI
  runs on `localhost`. Restrict it to your own origin before real use.

### 3. Extension → SDMA API

Requests are SigV4-signed with each function's execution role. The roles grant
`execute-api:Invoke` on `GET/iam/*` — plus `PUT/iam/*` for `finalize-render`
alone, which is the only function that registers files.

- **Threat**: the Extension corrupts SDMA's asset records.
  **Mitigation**: it no longer writes its own attributes there at all. Processing
  state lives in this stack's `AssetJobs` table. Only `finalize-render` writes,
  and only to register the renders it produced.
- **Threat**: a function reaches an asset outside the project it was invoked for.
  **Mitigation**: every path is library- and project-scoped, and the project is
  resolved from the asset rather than taken from the request.

### 4. Extension → SDMA S3

- **Threat**: a bucket-wide `s3:GetObject` lets the Extension read any asset in
  any project regardless of what SDMA would permit.
  **Mitigation**: content is read through the presigned URL `GetFile` issues, so
  the read goes through SDMA's access control. Both remaining grants are
  read-only, and `prepare-render` has none.
- **Threat**: a bucket-wide write grant lets the Extension overwrite arbitrary
  asset content. **Mitigation**: renders are written with the short-lived
  credentials SDMA vends, scoped to that asset's CAS prefix. No function has
  `s3:PutObject` on SDMA's bucket except `finalize-render`, for the manifest.
- **Accepted risk**: the **manifest** is read and written directly, because it is
  not a registered file and `GetFile` cannot address it. This is the one direct
  path, and it is scoped to `finalize-render`.
- **Accepted risk**: the direct-S3 read remains as a fallback when a render is
  not yet registered. It is exercised once per asset.

### 5. Extension → Bedrock

- **Threat**: an over-broad `bedrock:InvokeModel` lets a compromised function
  invoke any model. **Mitigation**: the search role is scoped to the embedding
  model's ARN. The tagging role is broader (inference profiles are required for
  newer models and their ARNs are not known at deploy time) — narrow it if your
  account hosts models this sample should not reach.
- **Threat**: model output is trusted. **Mitigation**: the tagger's response is
  parsed defensively and validated against the configured vocabulary; values
  outside it are corrected rather than stored.

### 6. SDMA connector → Extension

- **Threat**: anything in the account invokes the pipeline.
  **Mitigation**: `AWS::Lambda::Permission` restricts the principal to SDMA's
  connector role and `SourceAccount` to this account.
- **Threat**: a forged payload processes an asset the caller cannot see.
  **Mitigation**: the connector delivers only `assetId`; the project is resolved
  through SDMA's API, which fails for an asset that does not exist.

### 7. Extension's own storage

- **Threat**: the bucket is public, or objects are unencrypted.
  **Mitigation**: `BlockPublicAcls`/`BlockPublicPolicy`/`IgnorePublicAcls`/
  `RestrictPublicBuckets` all true, AES256 at rest, versioning on. Per-asset
  intermediates expire after 7 days.
- **Threat**: secrets in logs. **Mitigation**: `prepare-render` puts SDMA's
  short-lived write credentials in the Step Functions payload, and the handlers
  log that payload; `shared/log_utils.py` redacts credential fields before
  anything reaches CloudWatch. Log groups are declared with bounded retention so
  nothing is kept indefinitely.

## Known gaps

Deliberate, and to address before production use:

| Gap | Consequence |
|---|---|
| Search is not scoped per project or per user | Any signed-in SDMA user sees every indexed asset. Add authorisation on the search path if projects must be isolated |
| `AllowOrigin: '*'` | Any origin may call the API with a valid token. Restrict to your own origin |
| No rate limiting or usage plan | Bounded per request, not per caller. Add an API Gateway usage plan or WAF if cost matters |
| The tagging role's Bedrock grant is broad | Scoped to inference profiles and foundation models in the account, not to specific models |
| Renders are stored unencrypted beyond S3's default | They inherit SDMA's bucket encryption, which the Extension does not control |
| No audit trail of who searched for what | API Gateway access logs record the request, not the result set |
| The container image runs as root | The Lambda base image runs its runtime interface client as root, and Lambda isolates each execution environment. Adding a `USER` is untested here — the clearest remaining hardening step |
| Log groups, tables and environment variables use AWS-managed keys | Not customer-managed. A CMK is a key to manage and pay for; nothing in the environment variables is a secret (table names, model ids, the SDMA endpoint) |
| Functions do not run in a VPC | They hold no inbound surface and reach only AWS APIs. A VPC would require NAT or interface endpoints for Bedrock, S3, DynamoDB and SDMA |
| No point-in-time recovery on either table | Both are reproducible: `AssetVectors` by re-running the pipeline over the assets SDMA still holds, `AssetJobs` is processing state with a 30-day TTL |
| No S3 access logging on the Extension bucket | Would need a second bucket to receive the logs. The bucket holds configs and per-asset intermediates only |
| No API Gateway caching | A cached search would serve results that no longer reflect the index, and the cache is billed per hour. Enable it only if you accept staleness |

## Scanning

[ASH](https://github.com/awslabs/automated-security-helper) runs the scanners this
project is checked with:

```bash
ash --mode local
```

`.ash/.ash.yaml` carries the configuration. Every suppression in it states why
the finding does not apply or why the risk is accepted, and each accepted one has
a row in *Known gaps* above. ASH reports unused suppressions, so a suppression
left behind after the code changes shows up rather than rotting quietly.

Two things to know when reading a scan:

- **Check the per-scanner `Result` column, not just the finding count.** A
  scanner that fails to run reports `ERROR` with zero findings, which looks like
  an improvement. Misconfiguring checkov's `skip_path` did exactly that here and
  silently dropped all 34 of its findings.
- **The scan is only meaningful with build output excluded.** An unfiltered scan
  reported 28 bandit findings, every one of them in `botocore` or `six` under
  `.aws-sam/build/`, which `sam build` copies in. They are not this project's
  code and cannot be fixed here.

## Out of scope

SDMA itself. This Extension does not modify SDMA, and SDMA's own threat model
covers its API, its identity store, and its bucket. The Extension's assumption is
that SDMA's authorisation is correct — which is precisely why content is read
through SDMA rather than around it.
