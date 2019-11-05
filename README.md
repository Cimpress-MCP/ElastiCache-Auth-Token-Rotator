# Auth Token Rotation

## What It Is

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
      TransitEncryptionEnabled: true # Required to be true in order to use AuthToken.
      AuthToken: !Sub '{{resolve:secretsmanager:${ExampleSecret}::authToken}}'
  ExampleSecretRotator:
    Type: AWS::Serverless::Application
    Properties:
      Location:
        ApplicationId: arn:aws:serverlessrepo:us-east-1:820870426321:applications/auth-token-rotator
        SemanticVersion: 1.0.0
      Parameters:
        Endpoint: !Sub https://secretsmanager.${AWS::Region}.${AWS::URLSuffix}
        FunctionName: secret-rotator
        VpcSecurityGroupIds: !Ref SecurityGroup
        VpcSubnetIds: !Join
        - ','
        - - !Ref Subnet1
          - !Ref Subnet2
          - !Ref Subnet3
  ExampleSecretRotatorInvokePermission:
    Type: AWS::Lambda::Permission
    Properties:
      FunctionName: !GetAtt ExampleSecretRotator.Outputs.RotationLambdaARN
      Action: lambda:InvokeFunction
      Principal: !Sub secretsmanager.${AWS::URLSuffix}
  ExampleSecret:
    Type: AWS::SecretsManager::Secret
    Properties:
      Description: An example auth token.
      GenerateSecretString:
        GenerateStringKey: authToken
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
    Type: AWS::SecretsManager::RotationSchedule
    Properties:
      RotationLambdaARN: !GetAtt ExampleSecretRotator.Outputs.RotationLambdaARN
      RotationRules:
        AutomaticallyAfterDays: 30
      SecretId: !Ref ExampleSecret
# snip
```

## Helpful Links

* [AWS Secrets Manager][]
* [AWS ElastiCache][]

[AWS Secrets Manager]: https://aws.amazon.com/secrets-manager/
[AWS ElastiCache]: https://aws.amazon.com/elasticache/

## Inspirations

* AWS's [Rotation Lambda Functions][] for RDS credentials
* The CloudFormation [Custom Resource Helper][] library

[Rotation Lambda Functions]: https://github.com/aws-samples/aws-secrets-manager-rotation-lambdas
[Custom Resource Helper]: https://github.com/aws-cloudformation/custom-resource-helper
