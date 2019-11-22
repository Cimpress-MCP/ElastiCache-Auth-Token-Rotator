# SPDX-License-Identifier: Apache-2.0

import boto3
import json
import logging
import os
import time
from redis import Redis, RedisError


# Auth tokens:
# >Must be only printable ASCII characters.
# >The only permitted printable special characters are !, &, #, $, ^, <, >, and -.
EXCLUDE_CHARACTERS = r'''"%'()*+,./:;=?@[\]_`{|}~'''


# Set up the dependencies
logger = logging.getLogger()
secrets_manager_client = boto3.client('secretsmanager', endpoint_url=os.environ['SECRETS_MANAGER_ENDPOINT'])
elasticache_client = boto3.client('elasticache')

def handle(event, context):
  """Secrets Manager ElastiCache Replication Group Auth Token Rotator

  This handler uses the ElastiCache management API to rotate a replication group's auth token. This rotation
  scheme uses the ROTATE auth token update strategy, ensuring that locally cached credentials will continue
  to be accepted for a short period.

  The Secret SecretString is expected to be a JSON string with the following format:
  {
    "id": <required, unique identifier of ElastiCache replication group>,
    "host": <required, address of same>,
    "port": <required, port of same>,
    "ssl": <required, transit encryption requirement of same>,
    "password": <required, auth token ("password" in redis terms) of same>
  }

  Args:
    event (dict): Lambda dictionary of event parameters. These keys must include the following:
      - SecretId: The secret ARN or identifier
      - ClientRequestToken: The ClientRequestToken of the secret version
      - Step: The rotation step (one of "createSecret", "setSecret", "testSecret", or "finishSecret")

    context: (LambdaContext): The Lambda runtime information

  Raises:
    ResourceNotFoundException: If the secret with the specified ARN and stage does not exist

    ValueError: If the secret is not properly configured for rotation

    KeyError: If the secret JSON does not contain the expected keys

  """
  arn = event['SecretId']
  token = event['ClientRequestToken']
  step = event['Step']

  # Make sure that the version is staged correctly
  secret_metadata = secrets_manager_client.describe_secret(SecretId=arn)
  if 'RotationEnabled' in secret_metadata and not secret_metadata['RotationEnabled']:
    raise ValueError(f'Secret {arn} is not enabled for rotation.')
  versions = secret_metadata['VersionIdsToStages']
  if token not in versions:
    raise ValueError(f'Secret version {token} has no stage for rotation of secret {arn}.')
  if 'AWSCURRENT' in versions[token]:
    logger.info(f'Secret version {token} is already set as AWSCURRENT for secret {arn}.')
    return
  elif 'AWSPENDING' not in versions[token]:
    raise ValueError(f'Secret version {token} is not set as AWSPENDING for rotation of secret {arn}.')

  # Call the appropriate step
  if step == 'createSecret':
    create_secret(arn, token)
  elif step == 'setSecret':
    set_secret(arn, token)
  elif step == 'testSecret':
    test_secret(arn, token)
  elif step == 'finishSecret':
    finish_secret(arn, token)
  else:
    raise ValueError(f'handle: Invalid step parameter {step} for secret {arn}.')


def create_secret(arn, token):
  """Generate a new secret

  This method first checks for the existence of a secret for the passed-in token. If one does not exist, it will generate a
  new secret and put it with the passed-in token.

  Args:
    arn (string): The secret ARN or other identifier

    token (string): The ClientRequestToken associated with the secret version

  Raises:
    ValueError: if the current secret is not valid JSON

    KeyError: if the secret JSON does not contain the expected keys

  """
  # Make sure the current secret exists
  current_dict = _get_secret_dict(arn, 'AWSCURRENT')

  # Now try to get the secret version. If that fails, put a new secret
  try:
    _get_secret_dict(arn, 'AWSPENDING', token)
    logger.info(f'create_secret: Successfully retrieved secret for {arn}.')
  except secrets_manager_client.exceptions.ResourceNotFoundException:
    # Generate a random auth token according to length recommendations and allowed character set
    auth_token = secrets_manager_client.get_random_password(PasswordLength=64, ExcludeCharacters=EXCLUDE_CHARACTERS)
    current_dict['password'] = auth_token['RandomPassword']

    # Put the secret
    secrets_manager_client.put_secret_value(
      SecretId=arn,
      ClientRequestToken=token,
      SecretString=json.dumps(current_dict),
      VersionStages=['AWSPENDING'])
    logger.info(f'create_secret: Successfully put secret for ARN {arn} and version {token}.')


def set_secret(arn, token):
  """Set the pending secret as the auth token

  This method tries to create a redis client and get its ID with the AWSPENDING secret and returns on success.
  If that fails, it tries again with the AWSCURRENT and AWSPREVIOUS secrets. If either one succeeds, it sets
  the AWSPENDING secret as the auth token. Otherwise, it raises a ValueError.

  Args:
    arn (string): The secret ARN or other identifier

    token (string): The ClientRequestToken associated with the secret version

  Raises:
    ResourceNotFoundException: If the secret with the specified ARN and stage does not exist

    ValueError: If the secret is not valid JSON or valid credentials are not found to connect to redis

    KeyError: If the secret JSON does not contain the expected keys

  """
  # First try to log in with the pending secret. If it succeeds, return
  pending_dict = _get_secret_dict(arn, 'AWSPENDING', token)
  pong = _ping_redis(pending_dict)
  if pong:
    logger.info(f'set_secret: AWSPENDING secret is already set as auth token for secret {arn}.')
    return

  # Now try the current secret
  pong = _ping_redis(_get_secret_dict(arn, 'AWSCURRENT'))
  if not pong:
    # If both current and pending do not work, try previous
    try:
      redis_client_id = _ping_redis(_get_secret_dict(arn, 'AWSPREVIOUS'))
    except secrets_manager_client.exceptions.ResourceNotFoundException:
      redis_client_id = None

  # If we still don't have an access token, complain bitterly
  if not redis_client_id:
    raise ValueError(f'set_secret: Unable to connect to redis with previous, current, or pending secret of secret arn {arn}!')

  # Now set the auth token to the pending auth token
  replication_group_metadata = elasticache_client.modify_replication_group(
    ReplicationGroupId=pending_dict['id'],
    AuthToken=pending_dict['password'],
    AuthTokenUpdateStrategy='ROTATE',
    ApplyImmediately=True)
  # note(cosborn) Despite 'ApplyImmediately', it does take a hot moment to apply the new auth token.
  while 'AuthTokenStatus' in replication_group_metadata['ReplicationGroup']['PendingModifiedValues']:
    time.sleep(5)
    replication_groups_metadata = elasticache_client.describe_replication_groups(ReplicationGroupId=pending_dict['id'])
    replication_group_metadata['ReplicationGroup'] = replication_groups_metadata['ReplicationGroups'][0]


def test_secret(arn, token):
  """Test the pending secret by creating an access token

  This method tries to acquire an access token with the secrets staged with AWSPENDING.

  Args:
      arn (string): The secret ARN or other identifier

      token (string): The ClientRequestToken associated with the secret version

  Raises:
      ResourceNotFoundException: If the secret with the specified arn and stage does not exist

      ValueError: If the secret is not valid JSON or valid credentials are not found to connect to redis

      KeyError: If the secret json does not contain the expected keys

  """
  # Try to acquire an acccess token with the pending secret
  redis_client_id = _ping_redis(_get_secret_dict(arn, 'AWSPENDING', token))
  if not redis_client_id:
    raise ValueError(f'test_secret: Unable to acquire redis client id with pending secret of secret ARN {arn}.')


def finish_secret(arn, token):
  """Finish the rotation by marking the pending secret as current

  This method finishes the secret rotation by staging the secret staged AWSPENDING with the AWSCURRENT stage.

  Args:
      arn (string): The secret ARN or other identifier

      token (string): The ClientRequestToken associated with the secret version

  """
  # First describe the secret to get the current version
  metadata = secrets_manager_client.describe_secret(SecretId=arn)
  current_version = None
  for version in metadata['VersionIdsToStages']:
      if 'AWSCURRENT' in metadata['VersionIdsToStages'][version]:
          if version == token:
            # The correct version is already marked as current, return
            logger.info(f'finishSecret: Version {version} already marked as AWSCURRENT for {arn}.')
            return
          current_version = version
          break

  # Finalize by staging the secret version current
  secrets_manager_client.update_secret_version_stage(
    SecretId=arn,
    VersionStage='AWSCURRENT',
    MoveToVersionId=token,
    RemoveFromVersionId=current_version)
  logger.info(f'finish_secret: Successfully set AWSCURRENT stage to version {token} for secret {arn}.')


def _ping_redis(secret_dict):
  """Pings redis from a secret dictionary

  This helper function tries to create an active redis client, grabbing credential info
  from the secret dictionary. If successful, it returns "PONG", otherwise None.

  Args:
    secret_dict (dict): The secret dictionary

  Returns:
    string: "PONG" or None

  Raises:
    KeyError: If the secret JSON does not contain the expected keys.

  """
  try:
    with Redis(**secret_dict) as redis_client:
      return redis_client.ping()
  except RedisError:
    return None


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

    KeyError: If a required field is not found in the secret JSON

  """
  required_fields = ['id', 'host', 'port', 'ssl', 'password']

  # Only do VersionId validation against the stage if a token is passed in
  if token:
    secret = secrets_manager_client.get_secret_value(SecretId=arn, VersionId=token, VersionStage=stage)
  else:
    secret = secrets_manager_client.get_secret_value(SecretId=arn, VersionStage=stage)
  plaintext = secret['SecretString']
  secret_dict = json.loads(plaintext)

  # Run validations against the secret
  for field in required_fields:
    if field not in secret_dict:
      raise KeyError(f'{field} key is missing from secret JSON')

  return secret_dict
