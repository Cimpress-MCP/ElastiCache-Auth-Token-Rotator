# ElastiCache AuthToken Rotation

[![Find it on the Serverless Application Repository][logo]][sam]

[logo]: https://img.shields.io/badge/SAM-Find%20it%20on%20the%20Serverless%20Application%20Repository-brightgreen
[sam]: https://serverlessrepo.aws.amazon.com/applications/arn:aws:serverlessrepo:us-east-1:820870426321:applications~platform-client-secret-rotator

## What It Is

The ElastiCache AuthToken Rotator is an AWS Secrets Manager [Lambda Function Rotator][] intended to be used with AWS Secrets Manager and AWS ElastiCache. Secrets Manager can use rotators implemented as Lambda Functions to securely and automatically rotate secret configuration values.

This rotator can only be used with ElastiCache instances created as Replication Groups (`AWS::ElastiCache::ReplicationGroup`) because those created as plain cache clusters (`AWS::ElastiCache::CacheCluster`) do not support user-specified auth tokens.

[Lambda Function Rotator]: https://docs.aws.amazon.com/secretsmanager/latest/userguide/rotating-secrets.html

## Why You Want It

For good security hygiene, secret values should be rotated regularly. But _it's a pain_. And once the secret value is rotated wherever it's stored, how can that be injected into the application which requires the value? This is the value propsition of AWS Secrets Manager, and that value is augmented by the ability to write custom rotators. With this rotator configured to rotate a secret, the auth token will never be stale and it will never be out of date. You should configure your application to retrieve the secret just-in-time at runtime. Provide the ARN of the secret via some configuration means (though setting an environment variable in CloudFormation is probably best), and no further configuration is required, either before or after rotation.

## How To Use It

Here's an example use, provided in AWS Cloudformation:

```yaml
# snip

Transform: AWS::Serverless-2016-10-31

# snip
Resources:
  ExampleCache:
    Type: AWS::ElastiCache::ReplicationGroup
    Properties:
      # snip
      TransitEncryptionEnabled: true # Required to be true in order to use the AuthToken property.
      AuthToken: !Sub '{{resolve:secretsmanager:${ExampleSecret}::password}}'
  ExampleSecretRotator:
    Type: AWS::Serverless::Application
    Properties:
      Location:
        ApplicationId: arn:aws:serverlessrepo:us-east-1:820870426321:applications/elasticache-auth-token-rotator
        SemanticVersion: 2.0.8
      Parameters:
        Endpoint: !Sub https://secretsmanager.${AWS::Region}.${AWS::URLSuffix}
        FunctionName: secret-rotator
        KmsKeyArn: !GetAtt ExampleKey.Arn
        VpcSecurityGroupIds: !Ref SecurityGroup
        VpcSubnetIds: !Join
        - ','
        - [ !Ref Subnet1, !Ref Subnet2, !Ref Subnet3 ]
  ExampleSecret:
    Type: AWS::SecretsManager::Secret
    Properties:
      Description: An example replication group connection secret.
      GenerateSecretString:
        SecretStringTemplate: '{}'
        GenerateStringKey: password
        PasswordLength: 64
        ExcludeCharacters: |-
          "%'()*+,./:;=?@[\]_`{|}~
  ExampleSecretTargetAttachment:
    Type: Custom::TargetAttachment
    Properties:
      ServiceToken: !GetAtt ExampleSecretRotator.Outputs.AttachmentLambdaARN
      SecretId: !Ref ExampleSecret
      TargetId: !Ref ExampleCache
      TargetType: AWS::ElastiCache::ReplicationGroup
  ExampleSecretRotationSchedule:
    DependsOn: ExampleSecretTargetAttachment
    Type: AWS::SecretsManager::RotationSchedule
    Properties:
      RotationLambdaARN: !GetAtt ExampleSecretRotator.Outputs.RotationLambdaARN
      RotationRules:
        AutomaticallyAfterDays: 15
      SecretId: !Ref ExampleSecret
# snip
```

### Huh? What's a "TargetAttachment"?

Looking at the sample CloudFormation template above: Absent an intermediary, the resource `ExampleSecret` would need the connection information for the cache, and the resource `ExampleCache` needs the value of the secret. Because the rotator needs the secret to contain information about the cache which needs the value of the secret, we've created a circular dependency -- and one which CloudFormation can't detect, because of the dynamic reference to the secret in the cache.

The Lambda Function exposed at the output attribute `AttachmentLambdaARN` is used to create a [CloudFormation custom resource][] which will complete the final link between a Secrets Manager secret and its associated cache. The resource will populate the secret with the required information so that the rotation Lambda Function can function.

[CloudFormation custom resource]: https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/template-custom-resources.html

### Why `DependsOn`?

It takes a long time for an ElastiCache Replication Group to be created. This explicit `DependsOn` prevents the rotator from failing continually while the replication group resource is not yet created. It's totally optional, but it should soothe your frazzled DevOps nerves not to see the meaningless invocation failures.

## Helpful Links

* [AWS Secrets Manager][]
* [AWS ElastiCache][]
* [AWS::SecretsManager::SecretTargetAttachment][]

[AWS Secrets Manager]: https://aws.amazon.com/secrets-manager/
[AWS ElastiCache]: https://aws.amazon.com/elasticache/
[AWS::SecretsManager::SecretTargetAttachment]: https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-resource-secretsmanager-secrettargetattachment.html

## Inspirations

* AWS's [Rotation Lambda Functions][] for RDS credentials
* The CloudFormation [Custom Resource Helper][] library

[Rotation Lambda Functions]: https://github.com/aws-samples/aws-secrets-manager-rotation-lambdas
[Custom Resource Helper]: https://github.com/aws-cloudformation/custom-resource-helper
