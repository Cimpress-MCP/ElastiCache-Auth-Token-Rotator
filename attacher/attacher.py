# SPDX-License-Identifier: Apache-2.0

import boto3
import json
import logging
import os
from crhelper import CfnResource


RESOURCE_TYPE = 'AWS::ElastiCache::ReplicationGroup'


# Set up the dependencies
logger = logging.getLogger()
logger.setLevel(logging.INFO)

helper = CfnResource()
try:
  secrets_manager_client = boto3.client('secretsmanager', endpoint_url=os.environ['SECRETS_MANAGER_ENDPOINT'])
  elasticache_client = boto3.client('elasticache')
except Exception as e:
  helper.init_failure(e)


@helper.create
@helper.update
def create_update(event, context):
  """Sets connection information into a specified secret

  This handler completes the final link between the specified Secrets Manager secret and
  the target ElastiCache Replication Group. It populates the secret with the connection
  information for the target for later use by a secret rotation Lambda Function.

  After this handler runs, the Secret String will be a JSON string with the following format:
  {
    "_metadata": {
      "id": <string, required, unique identifier of ElastiCache replication group>,
    },
    "": [ <host>:<port> ],
    "ssl": <required, transit encryption requirement of same>,
    "password": <required, auth token ("password" in redis terms) of same>
    …the remainder of the properties
  }

  Args:
    event (dict): Lambda dictionary of event parameters. These keys must include the following:
      - ResourceProperties: The properties specified on the CloudFormation custom resource
        - SecretId: The unique identifier of the Secrets Manager Secret in which to store connection in formation.
        - TargetId: The unique identifier of the ElastiCache Replication Group whose connection information to query.
        - TargetType: The CloudFormation type of the target resource. Must be 'AWS::ElastiCache::ReplicationGroup'.

    context: (LambdaContext): The Lambda runtime information

  Returns:
    PhysicalResourceId: The unique identifier of the created resource, or None.

  Raises:
    ResourceNotFoundException: if the secret with the specified ARN does not exist

    ResourceNotFoundException: if the replication group with the specified ID does not exist

    ValueError: if the current secret is not valid JSON

    KeyError: if the secret JSON does not contain the expected keys

    KeyError: if the replication group metadata does not contain the expected keys

  """
  resource_properties = event['ResourceProperties']
  secret_id = resource_properties['SecretId']
  target_id = resource_properties['TargetId']
  target_type = resource_properties['TargetType']

  # Validate the provided configuration
  if target_type != RESOURCE_TYPE:
    raise ValueError(f'The specified target type is invalid, it must be "{RESOURCE_TYPE}".')

  # Make sure the current secret exists
  current_dict = _get_secret_dict(secrets_manager_client, secret_id, 'AWSCURRENT')
  logger.info(f'create_update: Successfully retrieved secret for ARN {secret_id}.')

  # Retrieve connection information
  # (We have to be sure that the only connection information stored here is that which cannot
  # be updated without replacement on the Replication Group. Fortunately, they all are.)
  replication_groups_metadata = elasticache_client.describe_replication_groups(ReplicationGroupId=target_id)
  # Getting the first (only) element of this collection is safe because we asked for one in particular.
  replication_group_metadata = replication_groups_metadata['ReplicationGroups'][0]
  end_points = [ node_group['PrimaryEndpoint'] for node_group in replication_group_metadata['NodeGroups'] ]

  # Update the secret dictionary with connection information (generated password already present)
  current_dict['_metadata'] = { 'id': target_id }
  current_dict[''] = [ f"{end_point['Address']}:{end_point['Port']}" for end_point in end_points ]
  # Transit encryption *must* be enabled to be using auth token, but why not.
  current_dict['ssl'] = replication_group_metadata['TransitEncryptionEnabled']

  # Put the updated secret back
  secrets_manager_client.put_secret_value(
    SecretId=secret_id,
    SecretString=json.dumps(current_dict),
    VersionStages=['AWSCURRENT'])
  logger.info(f'create_update: Successfully put secret for ARN {secret_id}.')


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


def handle(event, context):
  helper(event, context)
