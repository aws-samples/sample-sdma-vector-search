#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""List every asset id in an SDMA project, following the API's pagination.

The SDMA CLI's ``asset list`` cannot do this. It fetches one page and ignores the
``nextToken``, so it never sees past the first page -- and because the API applies
``maxResults`` *before* filtering out assets in a transient state, that page can
come back empty while later pages still hold assets. Deleting a project's assets
through the CLI alone therefore stops partway and the project delete then fails
with "still contains assets".

This walks the same REST API the Extension's Lambda functions use, so cleanup
does not depend on reading SDMA's DynamoDB tables.

Usage:
  list-assets.py <api-endpoint> <library-id> <project-id> [--region REGION]

Prints one asset id per line. Exits non-zero on an API error, so a caller can
tell "no assets" from "could not tell".
"""
import argparse
import json
import sys
import urllib.parse

import boto3
import urllib3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

# A project with more assets than this is beyond what this sample is sized for,
# and an unbounded loop on a paginating API is worse than a clear failure.
MAX_PAGES = 100
PAGE_SIZE = 100


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('endpoint', help='SDMA API endpoint, no trailing slash')
    parser.add_argument('library_id')
    parser.add_argument('project_id')
    parser.add_argument('--region', default=None)
    args = parser.parse_args()

    session = boto3.Session(region_name=args.region)
    region = session.region_name
    if not region:
        print('could not resolve an AWS region', file=sys.stderr)
        return 2
    credentials = session.get_credentials()
    if credentials is None:
        print('no AWS credentials available', file=sys.stderr)
        return 2
    frozen = credentials.get_frozen_credentials()

    http = urllib3.PoolManager()
    signer = SigV4Auth(frozen, 'execute-api', region)
    base = (f"{args.endpoint.rstrip('/')}/iam/libraries/{args.library_id}"
            f"/projects/{args.project_id}/assets")

    token = None
    for _ in range(MAX_PAGES):
        url = f'{base}?maxResults={PAGE_SIZE}'
        if token:
            url += '&nextToken=' + urllib.parse.quote(token, safe='')

        request = AWSRequest(method='GET', url=url)
        signer.add_auth(request)
        response = http.request('GET', url, headers=dict(request.headers))
        if response.status != 200:
            print(f'ListAssets returned HTTP {response.status}', file=sys.stderr)
            return 1

        body = json.loads(response.data) if response.data else {}
        for asset in body.get('assets') or []:
            asset_id = asset.get('assetId')
            if asset_id:
                print(asset_id)

        token = body.get('nextToken')
        if not token:
            return 0

    print(f'stopped after {MAX_PAGES} pages; assets may remain', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
