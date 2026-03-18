# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""AWS client initialization and configuration"""
import boto3

# Initialize AWS clients
s3_client = boto3.client('s3')
bedrock_client = boto3.client('bedrock-runtime')
dynamodb = boto3.resource('dynamodb')
dynamodb_client = boto3.client('dynamodb')
