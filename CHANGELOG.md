## Version 3.0.0 (Released 2022-06-17)

- The IDs of the secret to be rotated and the replication group whose password to store have been added as required parameters.
  - This being myriad advantages, primarily the removal of `*`-suffixed permissions from IAM.
- The target attachment has been encapsulated by the application.
  - References to it have thus been removed from exports and documentation. It should now be transparent.
- More custom parameters can be passed directly to the rotator, based on name matching.
- The rotator has been upgraded to the runtime "python3.9".
- More configuration parameters have been made optional, with reasonable default values.
