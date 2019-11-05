# SPDX-License-Identifier: Apache-2.0

import boto3
import json
import logging
import os
from crhelper import CfnResource


RESOURCE_TYPE = 'AWS::ElastiCache::ReplicationGroup'


# Set up the dependencies
logger = logging.getLogger()
helper = CfnResource()
try:
  secrets_manager_client = boto3.client('secretsmanager', endpoint_url=os.environ['SECRETS_MANAGER_ENDPOINT'])
except Exception as e:
  helper.init_failure(e)


@helper.create
@helper.update
def create(event, context):
  resource_properties = event['ResourceProperties']
  secret_id = resource_properties['SecretId']
  target_id = resource_properties['TargetId']
  target_type = resource_properties['TargetType']

  # Validate the provided configuration
  if target_type != RESOURCE_TYPE:
    raise ValueError(f'The specified target type is invalid, it must be "{RESOURCE_TYPE}".')

  # Make sure the current secret exists
  current_dict = _get_secret_dict(secret_id, 'AWSCURRENT')
  logger.info(f'create: Successfully retrieved secret for ARN {secret_id}.')

  # Put back the updated secret
  current_dict['name'] = target_id
  secrets_manager_client.put_secret_value(
    SecretId=secret_id,
    SecretString=json.dumps(current_dict),
    VersionStages=['AWSCURRENT'])
  logger.info(f'create: Successfully put secret for ARN {secret_id}.')


def _get_secret_dict(arn, stage, token=None):
  """Gets the secret dictionary corresponding to the secret arn, stage, and token

  This helper function gets client credentials for the arn and stage passed in and returns the dictionary by parsing the JSON string

  Args:
    arn (string): The secret ARN or other identifier

    token (string): the ClientRequestToken associated with the secret version, or None if no validation is desired

  Returns:
    SecretDictionary: Secret dictionary

  Raises:
    ResourceNotFoundException: If the secret with the specified ARN and stage does not exist

    ValueError: If the secret is not valid JSON

  """
  # Only do VersionId validation against the stage if a token is passed in
  if token:
    secret = secrets_manager_client.get_secret_value(SecretId=arn, VersionId=token, VersionStage=stage)
  else:
    secret = secrets_manager_client.get_secret_value(SecretId=arn, VersionStage=stage)
  return json.loads(secret['SecretString'])
