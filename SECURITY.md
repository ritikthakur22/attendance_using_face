# Security policy

FaceTrack is a local development application, not a production biometric
security system. The default server has no authentication or HTTPS and must not
be exposed directly to a network.

## Reporting a vulnerability

Report vulnerabilities privately to the repository owner through GitHub's
private vulnerability reporting feature when it is enabled. Include:

- affected version or commit;
- reproduction steps;
- potential impact;
- suggested mitigation, if known.

Do not include real face images, databases, credentials, or attendance records.

## Supported versions

Security fixes apply to the latest version on the default branch.

## Deployment expectations

Before production use, add authentication, authorization, TLS, audit logging,
rate limiting, encrypted backups, retention controls, anti-spoofing, and a
non-biometric attendance alternative.
