# Contributing

## First-time setup

```bash
git clone https://github.com/misanthropics-ai/0815.git
cd 0815
make bootstrap
source .venv/bin/activate
```

Use Python 3.10 or newer. The setup script creates `.venv` and installs both runtime and
development dependencies.

## Before opening a pull request

```bash
make format
make check
make frontend
```

The same checks, frontend builds, and a backend container smoke test run in GitHub Actions
for every pull request targeting `main`. Pull requests never receive AWS deployment access.

## AWS and Bedrock

Copy only the non-secret defaults if you need to customize them:

```bash
cp .env.example .env
```

Use temporary AWS environment variables or an AWS profile for credentials. Never commit
access keys, session tokens, or `.env` files. After authenticating, verify Bedrock with:

```bash
make bedrock
```

AWS deployment uses GitHub OIDC and an EC2 instance role. Do not add long-lived AWS keys to
GitHub Secrets; repository deployment configuration belongs in non-secret GitHub Variables.
Only a successful CI run on `main` can assume the deployment role.

## Contract changes

`contracts/openapi.yaml`, `contracts/schemas.py`, and `contracts/types.ts` are the shared
contracts. Any change must include corresponding mock fixture updates. The
`contract-samples/` directory is retained only as historical reference. Run `make contract`
before requesting review.
