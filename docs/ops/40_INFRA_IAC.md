# Provenance — Infrastructure and Deployment Specification

Purpose: the complete, buildable definition of every AWS resource, every CockroachDB Cloud provisioning command, every SQL grant, every environment variable, and every teardown step that the Provenance hackathon deployment requires.

Status: planning complete v1.1
Implementation status: not started

Audience: whoever runs `cdk deploy` and `ccloud cluster create` for the first time; backend engineers wiring `services/control_plane` and `workers/` to real resources; agent engineers configuring Bedrock AgentCore Runtime and the CockroachDB Cloud Managed MCP Server; reviewers auditing the Product Readiness criterion; whoever tears the account down after the submission deadline.

Authority: `CANONICAL_DECISIONS.md` and `00_PRODUCT.md` bind every name below. Within technical concerns, `specs/10_DATABASE_DDL.md` owns roles, grants, views, and the vector index; `specs/15_API_SPEC.md` owns endpoints, Cognito clients, scopes, event routing, and the outbox state machine; `quality/23_PHASE_GATES.md` owns the exit assertions this document must satisfy. This document owns only the *deployment shape* of those decisions, and it renegotiates none of them.

Nothing in this document has been deployed. No cloud resource, container image, IAM role, cluster, or DNS record described here exists yet. Every command is written to be run, not reported as having been run.

---

## 0. Contents

1. [Ground rules, naming, region, tagging](#1-ground-rules-naming-region-tagging)
2. [CDK stack layout, cross-stack references, deploy order](#2-cdk-stack-layout-cross-stack-references-deploy-order)
3. [Cognito](#3-cognito)
4. [S3](#4-s3)
5. [SES](#5-ses)
6. [EventBridge, Scheduler, SQS](#6-eventbridge-scheduler-sqs)
7. [Lambda workers](#7-lambda-workers)
8. [App Runner and ECR](#8-app-runner-and-ecr)
9. [Bedrock AgentCore Runtime](#9-bedrock-agentcore-runtime)
10. [Amplify Hosting](#10-amplify-hosting)
11. [CockroachDB Cloud](#11-cockroachdb-cloud)
12. [Environment variable manifest](#12-environment-variable-manifest)
13. [Cost controls](#13-cost-controls)
14. [Teardown](#14-teardown)
15. [Risks and open questions](#15-risks-and-open-questions)

---

## 1. Ground rules, naming, region, tagging

### 1.1 Region

`us-east-1`, single region, per `implementation/01_SYSTEM_ARCHITECTURE_DETAILED.md` §3.1. Every resource in this document lives there. There is no second region and no cross-region reference. `us-east-1` is also an Amazon SES email-receiving region, which matters because inbound SES receipt rules cannot be created in a region that does not support receiving.

Multi-region application compute is an explicit non-goal (`00_PRODUCT.md` §6). CockroachDB's multi-region capability is described truthfully in `ARCHITECTURE.md` §17.2 and is not deployed.

### 1.2 Naming rules

| Kind | Pattern | Example |
|---|---|---|
| CloudFormation stack | `Pv<Domain>Stack` | `PvMessagingStack` |
| Physical AWS resource | `provenance-<thing>[-<region>]` | `provenance-domain-bus`, `provenance-artifacts-us-east-1` |
| Lambda function | `provenance-<worker_module>` | `provenance-ses-ingest` |
| IAM role | `provenance-<principal>-role` | `provenance-apprunner-instance-role` |
| Secrets Manager secret | `provenance/<domain>` with JSON keys | `provenance/db` |
| SSM parameter | `/provenance/<stack>/<key>` | `/provenance/data/artifact-bucket-arn` |
| CloudWatch log group | `/provenance/<component>` | `/provenance/control-plane` |
| SQL role | `pv_*` | `pv_kernel_writer` |
| Cognito app client | `provenance-<audience>` | `provenance-agent-runtime` |
| AgentCore runtime | `provenance_agents` | underscores only; the AgentCore name grammar rejects hyphens |

Hyphens, never em dashes, in every AWS resource name, tag value, and CloudFormation description. Names are lowercase except CloudFormation logical ids.

### 1.3 Mandatory tags

Applied at the `App` level so nothing escapes them. The teardown verification in §14.5 depends on `Project=Provenance` being present on every taggable resource.

```typescript
// infra/cdk/bin/provenance.ts
import { App, Tags, Environment } from 'aws-cdk-lib';

const env: Environment = { account: process.env.CDK_DEFAULT_ACCOUNT, region: 'us-east-1' };
const app = new App();

Tags.of(app).add('Project', 'Provenance');
Tags.of(app).add('Component', 'hackathon');
Tags.of(app).add('Owner', process.env.PV_OWNER ?? 'unset');
Tags.of(app).add('CostCenter', 'crdb-aws-agentic-memory-hackathon');
Tags.of(app).add('DeleteAfter', '2026-10-15');
```

`DeleteAfter` is not enforcement, it is a note to a future reader of the bill. Enforcement is §14.

### 1.4 Bootstrap and prerequisites

```bash
# One time, per account/region.
npm ci --prefix infra/cdk
npx cdk bootstrap aws://$(aws sts get-caller-identity --query Account --output text)/us-east-1

# Verify the four model ids this build depends on are actually invokable in this account.
# CANONICAL_DECISIONS.md "Bedrock model access" probe. Fixture mode is dev-only; live
# submission stays blocked until this returns all three.
for m in anthropic.claude-haiku-4-5 anthropic.claude-opus-5 amazon.titan-embed-text-v2:0; do
  aws bedrock get-foundation-model --model-identifier "$m" --region us-east-1 \
    --query 'modelDetails.[modelId,modelLifecycle.status]' --output text || echo "MISSING $m"
done
```

Record the output in `ops/cluster-probe.txt` alongside the CockroachDB probes from `specs/10_DATABASE_DDL.md` §1. A missing model id is a Phase 0 stop condition, not something to work around at deploy time.

### 1.5 What is deliberately absent

- **No VPC for application compute.** App Runner uses default public egress; CockroachDB Cloud Basic has no IP allowlist to satisfy, so a VPC connector would add cost and cold-start latency and buy nothing. §8.5 states the production path.
- **No API Gateway.** App Runner terminates TLS and serves `/v1` and `/internal/v1` on one listener (`specs/15_API_SPEC.md` §1.1). `/internal/v1` is auth-isolated, not network-isolated, and the specification says so out loud.
- **No WAF.** Rate limiting is in-process and acknowledged as a gap in `specs/15_API_SPEC.md` §17.1.
- **No DynamoDB, no ElastiCache, no OpenSearch.** A second copy of canonical state is a listed anti-pattern (`implementation/01_SYSTEM_ARCHITECTURE_DETAILED.md` §16).
- **No CI/CD pipeline resources.** Deploys are run from a workstation or GitHub Actions using short-lived credentials. A CodePipeline would be a fifth thing to tear down for zero rubric value.

---

## 2. CDK stack layout, cross-stack references, deploy order

### 2.1 Stacks and what lives in each

Ten stacks. The split is chosen so that the two stacks most likely to be redeployed during the build (`PvComputeStack`, `PvApiStack`) contain no stateful resource, and so that nothing stateful shares a stack with anything that gets torn down and rebuilt.

| # | Stack | Contains | Stateful |
|---|---|---|---|
| 1 | `PvFoundationStack` | KMS CMK `provenance-artifacts-key`; CloudWatch log groups; SSM parameter namespace; AWS Budgets and the billing alarm | yes (KMS) |
| 2 | `PvIdentityStack` | Cognito user pool, hosted UI domain, resource server `provenance`, seven custom scopes, three app clients, `provenance-judges` group, post-confirmation Lambda + its role | yes (user pool) |
| 3 | `PvDataStack` | S3 `provenance-artifacts-us-east-1`, S3 `provenance-inbound-us-east-1`, lifecycle rules, bucket policies, ECR `provenance/control-plane`, ECR `provenance/agent-runtime`, Secrets Manager secrets | yes (buckets, secrets) |
| 4 | `PvMessagingStack` | EventBridge bus `provenance-domain-bus`, five rules, four SQS queues, four DLQs, Scheduler groups `provenance-triggers` and `provenance-system`, Scheduler invocation role | no |
| 5 | `PvComputeStack` | Nine Lambda functions, their roles and inline policies, SNS topic `provenance-textract-status`, event source mappings, async on-failure destinations | no |
| 6 | `PvApiStack` | App Runner service `provenance-control-plane`, autoscaling configuration, instance role, access role, custom domain association | no |
| 7 | `PvEmailStack` | SES domain identity `provenance.app`, MAIL FROM `mail.provenance.app`, identity `in.provenance.app`, receipt rule set, receipt rule, configuration set, verified demo sink identity | partially (identities) |
| 8 | `PvAgentStack` | Bedrock AgentCore Runtime `provenance_agents` (L1/custom resource), its execution role, its inbound JWT authorizer configuration | no |
| 9 | `PvWebStack` | Amplify app `provenance-web`, branch `main`, domain association `app.provenance.app`, custom headers | no |
| 10 | `PvObservabilityStack` | CloudWatch dashboard `provenance-ops`, the alarms `G13.7` asserts, metric filters, OTEL log-group subscriptions | no |

`infra/agentcore/` holds the AgentCore container Dockerfile and the runtime configuration JSON that `PvAgentStack` reads. It is not a second IaC tool; it is input to stack 8.

### 2.2 Cross-stack references

Rule: **props in, no `Fn::ImportValue` written by hand.** Each stack takes a typed props interface and receives concrete constructs or ARNs from `bin/provenance.ts`. CDK creates the exports it needs. Two exceptions, both deliberate:

1. **SES receipt rule to Lambda is a genuine cycle.** `PvEmailStack`'s receipt rule needs the `provenance-ses-ingest` function ARN; the function needs the inbound bucket ARN and grants on it. Resolved by dependency direction, not by an SSM indirection: `PvDataStack` (bucket) → `PvComputeStack` (function, granted read on the bucket) → `PvEmailStack` (rule, referencing the function). No stack needs anything from a stack that depends on it.
2. **App Runner needs the AgentCore runtime ARN, and AgentCore needs the App Runner URL.** This *is* a cycle and it is broken with SSM at runtime rather than at synth time. `PvApiStack` publishes `/provenance/api/base-url`; `PvAgentStack` reads it with a `ssm.StringParameter.valueForStringParameter` lookup at deploy time (stack 8 deploys after stack 6). `PvApiStack` receives `AGENTCORE_RUNTIME_ARN` as a *deferred* value: it is written to `/provenance/agent/runtime-arn` by stack 8 and injected into App Runner by a one-line `update-service` in the deploy script (§2.4 step 9). Trying to express this as a CDK reference produces a deadly embrace that no amount of restructuring removes, because the two services genuinely call each other.

```typescript
// infra/cdk/lib/props.ts
import { IBucket } from 'aws-cdk-lib/aws-s3';
import { IUserPool, IUserPoolClient } from 'aws-cdk-lib/aws-cognito';
import { IKey } from 'aws-cdk-lib/aws-kms';
import { IEventBus } from 'aws-cdk-lib/aws-events';
import { IQueue } from 'aws-cdk-lib/aws-sqs';
import { ISecret } from 'aws-cdk-lib/aws-secretsmanager';
import { IRepository } from 'aws-cdk-lib/aws-ecr';

export interface PvFoundationExports {
  readonly artifactKey: IKey;
  readonly controlPlaneLogGroupName: string;
}

export interface PvIdentityExports {
  readonly userPool: IUserPool;
  readonly webClient: IUserPoolClient;
  readonly agentClient: IUserPoolClient;
  readonly workerClient: IUserPoolClient;
  readonly issuer: string;              // https://cognito-idp.us-east-1.amazonaws.com/<poolId>
  readonly hostedUiDomain: string;      // provenance-auth.auth.us-east-1.amazoncognito.com
}

export interface PvDataExports {
  readonly artifactBucket: IBucket;
  readonly inboundBucket: IBucket;
  readonly controlPlaneRepo: IRepository;
  readonly agentRepo: IRepository;
  readonly dbSecret: ISecret;           // provenance/db
  readonly cryptoSecret: ISecret;       // provenance/crypto
  readonly mcpSecret: ISecret;          // provenance/mcp
}

export interface PvMessagingExports {
  readonly bus: IEventBus;
  readonly advocateQueue: IQueue;
  readonly actionQueue: IQueue;
  readonly workerDlq: IQueue;
  readonly schedulerDlq: IQueue;
  readonly triggerScheduleGroupName: string;   // provenance-triggers
  readonly systemScheduleGroupName: string;    // provenance-system
  readonly schedulerInvokeRoleArn: string;
}
```

### 2.3 Removal policies

Stateful resources carry `RemovalPolicy.RETAIN` **during the build** and are switched to `DESTROY` only by the teardown branch in §14. A `cdk destroy` run by accident in week two must not delete the seeded artifact bucket or the Cognito pool that the demo users live in.

```typescript
// infra/cdk/lib/removal.ts
import { RemovalPolicy } from 'aws-cdk-lib';

// PV_TEARDOWN=1 is set only by ops/teardown.sh. Nothing else may set it.
export const STATEFUL_REMOVAL: RemovalPolicy =
  process.env.PV_TEARDOWN === '1' ? RemovalPolicy.DESTROY : RemovalPolicy.RETAIN;

export const AUTO_DELETE_OBJECTS: boolean = process.env.PV_TEARDOWN === '1';
```

### 2.4 Deploy order

```bash
# ops/deploy.sh — ordered, idempotent, safe to re-run.
set -euo pipefail
export AWS_REGION=us-east-1
export PV_GIT_SHA="$(git rev-parse HEAD)"
cd infra/cdk

# 1. Foundation: KMS, log groups, budgets. Nothing depends on anything.
npx cdk deploy PvFoundationStack --require-approval never

# 2. Identity: user pool must exist before anything can validate a token.
npx cdk deploy PvIdentityStack --require-approval never

# 3. Data: buckets, ECR repos, secrets. Secrets are created EMPTY here; values are
#    written in step 4 so no secret material ever appears in a CDK template.
npx cdk deploy PvDataStack --require-approval never

# 4. Populate secrets out of band. See §11.4 for the CockroachDB URLs.
ops/secrets-populate.sh

# 5. Messaging: bus, rules, queues, scheduler groups. Rule targets are attached in step 6.
npx cdk deploy PvMessagingStack --require-approval never

# 6. Build and push both container images BEFORE the compute that runs them.
ops/build-push.sh control-plane "$PV_GIT_SHA"
ops/build-push.sh agent-runtime "$PV_GIT_SHA"

# 7. Compute: Lambda workers. Needs buckets, queues, secrets, user pool.
npx cdk deploy PvComputeStack --require-approval never

# 8. API: App Runner. AGENTCORE_RUNTIME_ARN is intentionally a placeholder here.
npx cdk deploy PvApiStack --require-approval never

# 9. Agents: AgentCore runtime; reads /provenance/api/base-url written by step 8.
npx cdk deploy PvAgentStack --require-approval never
#    Close the cycle described in §2.2 exception 2.
ops/apprunner-set-agent-arn.sh

# 10. Email: receipt rule referencing the ses_ingest function from step 7.
npx cdk deploy PvEmailStack --require-approval never

# 11. Web: Amplify. Needs the API base URL and the Cognito client id.
npx cdk deploy PvWebStack --require-approval never

# 12. Observability last, so alarms are created against metrics that already exist and
#     therefore reach OK rather than sitting in INSUFFICIENT_DATA (G13.7).
npx cdk deploy PvObservabilityStack --require-approval never

# 13. G13.1 — drift check. Any difference here is a gate failure, not a note.
npx cdk diff --all
```

Database migrations are **not** in this script. They run as `pv_migrator` from `ops/migrate.sh` between steps 4 and 7, because App Runner's health check will fail against a schema-less cluster and the Lambda workers have nothing to do until the schema exists. Migration ordering is owned by `specs/10_DATABASE_DDL.md` §16.

### 2.5 Representative CDK for the non-obvious constructs

The obvious constructs (a bucket, a queue, a log group) are omitted; L2 defaults plus the properties named in the sections below are sufficient. Everything below is a construct where the default is wrong, an L2 does not exist, or the property that matters is easy to miss.

Nothing in this file is generated. Every snippet is written to be copied into `infra/cdk/lib/` and compiled.

---

## 3. Cognito

Owner of the contract: `specs/15_API_SPEC.md` §2. This section deploys it and nothing more.

### 3.1 User pool

```typescript
// infra/cdk/lib/identity-stack.ts
import { Stack, StackProps, Duration, RemovalPolicy } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import {
  UserPool, UserPoolClient, UserPoolClientIdentityProvider, UserPoolDomain,
  OAuthScope, ResourceServerScope, UserPoolEmail, AccountRecovery, Mfa,
  AdvancedSecurityMode, ClientAttributes, CfnUserPoolGroup,
} from 'aws-cdk-lib/aws-cognito';
import { STATEFUL_REMOVAL } from './removal';

export class PvIdentityStack extends Stack {
  constructor(scope: Construct, id: string, props: StackProps) {
    super(scope, id, props);

    const pool = new UserPool(this, 'UserPool', {
      userPoolName: 'provenance-users',
      selfSignUpEnabled: true,
      signInAliases: { email: true },
      signInCaseSensitive: false,
      autoVerify: { email: true },
      standardAttributes: { email: { required: true, mutable: false } },
      passwordPolicy: {
        minLength: 12, requireLowercase: true, requireUppercase: true,
        requireDigits: true, requireSymbols: true,
        tempPasswordValidity: Duration.days(3),
      },
      mfa: Mfa.OPTIONAL,
      mfaSecondFactor: { sms: false, otp: true },
      accountRecovery: AccountRecovery.EMAIL_ONLY,
      advancedSecurityMode: AdvancedSecurityMode.AUDIT,
      email: UserPoolEmail.withCognito(),   // demo scale; see §5.6 on SES sandbox
      removalPolicy: STATEFUL_REMOVAL,
      deletionProtection: process.env.PV_TEARDOWN !== '1',
    });

    // The hosted UI domain. This exact host appears in specs/15_API_SPEC.md §1.1 and in
    // NEXT_PUBLIC_COGNITO_DOMAIN; changing it breaks the documented token endpoint.
    const domain = new UserPoolDomain(this, 'HostedUi', {
      userPool: pool,
      cognitoDomain: { domainPrefix: 'provenance-auth' },
    });

    new CfnUserPoolGroup(this, 'JudgesGroup', {
      userPoolId: pool.userPoolId,
      groupName: 'provenance-judges',
      description: 'Grants judge_mode_enabled on the HumanPrincipal. No cross-user visibility.',
      precedence: 10,
    });
```

`advancedSecurityMode: AUDIT` rather than `ENFORCED`: enforced mode can challenge a sign-in during a recorded demo, and a blocked login at 0:05 of a three-minute video is a worse outcome than an unmitigated credential-stuffing risk on a five-user pool. Audit mode still populates the risk telemetry.

### 3.2 Resource server and the seven scopes

The scope list is closed. It is copied from `specs/15_API_SPEC.md` §2.1 verbatim; adding an eighth scope requires editing that document first.

```typescript
    const scopeDefs: Record<string, ResourceServerScope> = {
      memoryRead:      new ResourceServerScope({ scopeName: 'memory/read',      scopeDescription: 'Read canonical memory and read models' }),
      memoryPropose:   new ResourceServerScope({ scopeName: 'memory/propose',   scopeDescription: 'Submit a typed MemoryProposal to the kernel' }),
      actionPropose:   new ResourceServerScope({ scopeName: 'action/propose',   scopeDescription: 'Create a draft ActionIntent' }),
      ingestWrite:     new ResourceServerScope({ scopeName: 'ingest/write',     scopeDescription: 'Register artifacts and admit evidence' }),
      triggerEvaluate: new ResourceServerScope({ scopeName: 'trigger/evaluate', scopeDescription: 'Evaluate a prospective trigger predicate' }),
      actionExecute:   new ResourceServerScope({ scopeName: 'action/execute',   scopeDescription: 'Execute an approved external action' }),
      outboxDispatch:  new ResourceServerScope({ scopeName: 'outbox/dispatch',  scopeDescription: 'Claim and publish outbox events' }),
    };

    const resourceServer = pool.addResourceServer('ApiResourceServer', {
      identifier: 'provenance',            // scopes render as provenance.memory/read
      userPoolResourceServerName: 'provenance-api',
      scopes: Object.values(scopeDefs),
    });

    const rs = (k: keyof typeof scopeDefs) =>
      OAuthScope.resourceServer(resourceServer, scopeDefs[k]);
```

The scope strings that the API enforces are therefore exactly:

```text
provenance.memory/read
provenance.memory/propose
provenance.action/propose
provenance.ingest/write
provenance.trigger/evaluate
provenance.action/execute
provenance.outbox/dispatch
```

The `/` inside a scope name must be percent-encoded when a token request body is hand-built (`specs/15_API_SPEC.md` §2.2). This is a client concern, not an infrastructure one, but it is the single most common way this configuration appears broken when it is correct.

### 3.3 The three app clients

```typescript
    // ---- 1. provenance-web: human, public client, authorization code + PKCE, NO secret.
    const webClient = pool.addClient('WebClient', {
      userPoolClientName: 'provenance-web',
      generateSecret: false,                       // public client; a secret in a browser is not a secret
      authFlows: { userSrp: true, userPassword: false, custom: false, adminUserPassword: false },
      oAuth: {
        flows: { authorizationCodeGrant: true, implicitCodeGrant: false, clientCredentials: false },
        scopes: [OAuthScope.OPENID, OAuthScope.EMAIL, OAuthScope.PROFILE, rs('memoryRead')],
        callbackUrls: ['https://app.provenance.app/auth/callback', 'http://localhost:3000/auth/callback'],
        logoutUrls:   ['https://app.provenance.app/',              'http://localhost:3000/'],
      },
      supportedIdentityProviders: [UserPoolClientIdentityProvider.COGNITO],
      preventUserExistenceErrors: true,
      enableTokenRevocation: true,
      accessTokenValidity: Duration.minutes(60),
      idTokenValidity: Duration.minutes(60),
      refreshTokenValidity: Duration.days(30),
      authSessionValidity: Duration.minutes(3),
      readAttributes: new ClientAttributes().withStandardAttributes({ email: true, emailVerified: true }),
      writeAttributes: new ClientAttributes().withStandardAttributes({ email: false }),
    });
```

PKCE is not a Cognito toggle. It is a property of the request: because `generateSecret` is false and the flow is `authorizationCodeGrant`, Cognito **requires** `code_challenge` with `code_challenge_method=S256` on `/oauth2/authorize` and `code_verifier` on `/oauth2/token`. The infrastructure guarantee is the absence of a client secret; the frontend must send the challenge, and `apps/web` uses the Amplify Auth or `oidc-client-ts` code path that does so by default. A deployment that generated a secret here would silently allow a non-PKCE flow, which is why `generateSecret: false` is load-bearing rather than cosmetic.

```typescript
    // ---- 2. provenance-agent-runtime: machine, client credentials, secret in Secrets Manager.
    const agentClient = pool.addClient('AgentRuntimeClient', {
      userPoolClientName: 'provenance-agent-runtime',
      generateSecret: true,
      authFlows: {},                               // no user auth flow at all
      oAuth: {
        flows: { authorizationCodeGrant: false, implicitCodeGrant: false, clientCredentials: true },
        scopes: [rs('memoryRead'), rs('memoryPropose'), rs('actionPropose'), rs('ingestWrite')],
        callbackUrls: [],
      },
      accessTokenValidity: Duration.minutes(60),   // matches the documented expires_in: 3600
      enableTokenRevocation: true,
    });

    // ---- 3. provenance-workers: machine, client credentials, secret in Secrets Manager.
    const workerClient = pool.addClient('WorkersClient', {
      userPoolClientName: 'provenance-workers',
      generateSecret: true,
      authFlows: {},
      oAuth: {
        flows: { authorizationCodeGrant: false, implicitCodeGrant: false, clientCredentials: true },
        scopes: [rs('ingestWrite'), rs('triggerEvaluate'), rs('actionExecute'),
                 rs('outboxDispatch'), rs('memoryRead')],
        callbackUrls: [],
      },
      accessTokenValidity: Duration.minutes(60),
      enableTokenRevocation: true,
    });
```

Scope allocation, restated as the deployed truth:

| App client | Grant | Secret | Scopes |
|---|---|---|---|
| `provenance-web` | authorization code + PKCE | none | `openid`, `email`, `profile`, `provenance.memory/read` |
| `provenance-agent-runtime` | client credentials | `provenance/cognito` → `agent_client_secret` | `provenance.memory/read`, `provenance.memory/propose`, `provenance.action/propose`, `provenance.ingest/write` |
| `provenance-workers` | client credentials | `provenance/cognito` → `worker_client_secret` | `provenance.ingest/write`, `provenance.trigger/evaluate`, `provenance.action/execute`, `provenance.outbox/dispatch`, `provenance.memory/read` |

`provenance-agent-runtime` holding `provenance.ingest/write` is deliberate and is the resolution `specs/15_API_SPEC.md` §9.0 footnote requires: §9.4 (`POST /internal/v1/agent-runs/{id}/evidence`) is the one place the agent must register evidence, and the narrowing is done by `CLIENT_CAPABILITY_MATRIX`, which permits that client to present only an `AGENT_RUN` capability. The scope alone does not let it reach §9.1, which demands an `INGEST_ALIAS` capability the agent client may never present. The §2.1 scope table lists three scopes for this client; §9.0 is the more specific statement and wins. That tension is recorded in §15.2 below.

`provenance-agent-runtime` holds **no** `provenance.action/execute` and **no** `provenance.outbox/dispatch`. The graph that writes a dispute letter is structurally incapable of sending it.

### 3.4 Post-confirmation provisioning Lambda

`specs/15_API_SPEC.md` §2.5 forbids auto-creating users from an API call. Provisioning is a pool trigger.

```typescript
    const postConfirm = new lambda.Function(this, 'PostConfirmation', {
      functionName: 'provenance-cognito-post-confirmation',
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset('../../workers/cognito_post_confirmation'),
      memorySize: 512,
      timeout: Duration.seconds(20),
      environment: {
        COCKROACH_DATABASE_URL_SECRET_ARN: props.dbSecretArn,
        INGEST_ALIAS_HMAC_KEY_ARN: props.cryptoSecretArn,
        SES_INGEST_DOMAIN: 'in.provenance.app',
        OTEL_SERVICE_NAME: 'provenance-cognito-post-confirmation',
      },
      logGroup: postConfirmLogGroup,
    });
    pool.addTrigger(UserPoolOperation.POST_CONFIRMATION, postConfirm);
```

It writes `tenants`, `users`, and `ingest_aliases` in **one** transaction as `pv_app_reader_writer` (all three tables carry `INSERT, UPDATE` for that role per `specs/10_DATABASE_DDL.md` §12). A failure here fails the sign-up, which is correct: a confirmed Cognito user with no `users` row produces `403 USER_NOT_PROVISIONED` on every request, and it is better to make the user retry sign-up than to leave them permanently broken.

### 3.5 The exact JWKS validation settings the API enforces

`specs/15_API_SPEC.md` §2.3 defines the order of checks. This is the deployed configuration of those checks, as a typed settings object so no value is a literal in a handler.

```python
# packages/python/provenance_contracts/settings.py  (auth subset)
from __future__ import annotations
from datetime import timedelta
from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="forbid", frozen=True)

    cognito_user_pool_id: str = Field(alias="COGNITO_USER_POOL_ID")
    cognito_issuer: HttpUrl = Field(alias="COGNITO_ISSUER")
    cognito_jwks_url: HttpUrl = Field(alias="COGNITO_JWKS_URL")
    cognito_web_client_id: str = Field(alias="COGNITO_WEB_CLIENT_ID")
    cognito_agent_client_id: str = Field(alias="COGNITO_AGENT_CLIENT_ID")
    cognito_worker_client_id: str = Field(alias="COGNITO_WORKER_CLIENT_ID")

    # --- frozen validation policy: these are NOT tunable per environment -------------
    allowed_algorithms: frozenset[str] = frozenset({"RS256"})
    required_token_use: str = "access"                 # ID tokens are never accepted
    clock_skew: timedelta = timedelta(seconds=60)
    jwks_cache_ttl: timedelta = timedelta(hours=12)
    jwks_refresh_min_interval: timedelta = timedelta(minutes=5)
    jwks_fetch_timeout: timedelta = timedelta(seconds=3)
    verify_audience: bool = False                      # Cognito access tokens carry client_id, not aud
    verify_issuer: bool = True
    verify_signature: bool = True
    require_scope_claim: bool = True

    @property
    def public_route_client_ids(self) -> frozenset[str]:
        return frozenset({self.cognito_web_client_id})

    @property
    def internal_route_client_ids(self) -> frozenset[str]:
        return frozenset({self.cognito_agent_client_id, self.cognito_worker_client_id})
```

```python
# services/control_plane/app/auth/jwks.py
import time
import httpx
from jose import jwt                      # python-jose[cryptography]
from jose.utils import base64url_decode   # noqa: F401  (documented dependency)


class JwksCache:
    """One instance per process. Thread-safe by being append-only per kid."""

    def __init__(self, settings: AuthSettings) -> None:
        self._s = settings
        self._keys: dict[str, dict] = {}
        self._fetched_at: float = 0.0
        self._last_forced: float = 0.0

    async def key_for(self, kid: str) -> dict:
        now = time.monotonic()
        expired = (now - self._fetched_at) > self._s.jwks_cache_ttl.total_seconds()
        if kid not in self._keys or expired:
            cooled = (now - self._last_forced) > self._s.jwks_refresh_min_interval.total_seconds()
            if kid not in self._keys and not cooled and not expired:
                # Unknown kid, refresh already attempted within 5 minutes: fail closed.
                raise ApiError("TOKEN_INVALID_SIGNATURE", 401, details={"reason": "UNKNOWN_KID"})
            await self._refresh()
        try:
            return self._keys[kid]
        except KeyError:
            raise ApiError("TOKEN_INVALID_SIGNATURE", 401, details={"reason": "UNKNOWN_KID"})

    async def _refresh(self) -> None:
        self._last_forced = time.monotonic()
        timeout = self._s.jwks_fetch_timeout.total_seconds()
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(str(self._s.cognito_jwks_url))
            r.raise_for_status()
        self._keys = {k["kid"]: k for k in r.json()["keys"]}
        self._fetched_at = time.monotonic()


def decode_access_token(token: str, key: dict, s: AuthSettings) -> dict:
    claims = jwt.decode(
        token,
        key,
        algorithms=list(s.allowed_algorithms),
        issuer=str(s.cognito_issuer),
        options={
            "verify_signature": s.verify_signature,
            "verify_aud": s.verify_audience,     # Cognito access tokens have no aud claim
            "verify_iss": s.verify_issuer,
            "verify_exp": True,
            "verify_nbf": True,
            "require_exp": True,
        },
        # python-jose applies leeway to exp and nbf only, which is what §2.3 step 6 wants.
        leeway=int(s.clock_skew.total_seconds()),
    )
    if claims.get("token_use") != s.required_token_use:
        raise ApiError("TOKEN_INVALID_SIGNATURE", 401,
                       details={"reason": "ID_TOKEN_NOT_ACCEPTED"})
    return claims
```

The two settings most often wrong, stated explicitly:

- **`verify_aud: False` is correct, not lax.** A Cognito *access* token has no `aud` claim; it has `client_id`. Enabling audience verification makes every valid token fail. The equivalent control is step 7 of §2.3: `client_id` must be in the allow-set for the route class, which `AuthSettings.public_route_client_ids` and `internal_route_client_ids` encode.
- **The JWKS URL is derived, never configured loosely.** `COGNITO_JWKS_URL` must equal `https://cognito-idp.us-east-1.amazonaws.com/{COGNITO_USER_POOL_ID}/.well-known/jwks.json`, and `COGNITO_ISSUER` must equal the same URL without the well-known suffix. A settings validator asserts the prefix relationship at container start so a copy-paste error fails at boot instead of at the first request.

---

## 4. S3

### 4.1 The two buckets

| Bucket | Purpose | Written by | Read by |
|---|---|---|---|
| `provenance-artifacts-us-east-1` | user-uploaded artifact bytes and derived parser output | browser (pre-signed `PUT`), `provenance-ses-ingest`, `provenance-textract-complete`, control plane | control plane, agent runtime (never directly; only via control plane) |
| `provenance-inbound-us-east-1` | raw inbound MIME written by SES | Amazon SES service principal only | `provenance-ses-ingest` |

`00_PRODUCT.md` §2.3 illustrates the artifact bucket as `provenance-artifacts-use1`. That is prose shorthand inside a worked example. The deployed name is `provenance-artifacts-us-east-1`, which is what `specs/15_API_SPEC.md` §8.18 puts in the pre-signed URL and §9.1 puts in the `s3_bucket` request field, and the API contract is the thing code depends on. Recorded in §15.1.

### 4.2 Key layout

Every key is chosen by the server. The user-supplied filename never appears in a key.

```text
provenance-artifacts-us-east-1
  raw/{tenant_id}/{user_id}/{artifact_id}/original
  normalized/{tenant_id}/{user_id}/{artifact_id}/parser-v{n}.json
  normalized/{tenant_id}/{user_id}/{artifact_id}/textract-raw.json
  ses/{yyyy}/{mm}/{dd}/{ses_message_id}-{sha256_prefix8}          <- canonical inbound copy

provenance-inbound-us-east-1
  ses/incoming/{ses_message_id}                                    <- SES staging write only
```

Three properties this layout buys, in order of importance:

1. **Tenant and user are the first two path segments.** Every IAM policy, every bucket-policy condition, and every future cross-account restriction can be written as a prefix condition instead of an object-level lookup. It is also what makes a lawful-deletion implementation a prefix delete rather than an index scan (`00_PRODUCT.md` §6 reserves room for this without building it).
2. **`artifact_id` is a directory, not a filename.** Parser output, Textract raw output, and any future derived rendition live beside the original under the same immutable prefix, so nothing needs a second lookup to find the derivatives of an artifact.
3. **`raw/` and `ses/` are never overwritten and never expired.** Invariant 1 is *evidence is append-only*, and a lifecycle rule that expires an artifact would silently make a State Proof unciteable four months later.

### 4.3 Bucket construct

```typescript
// infra/cdk/lib/data-stack.ts (artifact bucket)
import { Bucket, BucketEncryption, BlockPublicAccess, ObjectOwnership, HttpMethods } from 'aws-cdk-lib/aws-s3';
import { Duration } from 'aws-cdk-lib';
import { PolicyStatement, Effect, AnyPrincipal, ServicePrincipal } from 'aws-cdk-lib/aws-iam';
import { STATEFUL_REMOVAL, AUTO_DELETE_OBJECTS } from './removal';

const artifacts = new Bucket(this, 'ArtifactBucket', {
  bucketName: 'provenance-artifacts-us-east-1',
  blockPublicAccess: BlockPublicAccess.BLOCK_ALL,
  publicReadAccess: false,
  objectOwnership: ObjectOwnership.BUCKET_OWNER_ENFORCED,   // ACLs disabled entirely
  encryption: BucketEncryption.KMS,
  encryptionKey: props.artifactKey,
  bucketKeyEnabled: true,                                   // S3 Bucket Keys: fewer KMS calls, lower cost
  enforceSSL: true,                                         // adds the aws:SecureTransport deny
  minimumTLSVersion: 1.2,
  versioned: true,                                          // append-only insurance, see §4.4
  removalPolicy: STATEFUL_REMOVAL,
  autoDeleteObjects: AUTO_DELETE_OBJECTS,
  lifecycleRules: [
    {
      // Derived parser output is regenerable from raw/. It is the only prefix that expires.
      id: 'expire-normalized-parser-output',
      enabled: true,
      prefix: 'normalized/',
      expiration: Duration.days(90),
      noncurrentVersionExpiration: Duration.days(7),
    },
    {
      // Raw artifacts are never expired. They only get cheaper.
      id: 'cool-raw-artifacts',
      enabled: true,
      prefix: 'raw/',
      transitions: [{ storageClass: StorageClass.INTELLIGENT_TIERING, transitionAfter: Duration.days(30) }],
    },
    {
      id: 'cool-inbound-mime',
      enabled: true,
      prefix: 'ses/',
      transitions: [{ storageClass: StorageClass.INTELLIGENT_TIERING, transitionAfter: Duration.days(30) }],
    },
    {
      // A pre-signed PUT that was abandoned mid-multipart leaves billable parts behind.
      id: 'abort-incomplete-multipart',
      enabled: true,
      abortIncompleteMultipartUploadAfter: Duration.days(1),
    },
    {
      // Versioning is insurance against accidental overwrite, not an archive.
      id: 'expire-noncurrent-raw-versions',
      enabled: true,
      prefix: 'raw/',
      noncurrentVersionExpiration: Duration.days(30),
    },
  ],
  cors: [{
    // The browser PUTs directly to S3 (specs/15_API_SPEC.md §8.18 step 2).
    allowedOrigins: ['https://app.provenance.app', 'http://localhost:3000'],
    allowedMethods: [HttpMethods.PUT],
    allowedHeaders: ['content-type', 'x-amz-server-side-encryption', 'x-amz-checksum-sha256'],
    exposedHeaders: ['etag', 'x-amz-checksum-sha256'],
    maxAge: 300,
  }],
});
```

### 4.4 Bucket policy

`enforceSSL` and `BLOCK_ALL` cover the two default-wrong cases. Three more statements are added explicitly, because each one closes a hole that the L2 defaults leave open.

```typescript
// 1. Refuse any PUT that is not encrypted with OUR key. Without this, a client can
//    upload with SSE-S3 and the object silently leaves the KMS audit trail.
artifacts.addToResourcePolicy(new PolicyStatement({
  sid: 'DenyWrongEncryption',
  effect: Effect.DENY,
  principals: [new AnyPrincipal()],
  actions: ['s3:PutObject'],
  resources: [artifacts.arnForObjects('*')],
  conditions: {
    StringNotEquals: {
      's3:x-amz-server-side-encryption-aws-kms-key-id': props.artifactKey.keyArn,
    },
  },
}));

artifacts.addToResourcePolicy(new PolicyStatement({
  sid: 'DenyUnencryptedObjectUploads',
  effect: Effect.DENY,
  principals: [new AnyPrincipal()],
  actions: ['s3:PutObject'],
  resources: [artifacts.arnForObjects('*')],
  conditions: { StringNotEquals: { 's3:x-amz-server-side-encryption': 'aws:kms' } },
}));

// 2. Evidence is append-only. Nothing in the running system may delete an object or a
//    version. Only the teardown role may, and only when PV_TEARDOWN built the stack.
artifacts.addToResourcePolicy(new PolicyStatement({
  sid: 'DenyDeleteExceptTeardown',
  effect: Effect.DENY,
  notPrincipals: [new ArnPrincipal(`arn:aws:iam::${this.account}:role/provenance-teardown-role`)],
  actions: ['s3:DeleteObject', 's3:DeleteObjectVersion', 's3:PutBucketVersioning',
            's3:PutLifecycleConfiguration'],
  resources: [artifacts.bucketArn, artifacts.arnForObjects('raw/*'), artifacts.arnForObjects('ses/*')],
}));
```

The delete-deny is scoped to `raw/*` and `ses/*` only. `normalized/*` must remain deletable, because the lifecycle rule in §4.3 deletes it and a lifecycle expiration is evaluated against the bucket policy.

S3 Object Lock would express append-only more strongly than a policy statement, and it is deliberately not enabled: Object Lock cannot be turned on after bucket creation, cannot be turned off, and makes teardown require waiting out the retention period. A policy statement plus versioning gets the same practical guarantee for a hackathon and can be removed on 15 October. Recorded in §15.4.

### 4.5 Inbound bucket policy

The inbound bucket accepts writes from exactly one principal: the SES service, and only for this account.

```typescript
const inbound = new Bucket(this, 'InboundBucket', {
  bucketName: 'provenance-inbound-us-east-1',
  blockPublicAccess: BlockPublicAccess.BLOCK_ALL,
  objectOwnership: ObjectOwnership.BUCKET_OWNER_ENFORCED,
  encryption: BucketEncryption.KMS,
  encryptionKey: props.artifactKey,
  bucketKeyEnabled: true,
  enforceSSL: true,
  minimumTLSVersion: 1.2,
  versioned: false,
  removalPolicy: STATEFUL_REMOVAL,
  autoDeleteObjects: AUTO_DELETE_OBJECTS,
  lifecycleRules: [{
    // Staging only. ses_ingest copies the canonical object into the artifact bucket
    // under the immutable dated key; this copy is a duplicate and may expire.
    id: 'expire-ses-staging',
    enabled: true,
    prefix: 'ses/incoming/',
    expiration: Duration.days(7),
  }],
});

inbound.addToResourcePolicy(new PolicyStatement({
  sid: 'AllowSesInboundPut',
  effect: Effect.ALLOW,
  principals: [new ServicePrincipal('ses.amazonaws.com')],
  actions: ['s3:PutObject'],
  resources: [inbound.arnForObjects('ses/incoming/*')],
  conditions: {
    StringEquals: { 'aws:SourceAccount': this.account },
    StringLike:   { 'aws:SourceArn': `arn:aws:ses:us-east-1:${this.account}:receipt-rule-set/provenance-inbound-rules:receipt-rule/*` },
  },
}));
```

`aws:SourceAccount` plus `aws:SourceArn` is not optional. A bucket policy that allows `ses.amazonaws.com` without them lets any AWS customer's SES receipt rule write into this bucket, which for an evidence store is an evidence-injection vulnerability, not a noisy-neighbour problem.

The KMS key policy needs the matching grant, or SES writes fail with `AccessDenied` and the failure surfaces as silently missing mail:

```typescript
props.artifactKey.addToResourcePolicy(new PolicyStatement({
  sid: 'AllowSesEncrypt',
  effect: Effect.ALLOW,
  principals: [new ServicePrincipal('ses.amazonaws.com')],
  actions: ['kms:GenerateDataKey', 'kms:Encrypt'],
  resources: ['*'],
  conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
}));
```

### 4.6 Pre-signed URL constraints

`specs/15_API_SPEC.md` §8.18 fixes the contract. This is the generator that satisfies it.

```python
# services/control_plane/app/ingestion/presign.py
from __future__ import annotations
import base64
import uuid
from datetime import datetime, timedelta, timezone

import boto3
from botocore.config import Config

# specs/15_API_SPEC.md §8.18. Executables and archives are refused outright.
ALLOWED_MIME_TYPES: frozenset[str] = frozenset({
    "application/pdf",
    "image/png",
    "image/jpeg",
    "text/plain",
    "message/rfc822",
})

MAX_ARTIFACT_BYTES = 20 * 1024 * 1024        # 20 MiB
UPLOAD_URL_TTL = timedelta(minutes=15)
DOWNLOAD_URL_TTL = timedelta(minutes=5)

_s3 = boto3.client(
    "s3",
    region_name="us-east-1",
    config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"},
                  retries={"max_attempts": 3, "mode": "standard"}),
)


def artifact_raw_key(tenant_id: uuid.UUID, user_id: uuid.UUID, artifact_id: uuid.UUID) -> str:
    """The ONLY function permitted to build a raw artifact key."""
    return f"raw/{tenant_id}/{user_id}/{artifact_id}/original"


def presign_upload(
    *, bucket: str, kms_key_arn: str,
    tenant_id: uuid.UUID, user_id: uuid.UUID, artifact_id: uuid.UUID,
    mime_type: str, size_bytes: int, sha256_hex: str | None,
) -> dict:
    if mime_type not in ALLOWED_MIME_TYPES:
        raise ApiError("UNSUPPORTED_MIME_TYPE", 422,
                       details={"received": mime_type, "allowed": sorted(ALLOWED_MIME_TYPES)})
    if not (1 <= size_bytes <= MAX_ARTIFACT_BYTES):
        raise ApiError("PAYLOAD_TOO_LARGE", 413,
                       details={"max_bytes": MAX_ARTIFACT_BYTES, "received_bytes": size_bytes})

    key = artifact_raw_key(tenant_id, user_id, artifact_id)

    params: dict = {
        "Bucket": bucket,
        "Key": key,                                       # server-chosen, never client-chosen
        "ContentType": mime_type,                         # signed: a PUT with another type fails
        "ContentLength": size_bytes,                      # signed: see the caveat below
        "ServerSideEncryption": "aws:kms",
        "SSEKMSKeyId": kms_key_arn,
    }
    required_headers = {
        "Content-Type": mime_type,
        "x-amz-server-side-encryption": "aws:kms",
    }
    if sha256_hex is not None:
        checksum_b64 = base64.b64encode(bytes.fromhex(sha256_hex)).decode()
        params["ChecksumSHA256"] = checksum_b64            # S3 rejects a mismatched body itself
        required_headers["x-amz-checksum-sha256"] = checksum_b64

    url = _s3.generate_presigned_url(
        "put_object", Params=params,
        ExpiresIn=int(UPLOAD_URL_TTL.total_seconds()), HttpMethod="PUT",
    )
    return {
        "upload_url": url,
        "http_method": "PUT",
        "required_headers": required_headers,
        "max_size_bytes": MAX_ARTIFACT_BYTES,
        "expires_at": (datetime.now(timezone.utc) + UPLOAD_URL_TTL),
        "s3_key": key,
    }
```

The five constraints, and honestly which of them the URL itself enforces:

| Constraint | Enforced by | Strength |
|---|---|---|
| Exact key, chosen by the server | the signature covers the path | absolute. Changing one character invalidates the signature, so a client cannot redirect an upload into another tenant's prefix. |
| MIME allowlist | `ALLOWED_MIME_TYPES` check plus signed `Content-Type` | absolute for the header; the *bytes* are not sniffed at upload. The parser is the thing that decides what the file actually is, and it treats content as hostile either way. |
| 15-minute expiry | `X-Amz-Expires` in the signature | absolute. |
| SSE-KMS with our key | signed headers plus the two `Deny` statements in §4.4 | absolute, and it is belt-and-braces on purpose: the bucket policy catches a URL generated by future code that forgot the headers. |
| 20 MiB size cap | signed `Content-Length`, then `HeadObject` at `/complete` | **partial.** A SigV4 pre-signed `PUT` cannot express a range the way a POST policy's `content-length-range` can. Including `ContentLength` in `Params` puts `content-length` into the signed header set, so a body of a different length is rejected by S3 — this is the behaviour to confirm in the Phase 0 probe below. Whatever the outcome, `POST /v1/artifacts/{id}/complete` performs `HeadObject` and returns `422 ARTIFACT_SIZE_MISMATCH` on any disagreement, so an oversized object can be *stored* but can never be *admitted*, and the never-completed sweeper deletes it within 24 hours. |

```bash
# Phase 0 probe: does a signed content-length actually reject a mismatched body?
# Run after PvDataStack. Expected: the second PUT fails with 403 SignatureDoesNotMatch.
python - <<'PY'
import boto3, requests
s3 = boto3.client("s3", region_name="us-east-1")
url = s3.generate_presigned_url("put_object", Params={
    "Bucket": "provenance-artifacts-us-east-1",
    "Key": "raw/probe/probe/probe/original",
    "ContentType": "text/plain", "ContentLength": 4,
    "ServerSideEncryption": "aws:kms",
    "SSEKMSKeyId": "<artifact key arn>"}, ExpiresIn=900, HttpMethod="PUT")
h = {"Content-Type": "text/plain", "x-amz-server-side-encryption": "aws:kms"}
print("exact  :", requests.put(url, data=b"abcd", headers=h).status_code)   # expect 200
print("oversz :", requests.put(url, data=b"abcdefgh", headers=h).status_code) # expect 403
PY
```

Record the result in `ops/cluster-probe.txt`. If the oversized `PUT` returns `200`, the size cap is enforced solely at `/complete`, the sweeper becomes load-bearing, and that fact must appear in the Product Readiness narrative rather than being quietly assumed away.

Download URLs are the mirror image: `generate_presigned_url("get_object", ...)` with a 5-minute TTL, generated only after the owning-user check in `specs/15_API_SPEC.md` §8.20, and never returned for an artifact the principal does not own. Bytes are never proxied through the API.

---

## 5. SES

### 5.1 Inbound: identities, rule set, receipt rule

```typescript
// infra/cdk/lib/email-stack.ts
import { CfnReceiptRuleSet, CfnReceiptRule, CfnEmailIdentity, CfnConfigurationSet,
         CfnConfigurationSetEventDestination } from 'aws-cdk-lib/aws-ses';

// The ingest domain. MX must point at SES inbound for us-east-1.
new CfnEmailIdentity(this, 'IngestDomainIdentity', {
  emailIdentity: 'in.provenance.app',
  dkimAttributes: { signingEnabled: true },
});

const ruleSet = new CfnReceiptRuleSet(this, 'InboundRuleSet', {
  ruleSetName: 'provenance-inbound-rules',
});

const rule = new CfnReceiptRule(this, 'IngestRule', {
  ruleSetName: ruleSet.ruleSetName!,
  rule: {
    name: 'provenance-ingest-rule',
    enabled: true,
    scanEnabled: true,                  // populates spamVerdict and virusVerdict
    tlsPolicy: 'Optional',              // see the note below
    recipients: ['in.provenance.app'],  // whole domain: aliases are resolved in the database
    actions: [
      {
        s3Action: {
          bucketName: props.inboundBucket.bucketName,
          objectKeyPrefix: 'ses/incoming/',
          kmsKeyArn: props.artifactKey.keyArn,
        },
      },
      {
        lambdaAction: {
          functionArn: props.sesIngestFunctionArn,
          invocationType: 'Event',      // async: SES must not wait on the control plane
        },
      },
    ],
  },
});
rule.addDependency(ruleSet);
```

Four choices worth defending:

- **`recipients: ['in.provenance.app']`, one rule for the whole domain.** Per-address receipt rules would mean a CDK deploy on every user sign-up, which is absurd. The alias is resolved in `ingest_aliases` by HMAC (§5.2), so the routing decision lives in the database where it can be rotated and disabled in one statement (`specs/15_API_SPEC.md` §8.22).
- **`scanEnabled: true`.** Without it, `spamVerdict` and `virusVerdict` are absent, and §9.1 step 3's rejection rule has nothing to evaluate.
- **S3 action before Lambda action.** Actions run in order. The Lambda receives the SES event containing the object key and expects the object to already exist.
- **`tlsPolicy: 'Optional'`.** `Require` would bounce mail from any sending MTA that will not do STARTTLS. That is the right production setting and the wrong demo setting: a bounced hero artifact is unrecoverable in a live demo, and the transport security of an inbound counterparty invoice is not what the product's threat model depends on. The verdicts are captured either way. This is a deliberate demo concession, recorded in §15.5.

DNS, which CDK does not own because the domain is registered outside this account:

```text
in.provenance.app.   MX    10 inbound-smtp.us-east-1.amazonaws.com.
in.provenance.app.   TXT   "v=spf1 include:amazonses.com -all"
_dmarc.provenance.app. TXT "v=DMARC1; p=none; rua=mailto:dmarc@provenance.app"
<dkim-token-1>._domainkey.in.provenance.app. CNAME <token>.dkim.amazonses.com.
<dkim-token-2>._domainkey.in.provenance.app. CNAME <token>.dkim.amazonses.com.
<dkim-token-3>._domainkey.in.provenance.app. CNAME <token>.dkim.amazonses.com.
```

The rule set must be activated; creating it is not enough, and this is the single most common reason inbound SES appears configured and silently does nothing:

```bash
aws ses set-active-receipt-rule-set --rule-set-name provenance-inbound-rules --region us-east-1
aws ses describe-active-receipt-rule-set --region us-east-1 \
  --query 'Metadata.Name' --output text     # → provenance-inbound-rules
```

### 5.2 Opaque ingest alias and alias-hash storage

`implementation/01_SYSTEM_ARCHITECTURE_DETAILED.md` §5.1 requires an opaque alias that does not encode the user UUID or email. `specs/10_DATABASE_DDL.md` §3.3 fixes the storage: `ingest_aliases.alias_hash BYTES`, `length = 32`, `UNIQUE`, and the plaintext token is never stored.

```python
# packages/python/provenance_domain/ingest_alias.py
from __future__ import annotations
import base64
import hashlib
import hmac
import secrets

ALIAS_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"   # base32-ish, no 0/1/l/o
ALIAS_LENGTH = 10                                     # 32^10 ~= 1.1e15 addresses


def mint_alias_local_part() -> str:
    """A fresh forwarding local part. Returned to the user exactly once."""
    return "".join(secrets.choice(ALIAS_ALPHABET) for _ in range(ALIAS_LENGTH))


def alias_hash(secret: bytes, local_part: str) -> bytes:
    """HMAC-SHA256 over the lowercased local part. 32 bytes, matches ck_ingest_aliases_hash_len."""
    return hmac.new(secret, local_part.strip().lower().encode("ascii"), hashlib.sha256).digest()


def alias_hash_b64(secret: bytes, local_part: str) -> str:
    """The wire form used by POST /internal/v1/ingest/artifacts (field alias_hash)."""
    return "b64:" + base64.b64encode(alias_hash(secret, local_part)).decode()


def local_part_from_recipient(recipient: str) -> str:
    """'n7k4q9wv2x@in.provenance.app' -> 'n7k4q9wv2x'. Rejects sub-addressing."""
    local, _, domain = recipient.partition("@")
    if "+" in local:            # a+anything@ must not resolve to a's alias
        raise ValueError("SUBADDRESSING_NOT_ACCEPTED")
    return local.strip().lower()
```

Properties this gets right, each of which is a real failure if inverted:

- **HMAC, not a plain hash.** A 10-character alias out of a 32-symbol alphabet is brute-forceable offline against SHA-256 at trivial cost. The keyed construction means a database dump yields no working inbound addresses without `provenance/crypto:alias_hmac_key`, which lives only in Secrets Manager.
- **The Lambda computes the hash, the API never receives a user id.** `POST /internal/v1/ingest/artifacts` has an `alias_hash` field and no `user_id` field, and `extra="forbid"` makes adding one a `422`. The alias hash *is* the capability (`specs/15_API_SPEC.md` §3.3, kind `INGEST_ALIAS`).
- **Sub-addressing is refused.** `n7k4q9wv2x+anything@in.provenance.app` would otherwise resolve to the same alias, giving an attacker who learns one address an unbounded set of addresses that all look distinct in logs.
- **Rotation is a database write plus one email to the user**, never an infrastructure change, because the receipt rule matches the domain.

Rotation and disable, for reference (owned by `specs/15_API_SPEC.md` §8.22):

```sql
-- Rotation: the old alias stops resolving the moment status flips. No SES change.
UPDATE ingest_aliases SET status = 'DISABLED', rotated_at = now()
WHERE tenant_id = $1 AND user_id = $2 AND status = 'ACTIVE';
INSERT INTO ingest_aliases (id, tenant_id, user_id, alias_hash, alias_label, status)
VALUES ($3, $1, $2, $4, $5, 'ACTIVE');
```

### 5.3 Spam and auth verdict capture

The SES event delivered to `provenance-ses-ingest` carries `receipt.spamVerdict`, `virusVerdict`, `spfVerdict`, `dkimVerdict`, `dmarcVerdict`. Each has a `status` of `PASS`, `FAIL`, `GRAY`, or `PROCESSING_FAILED`. `specs/15_API_SPEC.md` §9.1 distinguishes only `PASS` from `FAIL`, so the worker normalizes, and the normalization is a security decision rather than a formatting one.

```python
# workers/ses_ingest/verdicts.py
from __future__ import annotations

# Fail closed on the two verdicts that gate admission. A scanner that could not decide
# is not evidence that the message is clean, and an admitted-then-quarantined artifact
# is more expensive than a rejected one the user can re-forward.
_BLOCKING = {"spam", "virus"}


def normalize_verdicts(receipt: dict) -> dict[str, str]:
    raw = {
        "spf":   receipt.get("spfVerdict",   {}).get("status", "PROCESSING_FAILED"),
        "dkim":  receipt.get("dkimVerdict",  {}).get("status", "PROCESSING_FAILED"),
        "dmarc": receipt.get("dmarcVerdict", {}).get("status", "PROCESSING_FAILED"),
        "spam":  receipt.get("spamVerdict",  {}).get("status", "PROCESSING_FAILED"),
        "virus": receipt.get("virusVerdict", {}).get("status", "PROCESSING_FAILED"),
    }
    out: dict[str, str] = {}
    for name, status in raw.items():
        if status == "PASS":
            out[name] = "PASS"
        elif name in _BLOCKING:
            out[name] = "FAIL"          # FAIL, GRAY, PROCESSING_FAILED all block
        else:
            out[name] = "FAIL"          # spf/dkim/dmarc: reported, never blocking
    return out


def raw_verdicts(receipt: dict) -> dict[str, str]:
    """The unnormalized statuses, preserved verbatim into parser_metadata.ses_verdicts."""
    return {k: receipt.get(f"{k}Verdict", {}).get("status", "PROCESSING_FAILED")
            for k in ("spf", "dkim", "dmarc", "spam", "virus")}
```

The asymmetry is the point and is stated in §9.1 step 3: **`spam` or `virus` failing rejects the artifact** (`422 VALIDATION_FAILED`, `details.reason = "SES_VERDICT_FAIL"`, and an `artifact.rejected.v1` event so the UI can explain a silent non-ingest). **`spf`, `dkim`, or `dmarc` failing does not reject anything.** A spoofed sender is itself meaningful evidence about a counterparty, so the verdict is preserved in `parser_metadata.ses_verdicts` and lowers the artifact's source authority band. Deleting a spoofed message would destroy the most interesting artifact in an adversarial scenario.

Both the normalized and the raw maps are stored. The normalized one drives the decision; the raw one is what a judge or an auditor reads, and `GRAY` is a materially different fact from `FAIL`.

### 5.4 Outbound identity verification

```typescript
new CfnEmailIdentity(this, 'SendingDomainIdentity', {
  emailIdentity: 'provenance.app',
  dkimAttributes: { signingEnabled: true },
  dkimSigningAttributes: { nextSigningKeyLength: 'RSA_2048_BIT' },
  mailFromAttributes: {
    mailFromDomain: 'mail.provenance.app',
    behaviorOnMxFailure: 'USE_DEFAULT_VALUE',
  },
  feedbackAttributes: { emailForwardingEnabled: false },
});

// The demo counterparty sink. Required by the sandbox restriction in §5.6.
new CfnEmailIdentity(this, 'DemoSinkIdentity', {
  emailIdentity: 'demo-sink.provenance.app',
  dkimAttributes: { signingEnabled: true },
});

const configSet = new CfnConfigurationSet(this, 'OutboundConfigSet', {
  name: 'provenance-outbound',
  reputationOptions: { reputationMetricsEnabled: true },
  sendingOptions: { sendingEnabled: true },
  suppressionOptions: { suppressedReasons: ['BOUNCE', 'COMPLAINT'] },
  trackingOptions: { customRedirectDomain: undefined },   // no open/click tracking on a dispute letter
});

new CfnConfigurationSetEventDestination(this, 'OutboundEvents', {
  configurationSetName: configSet.name!,
  eventDestinationName: 'provenance-outbound-to-eventbridge',
  eventDestination: {
    enabled: true,
    matchingEventTypes: ['SEND', 'DELIVERY', 'BOUNCE', 'COMPLAINT', 'REJECT',
                         'RENDERING_FAILURE', 'DELIVERY_DELAY'],
    eventBridgeDestination: { eventBusArn: props.bus.eventBusArn },
  },
});
```

Every `SendEmail` call passes `ConfigurationSetName: 'provenance-outbound'`. Without it the events are not emitted and `action.failed.v1` with `error_code: "RECIPIENT_BOUNCED"` can never be produced, which would leave the action plane unable to tell a delivered dispute from a bounced one.

Open and click tracking is off. Rewriting links inside a dispute letter would alter the exact bytes whose SHA-256 the human approved, which would break the `approval_draft_sha256` binding in `specs/10_DATABASE_DDL.md` §13. That is a correctness constraint, not a privacy preference.

### 5.5 Demo recipient allow-list

`specs/15_API_SPEC.md` §14.4: the hackathon allowlist is the counterparty's `canonical_domain` plus `demo-sink.provenance.app`. Enforced in `action_policy` before an intent may reach `APPROVED`, and again in the executor.

```python
# services/control_plane/app/actions/recipients.py
from __future__ import annotations
import os

DEMO_SINK_DOMAIN = "demo-sink.provenance.app"

# ACTION_RECIPIENT_MODE:
#   COUNTERPARTY  send to the counterparty canonical_domain (requires SES production access)
#   DEMO_SINK     send to the sink, and SAY SO in the UI. Never a silent rewrite.
RECIPIENT_MODE = os.environ.get("ACTION_RECIPIENT_MODE", "DEMO_SINK")


def allowed_recipient_domains(counterparty_canonical_domain: str | None) -> frozenset[str]:
    allowed = {DEMO_SINK_DOMAIN}
    if RECIPIENT_MODE == "COUNTERPARTY" and counterparty_canonical_domain:
        allowed.add(counterparty_canonical_domain.lower())
    return frozenset(allowed)


def assert_recipient_allowed(recipient: str, counterparty_canonical_domain: str | None) -> None:
    domain = recipient.rsplit("@", 1)[-1].lower()
    if domain not in allowed_recipient_domains(counterparty_canonical_domain):
        raise ApiError("RECIPIENT_NOT_ALLOWED", 422, details={"recipient_domain": domain})
```

Two rules that keep this honest:

1. **The recipient is never silently rewritten.** In `DEMO_SINK` mode the Advocate drafts to the sink address and the UI renders the sink address, so the human approves what will actually be sent. Substituting a recipient after approval would invalidate the approval binding and would be a lie on screen.
2. **`PV_ACTION_EXECUTION_MODE=DISABLED` is the kill switch** required by `quality/23_PHASE_GATES.md` §15 (G-9 rollback). Approvals continue to be recorded; `SendEmail` is never called; the intent settles as `action.failed.v1` with `error_code: "PROVIDER_REJECTED"` and a reason code stating the mode. It is tested at G-9, not discovered on demo day.

### 5.6 The SES sandbox restriction and what it costs the demo

**A new AWS account's SES is in the sandbox.** Stated plainly, because it changes what the demo can show:

- Outbound mail may be sent **only to verified identities**. An unverified recipient returns `MessageRejected: Email address is not verified`.
- The sending quota is **200 messages per 24 hours** and **1 message per second**.
- Inbound receiving is **not** restricted by the sandbox. Receipt rules, S3 writes, and Lambda invocations work in the sandbox exactly as they do in production.

Consequences, in order:

1. **The hero send must target a verified identity.** `demo-sink.provenance.app` is verified in §5.4 precisely so `ACTION_RECIPIENT_MODE=DEMO_SINK` works inside the sandbox with no production-access request on the critical path.
2. **The demo does not email a real institution, and would not even with production access.** Sending a generated dispute letter to a real ISP during a recorded demo is out of scope for the same reason the non-goals list rejects autonomous financial decisions. The sandbox is therefore aligned with the product's posture rather than fighting it.
3. **Production access should still be requested early** (`aws sesv2 put-account-details` with a use-case description, then the manual review), because approval is not instant and having it removes a single point of demo fragility. It is not on the critical path: every gate in `quality/23_PHASE_GATES.md` §15 and §19 passes inside the sandbox.
4. **The 200/day quota is far above demo need** (the hero flow sends one message) but is below what a careless retry loop could consume. The `provenance-action-dlq` is deliberately not auto-redriven (§6.4), which is the control that keeps a retry storm from burning the quota.
5. **Verify the from-address is usable before the demo**, since a verified *domain* covers every address at it:

```bash
aws sesv2 get-account --region us-east-1 --query '{sandbox:ProductionAccessEnabled,quota:SendQuota}'
aws sesv2 get-email-identity --email-identity provenance.app --region us-east-1 \
  --query '{verified:VerifiedForSendingStatus,dkim:DkimAttributes.Status}'
aws sesv2 get-email-identity --email-identity demo-sink.provenance.app --region us-east-1 \
  --query 'VerifiedForSendingStatus'
# All three must be true/SUCCESS before G-9 is signed.
```

---

## 6. EventBridge, Scheduler, SQS

Owner of the contract: `specs/15_API_SPEC.md` §11 (routing) and §13 (dispatcher). Rule names, queue names, and patterns below are copied from there and must not diverge.

### 6.1 Custom bus

```typescript
// infra/cdk/lib/messaging-stack.ts
import { EventBus, Rule, RuleTargetInput, Match } from 'aws-cdk-lib/aws-events';
import { SqsQueue, LambdaFunction, CloudWatchLogGroup } from 'aws-cdk-lib/aws-events-targets';
import { Queue, QueueEncryption, DeduplicationScope } from 'aws-cdk-lib/aws-sqs';
import { CfnScheduleGroup } from 'aws-cdk-lib/aws-scheduler';
import { LogGroup, RetentionDays } from 'aws-cdk-lib/aws-logs';

const bus = new EventBus(this, 'DomainBus', { eventBusName: 'provenance-domain-bus' });
```

One bus. Domain events only. The default bus is not used, so an AWS service event can never match a rule written for a Provenance event type, and `Source: provenance.control-plane` is a second filter on every rule rather than the only one.

### 6.2 Queues and DLQs

```typescript
const workerDlq = new Queue(this, 'WorkerDlq', {
  queueName: 'provenance-worker-dlq',
  encryption: QueueEncryption.KMS_MANAGED,
  retentionPeriod: Duration.days(14),
  enforceSSL: true,
});

const schedulerDlq = new Queue(this, 'SchedulerDlq', {
  queueName: 'provenance-scheduler-dlq',
  encryption: QueueEncryption.KMS_MANAGED,
  retentionPeriod: Duration.days(14),
  enforceSSL: true,
});

const advocateDlq = new Queue(this, 'AdvocateDlq', {
  queueName: 'provenance-advocate-dlq',
  encryption: QueueEncryption.KMS_MANAGED,
  retentionPeriod: Duration.days(14),
  enforceSSL: true,
});

const advocateQueue = new Queue(this, 'AdvocateQueue', {
  queueName: 'provenance-advocate-queue',
  encryption: QueueEncryption.KMS_MANAGED,
  enforceSSL: true,
  // 6x the consumer timeout. advocate_dispatch runs a LangGraph invocation.
  visibilityTimeout: Duration.seconds(180),
  retentionPeriod: Duration.days(4),
  deadLetterQueue: { queue: advocateDlq, maxReceiveCount: 3 },
});

const actionDlq = new Queue(this, 'ActionDlq', {
  queueName: 'provenance-action-dlq',
  encryption: QueueEncryption.KMS_MANAGED,
  retentionPeriod: Duration.days(14),
  enforceSSL: true,
});

const actionQueue = new Queue(this, 'ActionQueue', {
  queueName: 'provenance-action-queue',
  encryption: QueueEncryption.KMS_MANAGED,
  enforceSSL: true,
  visibilityTimeout: Duration.seconds(180),
  retentionPeriod: Duration.days(4),
  deadLetterQueue: { queue: actionDlq, maxReceiveCount: 2 },
});

const notificationDlq = new Queue(this, 'NotificationDlq', {
  queueName: 'provenance-notification-dlq',
  encryption: QueueEncryption.KMS_MANAGED,
  retentionPeriod: Duration.days(14),
  enforceSSL: true,
});
```

Redrive policy, restated as deployed:

| Queue | `maxReceiveCount` | DLQ | Redrive posture |
|---|---|---|---|
| `provenance-advocate-queue` | 3 | `provenance-advocate-dlq` | manual, after fixing the graph |
| `provenance-action-queue` | **2** | `provenance-action-dlq` | **manual only** |
| Scheduler targets | 3 (Scheduler retry policy) | `provenance-scheduler-dlq` | automatic replay is safe: evaluation is idempotent and re-reads state |
| `notification_dispatch` (Lambda async) | 2 (Lambda async retries) | `provenance-notification-dlq` | automatic redrive |
| `ses_ingest`, `textract_complete`, `outbox_dispatch` (Lambda async) | see §7 | `provenance-worker-dlq` | manual inspection; `ses_ingest` replay is safe because the idempotency key is the SES `messageId` |

The `redriveAllowPolicy` is set on every DLQ so only its own source queue may target it, which stops a future mis-wired queue from dumping unrelated messages into a DLQ an alarm is watching:

```typescript
for (const [dlq, source] of [[advocateDlq, advocateQueue], [actionDlq, actionQueue]] as const) {
  (dlq.node.defaultChild as CfnQueue).redriveAllowPolicy = {
    redrivePermission: 'byQueue',
    sourceQueueArns: [source.queueArn],
  };
}
```

**`provenance-action-dlq` is never auto-redriven, and that is a product decision rather than an operational preference.** Every other failure in this system is safe to retry. A queued outbound message is the one place where automatic replay could send a letter the user no longer wants, so a human decides. The alarm on its depth (§13.1) is therefore a page, not a chart.

### 6.3 Rules

Five rules. The patterns are verbatim from `specs/15_API_SPEC.md` §11.2; a drift here silently stops a consumer.

```typescript
// 1. Advocate. The attention_level filter keeps trivial state changes from waking an LLM.
new Rule(this, 'AdvocateRule', {
  ruleName: 'provenance-advocate-rule',
  eventBus: bus,
  eventPattern: {
    source: ['provenance.control-plane'],
    detailType: ['case.reopened.v1', 'conflict.detected.v1',
                 'commitment.overdue.v1', 'trigger.fired.v1'],
    detail: {
      schema_version: ['1.0'],
      payload: { attention_level: [{ 'anything-but': ['NONE'] }] },
    },
  },
  targets: [new SqsQueue(advocateQueue, { deadLetterQueue: workerDlq })],
});

// 2. Action executor. ONLY action.approved.v1 reaches it. There is no rule that routes
//    action.proposed.v1 here, and adding one would break invariant 4.
new Rule(this, 'ActionExecuteRule', {
  ruleName: 'provenance-action-execute-rule',
  eventBus: bus,
  eventPattern: {
    source: ['provenance.control-plane'],
    detailType: ['action.approved.v1'],
  },
  targets: [new SqsQueue(actionQueue, { deadLetterQueue: workerDlq })],
});

// 3. Notifications.
new Rule(this, 'NotificationRule', {
  ruleName: 'provenance-notification-rule',
  eventBus: bus,
  eventPattern: {
    source: ['provenance.control-plane'],
    detailType: ['case.reopened.v1', 'case.state_changed.v1', 'conflict.detected.v1',
                 'commitment.overdue.v1', 'commitment.fulfilled.v1', 'trigger.fired.v1',
                 'action.proposed.v1', 'action.executed.v1', 'action.failed.v1'],
  },
  targets: [new LambdaFunction(props.notificationDispatchFn, {
    deadLetterQueue: notificationDlq, retryAttempts: 2,
  })],
});

// 4. Telemetry: everything, to a log group. Never a second source of truth.
new Rule(this, 'TelemetryRule', {
  ruleName: 'provenance-telemetry-rule',
  eventBus: bus,
  eventPattern: { source: ['provenance.control-plane'] },
  targets: [new CloudWatchLogGroup(new LogGroup(this, 'DomainEventLog', {
    logGroupName: '/provenance/domain-events',
    retention: RetentionDays.ONE_MONTH,
  }))],
});

// 5. Trigger schedule lifecycle: arm creates a schedule, fire/noop deletes it.
new Rule(this, 'TriggerScheduleRule', {
  ruleName: 'provenance-trigger-schedule-rule',
  eventBus: bus,
  eventPattern: {
    source: ['provenance.control-plane'],
    detailType: ['trigger.armed.v1', 'trigger.noop.v1', 'trigger.fired.v1'],
  },
  targets: [new LambdaFunction(props.triggerScheduleManagerFn, {
    deadLetterQueue: workerDlq, retryAttempts: 2,
  })],
});
```

`specs/15_API_SPEC.md` §17.8 flags the `attention_level` filter as fragile: every event type listed on the advocate rule must actually carry that field in its payload, and EventBridge silently does not match when a required field is absent. Two mechanical controls, both required rather than advisory:

```bash
# spec_lint check: every detail-type on the advocate rule declares attention_level.
python -m tools.spec_lint --check advocate-rule-fields docs/specs/15_API_SPEC.md

# Contract test: publish one real fixture per routed type and assert arrival.
pytest tests/events/test_eventbridge_patterns.py -q
#   → 4 passed: one PutEvents per routed detail-type, each asserted to land in
#     provenance-advocate-queue within 10s. A pattern that matches nothing fails here.
```

### 6.4 Scheduler groups and one-time schedule naming

Two groups, so that the thousands of one-time trigger schedules never mix with the handful of system schedules and `delete-schedule-group` during teardown cannot take out a system sweep by accident.

```typescript
new CfnScheduleGroup(this, 'TriggerScheduleGroup', { name: 'provenance-triggers' });
new CfnScheduleGroup(this, 'SystemScheduleGroup', { name: 'provenance-system' });

// The role EventBridge Scheduler assumes to invoke a target. Scoped to two functions.
const schedulerRole = new Role(this, 'SchedulerInvokeRole', {
  roleName: 'provenance-scheduler-invoke-role',
  assumedBy: new ServicePrincipal('scheduler.amazonaws.com', {
    conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
  }),
});
schedulerRole.addToPolicy(new PolicyStatement({
  actions: ['lambda:InvokeFunction'],
  resources: [
    `arn:aws:lambda:us-east-1:${this.account}:function:provenance-trigger-wakeup`,
    `arn:aws:lambda:us-east-1:${this.account}:function:provenance-trigger-wakeup:*`,
    `arn:aws:lambda:us-east-1:${this.account}:function:provenance-outbox-dispatch`,
    `arn:aws:lambda:us-east-1:${this.account}:function:provenance-outbox-dispatch:*`,
  ],
}));
schedulerRole.addToPolicy(new PolicyStatement({
  actions: ['sqs:SendMessage'],
  resources: [schedulerDlq.queueArn],
}));
```

**One-time trigger schedules are created at runtime, not by CDK.** There is one schedule per armed trigger; putting them in a template would mean a stack update on every deadline the Kernel arms.

```python
# workers/trigger_schedule_manager/handler.py
from __future__ import annotations
import json
import os
import boto3
from botocore.exceptions import ClientError

_sched = boto3.client("scheduler", region_name="us-east-1")

GROUP = os.environ["EVENTBRIDGE_SCHEDULER_GROUP"]            # provenance-triggers
TARGET_ARN = os.environ["SCHEDULER_TARGET_LAMBDA_ARN"]       # provenance-trigger-wakeup
ROLE_ARN = os.environ["SCHEDULER_ROLE_ARN"]
DLQ_ARN = os.environ["SCHEDULER_DLQ_ARN"]


def schedule_name(trigger_id: str) -> str:
    """specs/15_API_SPEC.md §11.3: provenance-trigger-{trigger_id_short}.

    trigger_id_short is the first label of the UUID (8 hex chars). Scheduler names are
    limited to 64 chars and [0-9a-zA-Z-_.]; the short form keeps the name readable in
    the console and in trigger.armed.v1 payloads, which carry it verbatim.
    """
    return f"provenance-trigger-{trigger_id.split('-')[0]}"


def handler(event, _context):
    detail = event["detail"]
    payload = detail["payload"]
    trigger_id = payload["trigger_id"]
    name = schedule_name(trigger_id)

    if detail["event_type"] != "trigger.armed.v1":
        # trigger.fired.v1 / trigger.noop.v1: the trigger is spent, remove the schedule.
        try:
            _sched.delete_schedule(Name=name, GroupName=GROUP)
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":
                raise
        return {"deleted": name}

    not_before = payload["not_before"]          # RFC 3339 with Z, e.g. 2026-06-15T00:00:00Z
    wake = {
        "schema_version": "1.0",
        "wake_source": "SCHEDULER",
        "wake_id": f"pv-trg-{trigger_id.replace('-', '')}-v{payload['evaluation_version']}",
        "trigger_id": trigger_id,
        "evaluation_version": payload["evaluation_version"],
        "case_id": payload["case_id"],
        "tenant_id": detail["tenant_id"],
        "user_id": detail["user_id"],
        "scheduled_for": not_before,
        "trace_hint": "trigger-arm",
        "capability_proof": payload["capability_proof"],
    }

    _sched.create_schedule(
        Name=name,
        GroupName=GROUP,
        ScheduleExpression=f"at({not_before.replace('Z', '')})",   # at(2026-06-15T00:00:00)
        ScheduleExpressionTimezone="UTC",
        FlexibleTimeWindow={"Mode": "FLEXIBLE", "MaximumWindowInMinutes": 15},
        ActionAfterCompletion="DELETE",         # one-time schedules clean themselves up
        State="ENABLED",
        Target={
            "Arn": TARGET_ARN,
            "RoleArn": ROLE_ARN,
            "Input": json.dumps(wake, separators=(",", ":")),
            "RetryPolicy": {"MaximumRetryAttempts": 3, "MaximumEventAgeInSeconds": 3600},
            "DeadLetterConfig": {"Arn": DLQ_ARN},
        },
        ClientToken=wake["wake_id"][:64],       # idempotent create on Lambda retry
    )
    return {"created": name, "at": not_before}
```

Three details that are easy to get wrong:

- **`at()` takes a naive timestamp plus a separate timezone.** `at(2026-06-15T00:00:00Z)` is rejected; the `Z` must be stripped and `ScheduleExpressionTimezone: "UTC"` supplied.
- **`ActionAfterCompletion: "DELETE"`** means a fired schedule removes itself, so the delete path in this handler is only needed for a trigger that is disarmed *before* its wake time. Without it, `provenance-triggers` accumulates spent schedules and eventually hits the account schedule quota.
- **The 15-minute flexible window is safe** because the predicate is re-evaluated at wakeup against current canonical state (`specs/15_API_SPEC.md` §11.3). A trigger that wakes 12 minutes late reads current state; a trigger that wakes after the deposit arrived returns `NO_OP` with a closed reason code. The scheduled message is never proof the condition still holds.

The schedule input carries no authority. `case_id`, `tenant_id`, and `user_id` are present for log correlation only, and `specs/16_TRIGGER_DSL.md` §9.5 requires that a mismatch against the `prospective_triggers` row be recorded as `WAKE_PAYLOAD_MISMATCH` with the database row winning.

**System schedule: the outbox sweep.** `specs/15_API_SPEC.md` §13.6 requires a sweep every 30 seconds. EventBridge Scheduler's minimum `rate()` granularity is one minute, so a 30-second schedule cannot be expressed. Rather than quietly weaken the guarantee, the schedule fires once a minute and the handler performs two sweeps 30 seconds apart inside one invocation:

```typescript
new CfnSchedule(this, 'OutboxSweep', {
  name: 'provenance-outbox-sweep',
  groupName: 'provenance-system',
  scheduleExpression: 'rate(1 minute)',
  flexibleTimeWindow: { mode: 'OFF' },
  state: 'ENABLED',
  target: {
    arn: props.outboxDispatchFn.functionArn,
    roleArn: schedulerRole.roleArn,
    input: JSON.stringify({ source: 'SCHEDULER', batch_size: 50, passes: 2, pass_gap_seconds: 30 }),
    retryPolicy: { maximumRetryAttempts: 0, maximumEventAgeInSeconds: 60 },
    deadLetterConfig: { arn: schedulerDlq.queueArn },
  },
});
```

`maximumRetryAttempts: 0` on the sweep is correct: the next schedule fires in one minute and the claim/lease state machine already handles a dispatcher that died mid-publish. Retrying a sweep would only produce a second concurrent claimer, which is safe but pointless.

---

## 7. Lambda workers

Nine functions. The four named in `implementation/00_IMPLEMENTATION_MAP.md` §5 get a full handler contract and a written-out policy; the five that `specs/15_API_SPEC.md` §11.2 and §2.5 require are specified in §7.7 with the same rigour applied to their permissions.

### 7.1 Common configuration

```typescript
// infra/cdk/lib/compute-stack.ts
const commonWorker = {
  runtime: lambda.Runtime.PYTHON_3_12,
  architecture: lambda.Architecture.ARM_64,     // ~20% cheaper per ms; all deps are pure-python or arm wheels
  tracing: lambda.Tracing.ACTIVE,               // X-Ray, joined to the OTEL trace_id
  loggingFormat: lambda.LoggingFormat.JSON,
  applicationLogLevelV2: lambda.ApplicationLogLevel.INFO,
  systemLogLevelV2: lambda.SystemLogLevel.WARN,
  environment: {
    APP_ENV: 'prod',
    AWS_REGION_NAME: 'us-east-1',
    APP_BASE_URL: 'https://api.provenance.app',
    COGNITO_TOKEN_ENDPOINT: 'https://provenance-auth.auth.us-east-1.amazoncognito.com/oauth2/token',
    COGNITO_WORKER_CLIENT_ID: props.workerClientId,
    COGNITO_WORKER_CLIENT_SECRET_ARN: props.cognitoSecret.secretArn,
    POWERTOOLS_SERVICE_NAME: 'provenance',
    POWERTOOLS_LOG_LEVEL: 'INFO',
  },
  bundling: { assetExcludes: ['tests', '__pycache__', '.pytest_cache'] },
};
```

Every worker is thin by design. `implementation/01_SYSTEM_ARCHITECTURE_DETAILED.md` §16 forbids async handlers that mutate several invariant-linked rows without a database transaction, so **no worker holds a SQL credential**. Every worker's effect on canonical state goes through `/internal/v1` with a Cognito M2M token and a capability id, and the control plane opens the transaction. The one exception is `provenance-cognito-post-confirmation`, which writes three rows in one transaction as `pv_app_reader_writer` because there is no authenticated principal yet for it to call the API with.

M2M token caching, per `specs/15_API_SPEC.md` §17.7: in-memory per warm execution environment, keyed by `(client_id, scope)`, refreshed at `expires_at - 60s`, never written to `/tmp` and never persisted.

```python
# packages/python/provenance_telemetry/m2m.py
from __future__ import annotations
import base64
import json
import os
import threading
import time
import urllib.parse
import urllib.request

import boto3

_LOCK = threading.Lock()
_CACHE: dict[tuple[str, str], tuple[str, float]] = {}
_SECRETS = boto3.client("secretsmanager", region_name="us-east-1")
_SECRET_CACHE: dict[str, str] = {}


def _client_secret(arn: str, key: str) -> str:
    if arn not in _SECRET_CACHE:
        blob = _SECRETS.get_secret_value(SecretId=arn)["SecretString"]
        _SECRET_CACHE[arn] = json.loads(blob)[key]
    return _SECRET_CACHE[arn]


def m2m_token(*, client: str, scope: str) -> str:
    """Client-credentials access token. Cached in memory for the warm environment only."""
    key = (client, scope)
    now = time.time()
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and cached[1] > now:
            return cached[0]

    client_id = os.environ["COGNITO_WORKER_CLIENT_ID"]
    secret = _client_secret(os.environ["COGNITO_WORKER_CLIENT_SECRET_ARN"], "worker_client_secret")
    basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": scope}).encode()

    req = urllib.request.Request(
        os.environ["COGNITO_TOKEN_ENDPOINT"], data=body,
        headers={"Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=5) as r:      # noqa: S310 (fixed https endpoint)
        payload = json.load(r)

    token = payload["access_token"]
    with _LOCK:
        _CACHE[key] = (token, now + int(payload["expires_in"]) - 60)
    return token
```

`urllib.parse.urlencode` percent-encodes the `/` inside `provenance.trigger/evaluate` and the space between multiple scopes, which is the failure `specs/15_API_SPEC.md` §2.2 warns costs an afternoon.

### 7.2 `provenance-ses-ingest`

**Trigger:** SES receipt rule `lambdaAction`, `InvocationType: Event`.
**Memory:** 1024 MB. **Timeout:** 60 s. **Reserved concurrency:** 5. **Ephemeral storage:** 512 MB.
**Async retry:** `maxEventAge` 2 hours, `retryAttempts` 2, on-failure destination `provenance-worker-dlq`.

Memory is 1024 MB because the handler streams up to 20 MiB of MIME through a SHA-256 and a `CopyObject`; arm64 at 1024 MB is the cheapest configuration that keeps the p99 under ten seconds. Reserved concurrency of 5 is a cost guard, not a throughput target: five concurrent inbound messages is far beyond demo rate, and an unbounded function fronting a mail server is an unbounded bill.

```python
# workers/ses_ingest/handler.py
from __future__ import annotations
import hashlib
import json
import os
from datetime import datetime, timezone

import boto3
from provenance_domain.ingest_alias import alias_hash_b64, local_part_from_recipient
from provenance_telemetry.m2m import m2m_token
from .verdicts import normalize_verdicts, raw_verdicts
from .http import post_json                      # bounded-retry JSON client

_s3 = boto3.client("s3", region_name="us-east-1")
_secrets = boto3.client("secretsmanager", region_name="us-east-1")

INBOUND_BUCKET = os.environ["S3_INBOUND_BUCKET"]
ARTIFACT_BUCKET = os.environ["S3_ARTIFACT_BUCKET"]
KMS_KEY_ARN = os.environ["S3_KMS_KEY_ARN"]
API = os.environ["APP_BASE_URL"]
INGEST_DOMAIN = os.environ["SES_INGEST_DOMAIN"]
MAX_BYTES = int(os.environ.get("MAX_ARTIFACT_BYTES", 20 * 1024 * 1024))

_alias_secret: bytes | None = None


def _alias_key() -> bytes:
    global _alias_secret
    if _alias_secret is None:
        blob = _secrets.get_secret_value(SecretId=os.environ["INGEST_ALIAS_HMAC_KEY_ARN"])
        _alias_secret = json.loads(blob["SecretString"])["alias_hmac_key"].encode()
    return _alias_secret


def handler(event, _context) -> dict:
    """SES -> S3 canonical copy -> POST /internal/v1/ingest/artifacts. No business logic."""
    record = event["Records"][0]["ses"]
    mail, receipt = record["mail"], record["receipt"]
    message_id = mail["messageId"]
    staging_key = f"ses/incoming/{message_id}"

    # 1. Stream the staged object once: size and SHA-256 in one pass.
    obj = _s3.get_object(Bucket=INBOUND_BUCKET, Key=staging_key)
    digest, size = hashlib.sha256(), 0
    for chunk in obj["Body"].iter_chunks(1024 * 1024):
        size += len(chunk)
        if size > MAX_BYTES:
            return _reject(message_id, mail, "SIZE_EXCEEDED", {"size_bytes": size})
        digest.update(chunk)
    sha_hex = digest.hexdigest()

    # 2. Copy to the immutable canonical key recorded in source_artifacts.
    received = datetime.fromisoformat(mail["timestamp"].replace("Z", "+00:00")).astimezone(timezone.utc)
    canonical_key = (f"ses/{received:%Y}/{received:%m}/{received:%d}/"
                     f"{message_id}-{sha_hex[:8]}")
    _s3.copy_object(
        Bucket=ARTIFACT_BUCKET, Key=canonical_key,
        CopySource={"Bucket": INBOUND_BUCKET, "Key": staging_key},
        ServerSideEncryption="aws:kms", SSEKMSKeyId=KMS_KEY_ARN,
        MetadataDirective="REPLACE",
        Metadata={"ses-message-id": message_id, "content-sha256": sha_hex},
    )

    # 3. Resolve the alias to a hash. The Lambda never learns which user this is.
    recipient = _provenance_recipient(receipt["recipients"])
    if recipient is None:
        return _reject(message_id, mail, "ALIAS_DISABLED", {"recipients": receipt["recipients"]})
    local = local_part_from_recipient(recipient)

    body = {
        "alias_hash": alias_hash_b64(_alias_key(), local),
        "s3_bucket": ARTIFACT_BUCKET,
        "s3_key": canonical_key,
        "source_message_id": mail.get("commonHeaders", {}).get("messageId"),
        "sender": mail["source"],
        "recipient": recipient,
        "subject": mail.get("commonHeaders", {}).get("subject"),
        "received_at": received.isoformat().replace("+00:00", "Z"),
        "size_bytes": size,
        "content_sha256": sha_hex,
        "ses_verdicts": normalize_verdicts(receipt),
        "ses_verdicts_raw": raw_verdicts(receipt),
    }

    resp = post_json(
        f"{API}/internal/v1/ingest/artifacts", body,
        token=m2m_token(client="provenance-workers", scope="provenance.ingest/write"),
        idempotency_key=f"ses-{message_id}",        # §6.2: deterministic, so a retry replays
        capability_proof=None,                      # issued by the control plane; see §7.2 note
        timeout=25,
    )
    if resp.status_code >= 500:
        raise RuntimeError(f"retryable {resp.status_code}")   # Lambda async retries, then DLQ
    return {"status": resp.status_code, "artifact_id": resp.json().get("artifact_id")}


def _provenance_recipient(recipients: list[str]) -> str | None:
    for r in recipients:
        if r.lower().endswith(f"@{INGEST_DOMAIN}"):
            return r.lower()
    return None
```

**Handler contract.** Input: an SES `Records[0].ses` event. Output: `{"status": int, "artifact_id": str | None}`. Raises only on a retryable 5xx. A 4xx is terminal and is **not** raised: `404 INGEST_ALIAS_NOT_FOUND` and `409 INGEST_ALIAS_DISABLED` will never succeed on retry, and raising would burn the DLQ on a permanently invalid message.

**The capability proof.** `specs/15_API_SPEC.md` §3.5 requires `X-Provenance-Capability-Proof` on `/internal/v1`. For the `INGEST_ALIAS` kind the proof cannot be pre-issued by the control plane, because no control-plane request preceded the arriving mail. The proof is therefore computed by the worker over `("INGEST_ALIAS", alias_hash, expires_at)` using the same `provenance/crypto:capability_hmac_key`, which the worker's policy below grants it. This is a real widening of that secret's blast radius and is recorded in §15.6.

Least-privilege policy, written out:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "ReadStagedInboundObject",
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": "arn:aws:s3:::provenance-inbound-us-east-1/ses/incoming/*" },

    { "Sid": "WriteCanonicalInboundCopy",
      "Effect": "Allow",
      "Action": ["s3:PutObject"],
      "Resource": "arn:aws:s3:::provenance-artifacts-us-east-1/ses/*",
      "Condition": {
        "StringEquals": { "s3:x-amz-server-side-encryption": "aws:kms" } } },

    { "Sid": "UseArtifactKey",
      "Effect": "Allow",
      "Action": ["kms:Decrypt", "kms:GenerateDataKey"],
      "Resource": "arn:aws:kms:us-east-1:<account>:key/<artifact-key-id>",
      "Condition": {
        "StringEquals": { "kms:ViaService": "s3.us-east-1.amazonaws.com" } } },

    { "Sid": "ReadWorkerSecrets",
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": [
        "arn:aws:secretsmanager:us-east-1:<account>:secret:provenance/cognito-*",
        "arn:aws:secretsmanager:us-east-1:<account>:secret:provenance/crypto-*" ] },

    { "Sid": "DlqOnFailure",
      "Effect": "Allow",
      "Action": ["sqs:SendMessage"],
      "Resource": "arn:aws:sqs:us-east-1:<account>:provenance-worker-dlq" },

    { "Sid": "Telemetry",
      "Effect": "Allow",
      "Action": ["logs:CreateLogStream", "logs:PutLogEvents",
                 "xray:PutTraceSegments", "xray:PutTelemetryRecords"],
      "Resource": "*" }
  ]
}
```

What it deliberately cannot do: read any object outside `ses/incoming/*`, write anywhere outside `ses/*` in the artifact bucket, delete anything, publish to EventBridge, send email, invoke AgentCore, or reach CockroachDB. A fully compromised `ses_ingest` can inject a forged inbound artifact for a *known* alias, which the Kernel then types as a `COUNTERPARTY_CLAIM` requiring grounding. It cannot make it a fact and cannot make it another user's.

### 7.3 `provenance-textract-complete`

**Trigger:** SNS topic `provenance-textract-status`, populated by the `NotificationChannel` of `StartDocumentAnalysis`.
**Memory:** 1024 MB. **Timeout:** 120 s. **Reserved concurrency:** 3.
**Async retry:** `retryAttempts` 2, on-failure destination `provenance-worker-dlq`.

```python
# workers/textract_complete/handler.py
from __future__ import annotations
import json
import os

import boto3
from provenance_telemetry.m2m import m2m_token
from .normalize import blocks_to_content_blocks   # deterministic; no model call
from .http import post_json

_textract = boto3.client("textract", region_name="us-east-1")
_s3 = boto3.client("s3", region_name="us-east-1")

PARSER_VERSION = "textract-analyze-1"


def handler(event, _context) -> dict:
    note = json.loads(event["Records"][0]["Sns"]["Message"])
    job_id, status = note["JobId"], note["Status"]
    tenant_id, user_id, artifact_id = note["JobTag"].split(":")     # set at StartDocumentAnalysis

    if status != "SUCCEEDED":
        return _report_parser_failure(tenant_id, user_id, artifact_id, status)

    pages, token = [], None
    while True:
        kwargs = {"JobId": job_id, "MaxResults": 1000}
        if token:
            kwargs["NextToken"] = token
        page = _textract.get_document_analysis(**kwargs)
        pages.extend(page["Blocks"])
        token = page.get("NextToken")
        if not token:
            break

    prefix = f"normalized/{tenant_id}/{user_id}/{artifact_id}"
    _s3.put_object(Bucket=os.environ["S3_ARTIFACT_BUCKET"], Key=f"{prefix}/textract-raw.json",
                   Body=json.dumps({"JobId": job_id, "Blocks": pages}).encode(),
                   ContentType="application/json", ServerSideEncryption="aws:kms",
                   SSEKMSKeyId=os.environ["S3_KMS_KEY_ARN"])

    normalized = blocks_to_content_blocks(pages)      # span-anchored, page + bbox preserved
    _s3.put_object(Bucket=os.environ["S3_ARTIFACT_BUCKET"], Key=f"{prefix}/parser-v2.json",
                   Body=json.dumps(normalized).encode(),
                   ContentType="application/json", ServerSideEncryption="aws:kms",
                   SSEKMSKeyId=os.environ["S3_KMS_KEY_ARN"])

    resp = post_json(
        f"{os.environ['APP_BASE_URL']}/internal/v1/artifacts/{artifact_id}/parser-callback",
        {"parser_version": PARSER_VERSION, "parser_status": "PARSED",
         "normalized_s3_key": f"{prefix}/parser-v2.json",
         "parser_metadata": {"pages": normalized["page_count"], "used_textract": True,
                             "attachment_count": 0, "textract_job_id": job_id}},
        token=m2m_token(client="provenance-workers", scope="provenance.ingest/write"),
        idempotency_key=f"txt-{job_id}",
        timeout=25,
    )
    if resp.status_code >= 500:
        raise RuntimeError(f"retryable {resp.status_code}")
    return {"artifact_id": artifact_id, "blocks": len(pages)}
```

**This handler depends on one endpoint that `specs/15_API_SPEC.md` §9 does not yet define.** `POST /internal/v1/artifacts/{artifact_id}/parser-callback` is the only way a Textract completion can flip `source_artifacts.parser_status` to `PARSED` without giving a Lambda a SQL credential, and without it `GET /internal/v1/agent-runs/{id}/artifact-content` returns `409` forever for any scanned document. It slots into the existing security model with no new concepts: capability kind `ARTIFACT` (already in §3.3, already in the `provenance-workers` matrix row), required scope `provenance.ingest/write`, idempotency scope string `internal.parser.callback`. It must be added to §9 of the API specification, under the change-control rule in `README.md`, before Phase 8 begins. Recorded in §15.3.

Policy additions beyond the common set (`logs`, `xray`, `secretsmanager` on the two secrets, `sqs:SendMessage` on the DLQ):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "ReadTextractResults",
      "Effect": "Allow",
      "Action": ["textract:GetDocumentAnalysis", "textract:GetDocumentTextDetection"],
      "Resource": "*" },

    { "Sid": "WriteNormalizedParserOutput",
      "Effect": "Allow",
      "Action": ["s3:PutObject"],
      "Resource": "arn:aws:s3:::provenance-artifacts-us-east-1/normalized/*",
      "Condition": { "StringEquals": { "s3:x-amz-server-side-encryption": "aws:kms" } } },

    { "Sid": "ReadRawArtifactForReparse",
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": "arn:aws:s3:::provenance-artifacts-us-east-1/raw/*" },

    { "Sid": "UseArtifactKey",
      "Effect": "Allow",
      "Action": ["kms:Decrypt", "kms:GenerateDataKey"],
      "Resource": "arn:aws:kms:us-east-1:<account>:key/<artifact-key-id>",
      "Condition": { "StringEquals": { "kms:ViaService": "s3.us-east-1.amazonaws.com" } } }
  ]
}
```

`textract:GetDocumentAnalysis` cannot be resource-scoped; Textract job ids are not ARNs. The compensating control is that the function can only be invoked by its SNS topic and only learns job ids from Textract itself, and `JobTag` carries the tenant/user/artifact triple that the control plane re-validates server-side.

The role that Textract assumes to publish completion is separate and minimal:

```typescript
const textractPublishRole = new Role(this, 'TextractPublishRole', {
  roleName: 'provenance-textract-publish-role',
  assumedBy: new ServicePrincipal('textract.amazonaws.com', {
    conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
  }),
  inlinePolicies: {
    publish: new PolicyDocument({ statements: [new PolicyStatement({
      actions: ['sns:Publish'], resources: [textractTopic.topicArn],
    })] }),
  },
});
```

### 7.4 `provenance-outbox-dispatch`

**Trigger:** EventBridge Scheduler `provenance-outbox-sweep`, `rate(1 minute)`, plus a manual `POST /internal/v1/events/outbox/sweep`.
**Memory:** 512 MB. **Timeout:** 120 s. **Reserved concurrency:** 2.
**Async retry:** `retryAttempts` 0. The next schedule is the retry.

```python
# workers/outbox_dispatch/handler.py
from __future__ import annotations
import os
import time

from provenance_telemetry.m2m import m2m_token
from .http import post_json

API = os.environ["APP_BASE_URL"]


def handler(event, _context) -> dict:
    """Ask the control plane to sweep. The dispatcher STATE MACHINE lives in the control
    plane (specs/15_API_SPEC.md §13); this function is a clock, not a dispatcher."""
    passes = int(event.get("passes", 1))
    gap = float(event.get("pass_gap_seconds", 0))
    batch = int(event.get("batch_size", 50))
    token = m2m_token(client="provenance-workers", scope="provenance.outbox/dispatch")

    results = []
    for i in range(passes):
        if i:
            time.sleep(gap)                      # the 30s half-tick from §6.4
        r = post_json(f"{API}/internal/v1/events/outbox/sweep",
                      {"batch_size": batch, "worker_id": f"lambda-{os.environ['AWS_LAMBDA_LOG_STREAM_NAME']}"},
                      token=token, timeout=45)
        if r.status_code >= 500:
            raise RuntimeError(f"retryable {r.status_code}")
        results.append(r.json())
    return {"passes": results}
```

The `PutEvents` call, the claim query, the backoff table, and the reaper all live in the control plane, which already holds `pv_app_reader_writer` and can therefore run the `FOR UPDATE SKIP LOCKED` claim and the settle statements inside one transaction. Putting them in the Lambda would require giving a worker a SQL credential and would duplicate the state machine in two places.

Policy: the common set only. `secretsmanager:GetSecretValue` on `provenance/cognito`, `logs`, `xray`. **No `events:PutEvents`**, because this function never publishes; the control plane does. That absence is the check that the state machine has not leaked out of its owner.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "ReadCognitoWorkerSecret",
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": "arn:aws:secretsmanager:us-east-1:<account>:secret:provenance/cognito-*" },
    { "Sid": "Telemetry",
      "Effect": "Allow",
      "Action": ["logs:CreateLogStream", "logs:PutLogEvents",
                 "xray:PutTraceSegments", "xray:PutTelemetryRecords"],
      "Resource": "*" }
  ]
}
```

### 7.5 `provenance-trigger-wakeup`

**Trigger:** one-time EventBridge Scheduler schedules in group `provenance-triggers`.
**Memory:** 256 MB. **Timeout:** 30 s. **Reserved concurrency:** 10.
**Retry:** owned by the Scheduler target (`MaximumRetryAttempts: 3`, `MaximumEventAgeInSeconds: 3600`, DLQ `provenance-scheduler-dlq`). Lambda async retries are **0** so retry accounting lives in exactly one place.

The handler is the one from `specs/16_TRIGGER_DSL.md` §9.5, deployed verbatim, plus the capability proof header:

```python
# workers/trigger_wakeup/handler.py
from __future__ import annotations
import json
import os

from provenance_telemetry.m2m import m2m_token
from .http import post_json

CONTROL_PLANE = os.environ["APP_BASE_URL"]


def handler(event, _context) -> dict:
    """Scheduler -> control plane. Contains no business logic by design."""
    envelope = event if "trigger_id" in event else json.loads(event["Input"])
    resp = post_json(
        f"{CONTROL_PLANE}/internal/v1/triggers/{envelope['trigger_id']}/evaluate",
        envelope,
        token=m2m_token(client="provenance-workers", scope="provenance.trigger/evaluate"),
        idempotency_key=envelope["wake_id"],
        capability_proof=envelope["capability_proof"],
        timeout=20,
    )
    if resp.status_code >= 500:
        raise RuntimeError(f"retryable {resp.status_code}")   # Scheduler retries, then DLQ
    return {"outcome": resp.json().get("outcome"), "wake_id": envelope["wake_id"]}
```

256 MB and 30 s because the function does one HTTPS POST. A 4xx is terminal and not raised: a deleted trigger (404) or a superseded generation (409) will never succeed on retry.

Policy: identical to `outbox_dispatch` plus `sqs:SendMessage` on `provenance-scheduler-dlq`. Notably absent: `scheduler:*`. The wakeup function does not create, delete, or inspect schedules; `trigger_schedule_manager` owns that, so a compromised wakeup path cannot arm a new schedule.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "ReadCognitoWorkerSecret",
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": "arn:aws:secretsmanager:us-east-1:<account>:secret:provenance/cognito-*" },
    { "Sid": "SchedulerDlq",
      "Effect": "Allow",
      "Action": ["sqs:SendMessage"],
      "Resource": "arn:aws:sqs:us-east-1:<account>:provenance-scheduler-dlq" },
    { "Sid": "Telemetry",
      "Effect": "Allow",
      "Action": ["logs:CreateLogStream", "logs:PutLogEvents",
                 "xray:PutTraceSegments", "xray:PutTelemetryRecords"],
      "Resource": "*" }
  ]
}
```

### 7.6 Retry configuration, all four, in one table

| Function | Invoked by | Memory | Timeout | Reserved conc. | Retries | On failure |
|---|---|---|---|---|---|---|
| `provenance-ses-ingest` | SES receipt rule (async) | 1024 MB | 60 s | 5 | 2 async, max event age 2 h | `provenance-worker-dlq` |
| `provenance-textract-complete` | SNS `provenance-textract-status` | 1024 MB | 120 s | 3 | 2 async | `provenance-worker-dlq` |
| `provenance-outbox-dispatch` | Scheduler `rate(1 minute)` | 512 MB | 120 s | 2 | 0 (next tick is the retry) | `provenance-scheduler-dlq` |
| `provenance-trigger-wakeup` | Scheduler one-time `at()` | 256 MB | 30 s | 10 | 3, by the Scheduler target | `provenance-scheduler-dlq` |

Every one of them treats a 5xx as retryable and a 4xx as terminal, and every one derives its `Idempotency-Key` deterministically (`specs/15_API_SPEC.md` §6.2) so a retry replays rather than duplicates.

### 7.7 The other five functions

| Function | Invoked by | Memory | Timeout | Retries | Distinguishing permission |
|---|---|---|---|---|---|
| `provenance-advocate-dispatch` | SQS `provenance-advocate-queue`, batch 1 | 512 MB | 30 s | 3 receives → `provenance-advocate-dlq` | `sqs:ReceiveMessage`/`DeleteMessage` on its queue; **no** `bedrock:InvokeModel`. It calls `POST /internal/v1/events/deliveries` and the control plane starts the agent run, so the queue consumer never holds an agent capability. |
| `provenance-action-execute` | SQS `provenance-action-queue`, batch 1 | 512 MB | 60 s | 2 receives → `provenance-action-dlq` | scope `provenance.action/execute`; **no** `ses:SendEmail`. The control plane sends, after re-validating case revision and draft hash inside a transaction. A worker that could call SES directly would be a path around the revalidation. |
| `provenance-notification-dispatch` | EventBridge rule, async | 256 MB | 20 s | 2 async → `provenance-notification-dlq` | `ses:SendEmail` restricted by `ses:FromAddress` to `notifications@provenance.app`, and only for user notifications, never for an ActionIntent. |
| `provenance-trigger-schedule-manager` | EventBridge rule, async | 256 MB | 20 s | 2 async → `provenance-worker-dlq` | `scheduler:CreateSchedule`/`DeleteSchedule`/`GetSchedule` on `arn:aws:scheduler:us-east-1:<account>:schedule/provenance-triggers/*` only, plus `iam:PassRole` on `provenance-scheduler-invoke-role` with `iam:PassedToService: scheduler.amazonaws.com`. |
| `provenance-cognito-post-confirmation` | Cognito pool trigger | 512 MB | 20 s | none (failure fails sign-up) | the **only** worker with a CockroachDB credential (`pv_app_reader_writer` via `provenance/db:app_url`), because no principal exists yet for it to call the API as. |

`action_execute` not holding `ses:SendEmail` is the single most important row in that table. Invariant 4 says no uncommitted proposal produces an external side effect; the executor's inability to reach SES means the *only* code path to `SendEmail` runs after the revalidation query in `specs/10_DATABASE_DDL.md` §13 returns a row.

```typescript
// The one PassRole grant in the whole account, scoped two ways.
scheduleManagerFn.addToRolePolicy(new PolicyStatement({
  sid: 'PassSchedulerInvokeRoleOnly',
  actions: ['iam:PassRole'],
  resources: [schedulerRole.roleArn],
  conditions: { StringEquals: { 'iam:PassedToService': 'scheduler.amazonaws.com' } },
}));
```

---

## 8. App Runner and ECR

### 8.1 ECR repositories

```typescript
// infra/cdk/lib/data-stack.ts
import { Repository, TagMutability, TagStatus } from 'aws-cdk-lib/aws-ecr';

for (const name of ['control-plane', 'agent-runtime']) {
  new Repository(this, `Repo${name}`, {
    repositoryName: `provenance/${name}`,
    imageScanOnPush: true,
    imageTagMutability: TagMutability.IMMUTABLE,   // a git sha tag can never be reassigned
    encryption: RepositoryEncryption.KMS,
    encryptionKey: props.artifactKey,
    removalPolicy: STATEFUL_REMOVAL,
    emptyOnDelete: AUTO_DELETE_OBJECTS,
    lifecycleRules: [
      { rulePriority: 1, description: 'keep 10 tagged builds',
        tagStatus: TagStatus.TAGGED, tagPrefixList: ['sha-'], maxImageCount: 10 },
      { rulePriority: 2, description: 'expire untagged after 1 day',
        tagStatus: TagStatus.UNTAGGED, maxImageAge: Duration.days(1) },
    ],
  });
}
```

`IMMUTABLE` tags are what make `G13.2` (`GET /v1/version` reports the reviewed `git_sha`) meaningful. With mutable tags, `sha-abc123` could point at different bytes than the reviewer read, and the gate would be checking a label rather than an artifact.

```bash
# ops/build-push.sh
set -euo pipefail
COMPONENT="$1"; SHA="$2"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REG="${ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com"

aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin "$REG"

# App Runner supports x86_64 and arm64; AgentCore Runtime requires arm64. Both are built
# arm64 so one base image and one dependency set serve both.
docker buildx build \
  --platform linux/arm64 \
  --build-arg BUILD_SHA="$SHA" \
  --provenance=false \
  -f "deploy/${COMPONENT}/Dockerfile" \
  -t "${REG}/provenance/${COMPONENT}:sha-${SHA}" \
  --push .

aws ecr describe-images --repository-name "provenance/${COMPONENT}" \
  --image-ids imageTag="sha-${SHA}" \
  --query 'imageDetails[0].{digest:imageDigest,scan:imageScanStatus.status}'
```

`--provenance=false` is not a naming pun: buildx's attestation manifests confuse some registry consumers, and App Runner wants a plain single-platform image.

### 8.2 Service configuration

```typescript
// infra/cdk/lib/api-stack.ts
import { CfnService, CfnAutoScalingConfiguration } from 'aws-cdk-lib/aws-apprunner';

const scaling = new CfnAutoScalingConfiguration(this, 'Scaling', {
  autoScalingConfigurationName: 'provenance-apprunner-scaling',
  maxConcurrency: 40,     // requests per instance before a new one is added
  minSize: 1,             // one always-warm instance: cold starts are a demo risk (G13.8)
  maxSize: 2,             // pinned 1-2 per specs/15_API_SPEC.md §14 and §17.1
});

const service = new CfnService(this, 'ControlPlane', {
  serviceName: 'provenance-control-plane',
  autoScalingConfigurationArn: scaling.attrAutoScalingConfigurationArn,
  instanceConfiguration: {
    cpu: '1 vCPU',
    memory: '2 GB',
    instanceRoleArn: instanceRole.roleArn,
  },
  healthCheckConfiguration: {
    protocol: 'HTTP',
    path: '/v1/healthz',        // liveness only: no auth, no DB, no rate limit
    interval: 10,
    timeout: 5,
    healthyThreshold: 1,
    unhealthyThreshold: 5,
  },
  networkConfiguration: {
    egressConfiguration: { egressType: 'DEFAULT' },   // public egress; see §8.5
    ingressConfiguration: { isPubliclyAccessible: true },
    ipAddressType: 'IPV4',
  },
  observabilityConfiguration: {
    observabilityEnabled: true,
    observabilityConfigurationArn: xrayObservability.attrObservabilityConfigurationArn,
  },
  sourceConfiguration: {
    autoDeploymentsEnabled: false,      // deploys are explicit; see the note below
    authenticationConfiguration: { accessRoleArn: accessRole.roleArn },
    imageRepository: {
      imageIdentifier: `${this.account}.dkr.ecr.us-east-1.amazonaws.com/provenance/control-plane:sha-${process.env.PV_GIT_SHA}`,
      imageRepositoryType: 'ECR',
      imageConfiguration: {
        port: '8080',
        startCommand: 'uvicorn services.control_plane.app.main:app --host 0.0.0.0 --port 8080 --workers 2',
        runtimeEnvironmentVariables: [
          { name: 'APP_ENV', value: 'prod' },
          { name: 'APP_BASE_URL', value: 'https://api.provenance.app' },
          { name: 'WEB_BASE_URL', value: 'https://app.provenance.app' },
          { name: 'BUILD_SHA', value: process.env.PV_GIT_SHA! },
          // ... the full non-secret manifest from §12
        ],
        runtimeEnvironmentSecrets: [
          { name: 'COCKROACH_DATABASE_URL',           value: `${props.dbSecret.secretArn}:app_url::` },
          { name: 'COCKROACH_KERNEL_URL',             value: `${props.dbSecret.secretArn}:kernel_url::` },
          { name: 'COGNITO_AGENT_CLIENT_SECRET_ARN',  value: `${props.cognitoSecret.secretArn}:agent_client_secret::` },
          { name: 'COGNITO_WORKER_CLIENT_SECRET_ARN', value: `${props.cognitoSecret.secretArn}:worker_client_secret::` },
          { name: 'MCP_AUTH_SECRET_ARN',              value: `${props.mcpSecret.secretArn}:agent_url::` },
          { name: 'PROVENANCE_CAPABILITY_HMAC_KEY',   value: `${props.cryptoSecret.secretArn}:capability_hmac_key::` },
          { name: 'CURSOR_HMAC_KEY',                  value: `${props.cryptoSecret.secretArn}:cursor_hmac_key::` },
          { name: 'INGEST_ALIAS_HMAC_KEY',            value: `${props.cryptoSecret.secretArn}:alias_hmac_key::` },
        ],
      },
    },
  },
});
```

Notes on choices a reviewer will question:

- **`autoDeploymentsEnabled: false`.** An auto-deploy on ECR push means a `docker push` during rehearsal restarts the service. Deploys are one explicit command (`aws apprunner start-deployment`), which also makes `G13.2`'s build-sha equality a statement about a decision rather than a race.
- **`--workers 2` with 1 vCPU.** Two uvicorn workers on one vCPU is deliberate: the workload is I/O-bound (CockroachDB round trips, Bedrock waits), so a second worker absorbs a blocked event loop. It also doubles the in-process rate-limit counters, which `specs/15_API_SPEC.md` §17.1 already calls a known gap; the limits are cost guards, not security controls, and the note there stands.
- **The three `*_ARN`-named entries delivered as secrets.** `G13.6` asserts `RuntimeEnvironmentSecrets` contains the keys `COCKROACH_DATABASE_URL`, `COGNITO_AGENT_CLIENT_SECRET_ARN`, and `MCP_AUTH_SECRET_ARN`. Two of those names end in `_ARN` but are delivered through the secrets channel and therefore arrive as *values*, not ARNs. The settings object accepts either shape and resolves an ARN-looking value lazily, which keeps the gate honest and keeps account ids out of plaintext configuration.
- **`:key::` suffix syntax.** App Runner resolves a JSON key inside a Secrets Manager secret with `<arn>:<json-key>::` (empty version stage, empty version id). Getting this wrong injects the whole JSON blob as the value, and the failure looks like a malformed connection string.

### 8.3 The two IAM roles

App Runner needs two distinct roles, and conflating them is the most common App Runner mistake.

```typescript
// Access role: used by the App Runner SERVICE to pull from ECR. Not the app's identity.
const accessRole = new Role(this, 'AccessRole', {
  roleName: 'provenance-apprunner-access-role',
  assumedBy: new ServicePrincipal('build.apprunner.amazonaws.com'),
  managedPolicies: [ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSAppRunnerServicePolicyForECRAccess')],
});
props.artifactKey.grantDecrypt(accessRole);      // the ECR repo is KMS-encrypted

// Instance role: the RUNNING CONTAINER's identity. This is the app's least-privilege set.
const instanceRole = new Role(this, 'InstanceRole', {
  roleName: 'provenance-apprunner-instance-role',
  assumedBy: new ServicePrincipal('tasks.apprunner.amazonaws.com'),
});
```

Instance role policy, complete:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "PresignAndVerifyArtifacts",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:GetObjectAttributes"],
      "Resource": [
        "arn:aws:s3:::provenance-artifacts-us-east-1/raw/*",
        "arn:aws:s3:::provenance-artifacts-us-east-1/normalized/*",
        "arn:aws:s3:::provenance-artifacts-us-east-1/ses/*" ] },

    { "Sid": "HeadObjectNeedsListOnPrefix",
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::provenance-artifacts-us-east-1",
      "Condition": { "StringLike": { "s3:prefix": ["raw/*", "normalized/*", "ses/*"] } } },

    { "Sid": "UseArtifactKey",
      "Effect": "Allow",
      "Action": ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"],
      "Resource": "arn:aws:kms:us-east-1:<account>:key/<artifact-key-id>" },

    { "Sid": "InvokeCanonicalModelsOnly",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": [
        "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-haiku-4-5",
        "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-opus-5",
        "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0" ] },

    { "Sid": "InvokeAgentRuntime",
      "Effect": "Allow",
      "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
      "Resource": "arn:aws:bedrock-agentcore:us-east-1:<account>:runtime/provenance_agents*" },

    { "Sid": "PublishDomainEvents",
      "Effect": "Allow",
      "Action": ["events:PutEvents"],
      "Resource": "arn:aws:events:us-east-1:<account>:event-bus/provenance-domain-bus" },

    { "Sid": "SendApprovedActionEmail",
      "Effect": "Allow",
      "Action": ["ses:SendEmail", "ses:SendRawEmail"],
      "Resource": "arn:aws:ses:us-east-1:<account>:identity/provenance.app",
      "Condition": {
        "StringEquals": {
          "ses:FromAddress": "disputes@provenance.app",
          "ses:ConfigurationSetName": "provenance-outbound" } } },

    { "Sid": "StartTextractForScannedDocuments",
      "Effect": "Allow",
      "Action": ["textract:StartDocumentAnalysis", "textract:DetectDocumentText",
                 "textract:AnalyzeDocument"],
      "Resource": "*" },

    { "Sid": "PassTextractPublishRole",
      "Effect": "Allow",
      "Action": ["iam:PassRole"],
      "Resource": "arn:aws:iam::<account>:role/provenance-textract-publish-role",
      "Condition": { "StringEquals": { "iam:PassedToService": "textract.amazonaws.com" } } },

    { "Sid": "ReadOwnSecrets",
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": [
        "arn:aws:secretsmanager:us-east-1:<account>:secret:provenance/db-*",
        "arn:aws:secretsmanager:us-east-1:<account>:secret:provenance/cognito-*",
        "arn:aws:secretsmanager:us-east-1:<account>:secret:provenance/crypto-*",
        "arn:aws:secretsmanager:us-east-1:<account>:secret:provenance/mcp-*" ] },

    { "Sid": "Telemetry",
      "Effect": "Allow",
      "Action": ["logs:CreateLogStream", "logs:PutLogEvents", "cloudwatch:PutMetricData",
                 "xray:PutTraceSegments", "xray:PutTelemetryRecords"],
      "Resource": "*",
      "Condition": { "StringEqualsIfExists": { "cloudwatch:namespace": "Provenance" } } }
  ]
}
```

`ses:FromAddress` and `ses:ConfigurationSetName` conditions mean the control plane cannot send from an arbitrary address or bypass the configuration set that produces the bounce events the action plane depends on. `bedrock:InvokeModel` is enumerated to exactly the three canonical model ids, so a code path that reached for a fourth model would fail closed rather than quietly incur cost on an unreviewed model.

There is no `secretsmanager:PutSecretValue`, no `s3:DeleteObject`, no `sqs:*`, and no `scheduler:*`. The control plane reads secrets, writes evidence, publishes events, and sends one kind of email.

### 8.4 Custom domain

```typescript
const domain = new CfnCustomDomain(this, 'ApiDomain', {
  serviceArn: service.attrServiceArn,
  domainName: 'api.provenance.app',
  enableWwwSubdomain: false,
});
```

App Runner returns CNAME validation records that must be added at the registrar. Until they validate, `APP_BASE_URL` should point at the generated `*.awsapprunner.com` host so the stack is testable before DNS lands; the value is a single environment variable and the API specification's §1.1 host is the target state, not a build blocker.

CORS is configured in the application, not the platform, because App Runner has no CORS layer:

```python
# services/control_plane/app/main.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_base_url, "http://localhost:3000"],
    allow_credentials=False,                      # bearer tokens, never cookies
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["authorization", "content-type", "idempotency-key",
                   "x-provenance-trace-id", "prefer"],
    expose_headers=["x-provenance-trace-id", "x-provenance-request-id",
                    "x-provenance-case-revision", "idempotency-key",
                    "idempotency-replayed", "retry-after",
                    "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"],
    max_age=600,
)
```

`allow_credentials=False` with an explicit origin list is the correct pairing. The frontend sends `Authorization`, never a cookie, so credentialed CORS would only widen the surface.

### 8.5 VPC egress, and why there is none

`egressType: 'DEFAULT'` means the container egresses through an AWS-managed public path. This is the right choice here for one concrete reason: **CockroachDB Cloud Basic does not offer IP allowlisting or PrivateLink**, so a VPC connector would buy no network-layer restriction, and it would add a NAT gateway (roughly USD 32 per month plus data processing) and measurable cold-start latency to reach the same public TLS endpoint.

The production path, stated so the choice is legible rather than accidental:

1. Upgrade the cluster to CockroachDB Cloud Standard or Advanced.
2. Attach an App Runner VPC connector to two private subnets.
3. Route egress through a NAT gateway with an Elastic IP, and add that EIP to the cluster's IP allowlist; or configure AWS PrivateLink to the cluster and reach it over a private endpoint.
4. Keep the SQL role separation unchanged. Network reachability is defence in depth; the grants in §11.5 remain the actual permission boundary.

None of that is deployed, and claiming a private path that does not exist would be exactly the kind of statement `quality/23_PHASE_GATES.md` §3 exists to prevent.

### 8.6 CockroachDB TLS connection pooling

Three pools, one per SQL role that the control plane uses, created at startup and never mixed. The Memory Kernel physically cannot use the app pool, because the Kernel module is constructed with the kernel pool and nothing else.

```python
# packages/python/provenance_db/pool.py
from __future__ import annotations
import ssl
from dataclasses import dataclass

import asyncpg


@dataclass(frozen=True, slots=True)
class PoolSpec:
    dsn: str
    min_size: int
    max_size: int
    application_name: str
    read_only: bool


def _ssl_context() -> ssl.SSLContext:
    """CockroachDB Cloud Basic presents a certificate chained to a public CA, so the
    container's trust store is sufficient. verify-full (hostname + chain) is mandatory;
    sslmode=require alone authenticates nothing and is a silent MITM window.

    For a Standard/Advanced cluster, download the cluster CA and point cafile at it:
      ccloud cluster cert download provenance-prod --output /etc/ssl/certs/cc-provenance-ca.crt
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


async def create_pool(spec: PoolSpec) -> asyncpg.Pool:
    async def _init(conn: asyncpg.Connection) -> None:
        # Applied to EVERY connection, including ones created after a pool refill.
        await conn.execute("SET application_name = $1", spec.application_name)
        await conn.execute("SET statement_timeout = '15s'")
        await conn.execute("SET lock_timeout = '3s'")
        await conn.execute("SET idle_in_transaction_session_timeout = '30s'")
        if spec.read_only:
            await conn.execute("SET default_transaction_read_only = true")

    return await asyncpg.create_pool(
        dsn=spec.dsn,
        min_size=spec.min_size,
        max_size=spec.max_size,
        max_inactive_connection_lifetime=300.0,
        command_timeout=20.0,
        # CockroachDB Cloud routes through a connection proxy. Server-side prepared
        # statement caching across a proxied, load-balanced connection is the single
        # most common source of "prepared statement does not exist" in production.
        statement_cache_size=0,
        max_cached_statement_lifetime=0,
        ssl=_ssl_context(),
        init=_init,
        server_settings={"application_name": spec.application_name},
    )


APP_POOL = PoolSpec(dsn_env="COCKROACH_DATABASE_URL", min_size=2, max_size=10,
                    application_name="provenance-app", read_only=False)
KERNEL_POOL = PoolSpec(dsn_env="COCKROACH_KERNEL_URL", min_size=1, max_size=6,
                       application_name="provenance-kernel", read_only=False)
READ_POOL = PoolSpec(dsn_env="COCKROACH_DATABASE_URL", min_size=1, max_size=6,
                     application_name="provenance-read", read_only=True)
```

Sizing rationale: 2 instances x (10 + 6 + 6) = 44 maximum connections. CockroachDB Cloud Basic's connection allowance comfortably exceeds that at demo scale, and the numbers are deliberately small so a connection leak shows up as pool exhaustion in one instance rather than as cluster-wide throttling. The retrieval read pool is `default_transaction_read_only = true` so a refactor that put a write on the retrieval path fails at the database rather than at review.

The DSN itself, as stored in `provenance/db`:

```text
postgresql://pv_app_reader_writer:<password>@<cluster-host>:26257/provenance?sslmode=verify-full&application_name=provenance-app
postgresql://pv_kernel_writer:<password>@<cluster-host>:26257/provenance?sslmode=verify-full&application_name=provenance-kernel
```

CockroachDB Cloud Basic hosts include a cluster routing id in the database portion for some connection forms; the value written to Secrets Manager is whatever `ccloud cluster sql --print-connection-url` emits for that role (§11.3), never hand-assembled.

### 8.7 Secrets Manager layout

Four secrets, each a JSON document, so the number of `GetSecretValue` calls at cold start is four rather than a dozen.

| Secret | Keys |
|---|---|
| `provenance/db` | `app_url`, `kernel_url`, `agent_url`, `migrator_url`, `ops_reader_url` |
| `provenance/cognito` | `agent_client_secret`, `worker_client_secret` |
| `provenance/crypto` | `capability_hmac_key`, `capability_hmac_kid`, `cursor_hmac_key`, `alias_hmac_key` |
| `provenance/mcp` | `agent_url`, `mcp_endpoint`, `mcp_bearer` |

```bash
# ops/secrets-populate.sh — run once, after §11 creates the SQL roles.
# Values are read from a local file that is gitignored and shredded afterwards.
set -euo pipefail
aws secretsmanager put-secret-value --secret-id provenance/crypto --secret-string "$(jq -n \
  --arg cap "$(openssl rand -base64 32)" \
  --arg cur "$(openssl rand -base64 32)" \
  --arg ali "$(openssl rand -base64 32)" \
  '{capability_hmac_key:$cap, capability_hmac_kid:"k1", cursor_hmac_key:$cur, alias_hmac_key:$ali}')"

# G13.6 companion: prove no secret material is a plaintext environment value.
aws apprunner describe-service --service-arn "$PV_APPRUNNER_ARN" \
  | jq '.Service.SourceConfiguration.ImageRepository.ImageConfiguration.RuntimeEnvironmentVariables
        | to_entries[] | select(.value|test("://|AKIA|BEGIN "))'
#   → no output
```

`capability_hmac_kid` exists because `specs/15_API_SPEC.md` §17.2 requires versioned capability-proof keys with a 30-day rotation and a 30-minute overlap. The proof header carries the `kid`; verification tries the current key, then the previous one within the overlap window.

---

## 9. Bedrock AgentCore Runtime

### 9.1 What runs there

One runtime, `provenance_agents`, serving both LangGraph graphs (`ingestion_graph` and `advocate_graph` from `implementation/00_IMPLEMENTATION_MAP.md` §5). The graph to run is selected by the invocation payload, not by a separate runtime, because two runtimes would double the cold-start surface and the container is identical.

AgentCore Runtime requirements the container must satisfy:

- **linux/arm64** only.
- Listens on **port 8080**.
- Serves **`POST /invocations`** (the agent entry point) and **`GET /ping`** (health).
- Stateless per invocation. LangGraph checkpoints are transient only; `implementation/01_SYSTEM_ARCHITECTURE_DETAILED.md` §12 forbids treating them as canonical, and deleting every checkpoint must leave product memory correct.

```python
# agents/runtime/server.py
from fastapi import FastAPI, Request

app = FastAPI()


@app.get("/ping")
async def ping() -> dict:
    return {"status": "healthy"}


@app.post("/invocations")
async def invoke(request: Request) -> dict:
    payload = await request.json()
    # The ONLY authority in this payload is agent_run_id + capability_proof. Everything
    # else the graph needs it fetches from /internal/v1/agent-runs/{agent_run_id}.
    graph = {"ingestion_graph": run_ingestion, "advocate_graph": run_advocate}[payload["graph_name"]]
    return await graph(
        agent_run_id=payload["agent_run_id"],
        capability_proof=payload["capability_proof"],
        trace_id=payload["trace_id"],
        memory_mode=payload.get("memory_mode", "ON"),      # Judge Mode counterfactual
    )
```

The graph never receives a `user_id` or a `tenant_id`. `GET /internal/v1/agent-runs/{id}` deliberately omits them (`specs/15_API_SPEC.md` §9.2), so there is nothing for a model to see and repeat.

### 9.2 Runtime creation

There is no CDK L2 for AgentCore Runtime. It is created with the control-plane API through a custom resource so the ARN is a stack output rather than a copied string.

```typescript
// infra/cdk/lib/agent-stack.ts
import { AwsCustomResource, AwsCustomResourcePolicy, PhysicalResourceId } from 'aws-cdk-lib/custom-resources';
import { StringParameter } from 'aws-cdk-lib/aws-ssm';

const apiBaseUrl = StringParameter.valueForStringParameter(this, '/provenance/api/base-url');

const runtime = new AwsCustomResource(this, 'AgentRuntime', {
  onCreate: {
    service: 'bedrock-agentcore-control',
    action: 'CreateAgentRuntime',
    parameters: {
      agentRuntimeName: 'provenance_agents',          // underscores only; hyphens are rejected
      description: 'Provenance LangGraph interpreter and advocate graphs',
      roleArn: agentExecutionRole.roleArn,
      networkConfiguration: { networkMode: 'PUBLIC' },
      protocolConfiguration: { serverProtocol: 'HTTP' },
      agentRuntimeArtifact: {
        containerConfiguration: {
          containerUri: `${this.account}.dkr.ecr.us-east-1.amazonaws.com/provenance/agent-runtime:sha-${process.env.PV_GIT_SHA}`,
        },
      },
      authorizerConfiguration: {
        customJWTAuthorizer: {
          discoveryUrl: `${props.cognitoIssuer}/.well-known/openid-configuration`,
          allowedClients: [props.workerClientId],
        },
      },
      environmentVariables: {
        APP_BASE_URL: apiBaseUrl,
        COGNITO_TOKEN_ENDPOINT: `https://${props.hostedUiDomain}/oauth2/token`,
        COGNITO_AGENT_CLIENT_ID: props.agentClientId,
        COGNITO_AGENT_CLIENT_SECRET_ARN: props.cognitoSecret.secretArn,
        MCP_SERVER_URL: props.mcpServerUrl,
        MCP_AUTH_SECRET_ARN: props.mcpSecret.secretArn,
        BEDROCK_EXTRACTION_MODEL_ID: 'anthropic.claude-haiku-4-5',
        BEDROCK_REASONING_MODEL_ID: 'anthropic.claude-opus-5',
        BEDROCK_EMBEDDING_MODEL_ID: 'amazon.titan-embed-text-v2:0',
        EMBEDDING_DIMENSIONS: '1024',
        EMBEDDING_VERSION: 'v1',
        OTEL_SERVICE_NAME: 'provenance-agent-runtime',
        PV_MCP_ENABLED: 'true',
      },
    },
    physicalResourceId: PhysicalResourceId.fromResponse('agentRuntimeId'),
  },
  onUpdate: { /* UpdateAgentRuntime with the same shape plus agentRuntimeId */ },
  onDelete: {
    service: 'bedrock-agentcore-control',
    action: 'DeleteAgentRuntime',
    parameters: { agentRuntimeId: new PhysicalResourceIdReference() },
  },
  policy: AwsCustomResourcePolicy.fromSdkCalls({ resources: AwsCustomResourcePolicy.ANY_RESOURCE }),
  installLatestAwsSdk: true,
});

new StringParameter(this, 'RuntimeArnParam', {
  parameterName: '/provenance/agent/runtime-arn',
  stringValue: runtime.getResponseField('agentRuntimeArn'),
});
```

The equivalent CLI, which is what to reach for when the custom resource fails and the error message is unhelpful:

```bash
aws bedrock-agentcore-control create-agent-runtime \
  --region us-east-1 \
  --agent-runtime-name provenance_agents \
  --role-arn "arn:aws:iam::${ACCOUNT}:role/provenance-agentcore-execution-role" \
  --network-configuration '{"networkMode":"PUBLIC"}' \
  --protocol-configuration '{"serverProtocol":"HTTP"}' \
  --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${REG}/provenance/agent-runtime:sha-${SHA}\"}}" \
  --authorizer-configuration "$(cat infra/agentcore/authorizer.json)" \
  --environment-variables "file://infra/agentcore/env.json"

aws bedrock-agentcore-control list-agent-runtimes --region us-east-1 \
  --query 'agentRuntimes[?agentRuntimeName==`provenance_agents`].[agentRuntimeArn,status]' --output text
```

The AgentCore control-plane API surface is the most version-sensitive thing in this document. `infra/agentcore/` holds the exact request JSON so a shape change is a one-file edit, and the Phase 0 checklist includes running `aws bedrock-agentcore-control create-agent-runtime --generate-cli-skeleton` against the installed SDK and diffing it against `infra/agentcore/`. Recorded in §15.7.

### 9.3 The inbound JWT authorizer

```json
// infra/agentcore/authorizer.json
{
  "customJWTAuthorizer": {
    "discoveryUrl": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_XXXXXXXXX/.well-known/openid-configuration",
    "allowedClients": ["<provenance-workers client id>"]
  }
}
```

**Why `provenance-workers` and not a fourth app client.** Two components invoke the runtime: the control plane (immediately after `POST /v1/artifacts/{id}/complete`, per `specs/15_API_SPEC.md` §8.19 step 5) and `provenance-advocate-dispatch` (on `case.reopened.v1` and its siblings). Both are trusted control-plane-side workloads, and `provenance-workers` is the existing client for exactly that population. Adding a fourth app client would contradict the frozen three-client design in §2.1 of the API specification; reusing `provenance-agent-runtime` would mean the agent's own callback credential also opens the front door of the runtime it runs in, which is strictly worse.

**The authorizer is not the authorization.** It answers only "did a Provenance workload send this request". What the invocation may *do* is bounded entirely by the `agent_run_id` in the payload, which is a server-written row binding `tenant_id`, `user_id`, `graph_name`, `input_artifact_id`, and `allowed_case_ids`, expiring in 15 minutes, and unforgeable by the caller. A stolen worker token gets an attacker an agent invocation that can only touch a run the control plane already created for a real user, and every subsequent `/internal/v1` call re-reads and re-checks that row.

No `allowedAudience` is configured, because a Cognito access token has no `aud` claim; `allowedClients` matches `client_id`, which is the claim that exists. Configuring an audience here produces a runtime that rejects every valid token.

Invocation, with the JWT path:

```python
# services/control_plane/app/agents/invoke.py
import urllib.parse
import httpx

AGENTCORE_HOST = "https://bedrock-agentcore.us-east-1.amazonaws.com"


async def invoke_agent(*, runtime_arn: str, payload: dict, token: str,
                       session_id: str, timeout_s: float = 120.0) -> dict:
    escaped = urllib.parse.quote(runtime_arn, safe="")
    url = f"{AGENTCORE_HOST}/runtimes/{escaped}/invocations?qualifier=DEFAULT"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        # Session id must be >= 33 chars; the agent_run_id plus a prefix satisfies it and
        # makes the AgentCore session directly joinable to the capability row.
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
    }
    async with httpx.AsyncClient(timeout=timeout_s) as c:
        r = await c.post(url, json=payload, headers=headers)
    if r.status_code >= 500:
        raise ApiError("UPSTREAM_UNAVAILABLE", 503, details={"dependency": "AGENTCORE"})
    r.raise_for_status()
    return r.json()
```

The runtime is invoked asynchronously from the control plane's perspective: `/complete` returns `202` immediately and the client polls (`specs/15_API_SPEC.md` §8.19). The invocation runs in a background task whose failure sets `parser_status` back to a retryable state and leaves admitted evidence untouched, because evidence admission precedes semantic commit by design.

### 9.4 AgentCore execution role

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "InvokeCanonicalModelsOnly",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": [
        "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-haiku-4-5",
        "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-opus-5",
        "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0" ] },

    { "Sid": "PullAgentImage",
      "Effect": "Allow",
      "Action": ["ecr:GetAuthorizationToken", "ecr:BatchGetImage",
                 "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"],
      "Resource": "arn:aws:ecr:us-east-1:<account>:repository/provenance/agent-runtime" },

    { "Sid": "ReadAgentCallbackAndMcpSecrets",
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": [
        "arn:aws:secretsmanager:us-east-1:<account>:secret:provenance/cognito-*",
        "arn:aws:secretsmanager:us-east-1:<account>:secret:provenance/mcp-*" ] },

    { "Sid": "Telemetry",
      "Effect": "Allow",
      "Action": ["logs:CreateLogStream", "logs:PutLogEvents",
                 "xray:PutTraceSegments", "xray:PutTelemetryRecords",
                 "cloudwatch:PutMetricData"],
      "Resource": "*" }
  ]
}
```

What the agent execution role **cannot** do, and why each absence matters:

| Absent permission | Consequence |
|---|---|
| `s3:*` | A malicious artifact cannot make the agent read another artifact's bytes. Content arrives only through `GET /internal/v1/agent-runs/{id}/artifact-content`, scoped to the run's bound artifact. |
| `ses:*` | The Advocate drafts and cannot send. Invariant 4, in IAM. |
| `events:PutEvents` | The agent cannot manufacture a domain event that a consumer would treat as committed state. |
| `secretsmanager` on `provenance/db` or `provenance/crypto` | No SQL credential and no capability-proof key. The agent's only database reach is the MCP server as `pv_agent_reader` on five views. |
| `bedrock-agentcore:*` | The agent cannot invoke itself or another runtime, so there is no recursion path and no way to escape its own capability. |

### 9.5 Judge Mode counterfactual

The memory OFF/ON toggle is a field on the invocation payload (`memory_mode`), not a second runtime, a second prompt, or a second model. `agent_runs.memory_mode` and `agent_runs.is_counterfactual` (`specs/10_DATABASE_DDL.md` §0 deviation 5) record which one ran, and the Kernel rejects a proposal originating from a counterfactual run. There is no infrastructure difference between the two sides, which is precisely what makes the comparison defensible under the "did you nerf it" question in `00_PRODUCT.md` §7 R2.

---

## 10. Amplify Hosting

### 10.1 App and branch

```typescript
// infra/cdk/lib/web-stack.ts
import { CfnApp, CfnBranch, CfnDomain } from 'aws-cdk-lib/aws-amplify';

const amplifyApp = new CfnApp(this, 'WebApp', {
  name: 'provenance-web',
  description: 'Provenance Next.js experience plane',
  platform: 'WEB_COMPUTE',                    // Next.js SSR; WEB is static-export only
  repository: 'https://github.com/<org>/provenance',
  accessToken: undefined,                     // set out of band; never in a template
  iamServiceRole: amplifyRole.roleArn,
  enableBranchAutoDeletion: false,
  customRules: [
    // SPA-style fallbacks are wrong for an SSR app; only the static asset rule is needed.
    { source: '/<*>', target: '/index.html', status: '404-200' },
  ],
  customHeaders: [
    'customHeaders:',
    '  - pattern: "**"',
    '    headers:',
    '      - key: Strict-Transport-Security',
    '        value: "max-age=63072000; includeSubDomains; preload"',
    '      - key: X-Content-Type-Options',
    '        value: "nosniff"',
    '      - key: X-Frame-Options',
    '        value: "DENY"',
    '      - key: Referrer-Policy',
    '        value: "strict-origin-when-cross-origin"',
    '      - key: Content-Security-Policy',
    '        value: "default-src \'self\'; connect-src \'self\' https://api.provenance.app https://provenance-auth.auth.us-east-1.amazoncognito.com https://cognito-idp.us-east-1.amazonaws.com https://provenance-artifacts-us-east-1.s3.us-east-1.amazonaws.com; img-src \'self\' data:; style-src \'self\' \'unsafe-inline\'; script-src \'self\'; frame-ancestors \'none\'; base-uri \'self\'; form-action \'self\'"',
  ].join('\n'),
  environmentVariables: [
    { name: 'NEXT_PUBLIC_API_BASE_URL', value: 'https://api.provenance.app' },
    { name: 'NEXT_PUBLIC_AWS_REGION', value: 'us-east-1' },
    { name: 'NEXT_PUBLIC_COGNITO_USER_POOL_ID', value: props.userPoolId },
    { name: 'NEXT_PUBLIC_COGNITO_WEB_CLIENT_ID', value: props.webClientId },
    { name: 'NEXT_PUBLIC_COGNITO_DOMAIN', value: props.hostedUiDomain },
    { name: 'NEXT_PUBLIC_COGNITO_SCOPES', value: 'openid email profile provenance.memory/read' },
    { name: 'NEXT_PUBLIC_BUILD_SHA', value: process.env.PV_GIT_SHA! },
    { name: 'AMPLIFY_DIFF_DEPLOY', value: 'false' },
    { name: '_LIVE_UPDATES', value: '[{"name":"Node.js version","pkg":"node","type":"nvm","version":"20"}]' },
  ],
});

const mainBranch = new CfnBranch(this, 'MainBranch', {
  appId: amplifyApp.attrAppId,
  branchName: 'main',
  stage: 'PRODUCTION',
  enableAutoBuild: true,
  framework: 'Next.js - SSR',
  enablePerformanceMode: false,
});

new CfnDomain(this, 'WebDomain', {
  appId: amplifyApp.attrAppId,
  domainName: 'provenance.app',
  subDomainSettings: [{ prefix: 'app', branchName: 'main' }],
  enableAutoSubDomain: false,
});
```

`platform: 'WEB_COMPUTE'` is required, not preferred: the app has server components and route handlers, and `WEB` would build a static export that cannot render them.

The CSP `connect-src` list is the API origin, the Cognito hosted UI, the Cognito IdP endpoint (for JWKS and token refresh), and the S3 artifact bucket (for the direct pre-signed `PUT`). Omitting the bucket breaks uploads in a way that looks like a CORS bug in the browser console; omitting the IdP host breaks login silently on token refresh.

### 10.2 Build spec

```yaml
# amplify.yml (repo root)
version: 1
frontend:
  phases:
    preBuild:
      commands:
        - cd apps/web
        - npm ci
        # The client is generated from the deployed OpenAPI document, so a frontend build
        # cannot drift from the API it talks to. specs/15_API_SPEC.md §16.4.
        - npx openapi-typescript "$NEXT_PUBLIC_API_BASE_URL/v1/openapi.json" -o src/lib/api/schema.d.ts
    build:
      commands:
        - npm run lint
        # No hard-coded UUIDs in frontend source: a rendered id is a rendered lie (G12.3).
        - >-
          test "$(grep -rnE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
          src --include='*.ts*' | grep -v '__tests__\|\.fixture\.' | wc -l)" -eq 0
        - npm run build
  artifacts:
    baseDirectory: apps/web/.next
    files:
      - '**/*'
  cache:
    paths:
      - apps/web/node_modules/**/*
      - apps/web/.next/cache/**/*
```

### 10.3 API origin

The browser calls `https://api.provenance.app` **directly**. There is no Amplify rewrite proxying `/api/*` to App Runner, and that is a deliberate choice with a cost:

- **Why direct:** a rewrite would put Amplify's compute in the path of every authenticated request, adding a hop and a place for `Authorization` headers to be logged, and it would break the `X-Provenance-Trace-Id` response header contract unless the proxy were configured to pass it through. Direct calls keep the trace header, the rate-limit headers, and the `Retry-After` semantics intact end to end.
- **What it costs:** CORS must be right (§8.4), and the API origin is visible in the client bundle. Neither is a secret, and the API authorizes every request against a validated token regardless of origin.

Token handling in the browser: authorization code + PKCE against the hosted UI, tokens held in memory with a refresh token in a `Secure; HttpOnly` cookie set by a Next.js route handler, never `localStorage`. The API never accepts a cookie (`allow_credentials=False`), so the cookie is only ever exchanged for a bearer token by the app's own server code.

---

## 11. CockroachDB Cloud

### 11.1 ccloud is the third qualifying CockroachDB tool

**Stated explicitly for the submission's tool-usage disclosure:** this build uses three distinct CockroachDB tools, and the hackathon requires at least two.

| # | Tool | Where it is used | What it does that the others do not |
|---|---|---|---|
| 1 | **CockroachDB Cloud** (the database itself, with Distributed Vector Indexing) | canonical state, `evidence_embedding_ann_idx`, transactional outbox, `SERIALIZABLE` kernel transactions | the product's memory plane |
| 2 | **CockroachDB Cloud Managed MCP Server** | LangGraph agents read five `_v1` views as `pv_agent_reader` | the agent's visible, grant-bounded memory read path |
| 3 | **ccloud CLI** | provisioning, inspection, SQL role creation, connection-string issuance, probe execution, teardown | the entire lifecycle of the cluster, and every command in §11.2 through §11.4 |

`ccloud` counts as a qualifying tool because it is the official CockroachDB Cloud command-line interface and it is doing real, load-bearing work here: it creates the cluster, mints the four SQL role credentials that constitute the security boundary, prints the connection URLs that go into Secrets Manager, opens the SQL shell that runs the Phase 0 probes, and deletes the cluster at teardown. It is not decorative usage added to reach a count.

### 11.2 Provisioning

```bash
# ---- Authenticate. Opens a browser; the token lands in ~/.ccloud/credentials.
ccloud auth login

# ---- Confirm the organization and that the free trial credits are attached.
ccloud org list
ccloud info

# ---- Create the cluster. Basic plan, AWS, us-east-1, same region as every AWS resource.
ccloud cluster create basic provenance-prod \
  --cloud aws \
  --region us-east-1

# Flag names have moved between ccloud releases (the plan has been expressed as a
# subcommand, as --plan, and as --serverless). `ccloud cluster create --help` is the
# authority; record the ccloud version and the exact command that worked:
ccloud version | tee -a ops/cluster-probe.txt

# ---- Set a hard spend limit so a runaway seed cannot produce a surprise invoice.
#      Basic includes a monthly free allowance; this caps anything beyond it.
ccloud cluster update provenance-prod --spend-limit 25    # USD/month

# ---- Inspect.
ccloud cluster list
ccloud cluster describe provenance-prod
#   → note: cluster id, state (CREATED), plan, cloud provider, region, version
```

### 11.3 Connecting, and issuing the four role credentials

```bash
# ---- One SQL user per SQL role. ccloud creates the login user; the GRANTs in §11.5
#      are what actually differentiate them. Passwords are generated, shown once,
#      and written straight into Secrets Manager. They are never echoed to a file.
for role in pv_migrator pv_app_reader_writer pv_kernel_writer pv_agent_reader; do
  ccloud cluster user create provenance-prod "$role" --password "$(openssl rand -base64 24)"
done
ccloud cluster user list provenance-prod

# ---- Print a connection URL for a specific user. This is the value that goes into
#      provenance/db; never hand-assemble a DSN.
ccloud cluster sql provenance-prod --user pv_app_reader_writer --print-connection-url
ccloud cluster sql provenance-prod --user pv_kernel_writer     --print-connection-url
ccloud cluster sql provenance-prod --user pv_agent_reader      --print-connection-url
ccloud cluster sql provenance-prod --user pv_migrator          --print-connection-url

# ---- Interactive SQL shell, used for the Phase 0 probes and the verification queries.
ccloud cluster sql provenance-prod --user pv_migrator

# ---- For a Standard/Advanced cluster, the CA must be downloaded; Basic uses a public CA.
# ccloud cluster cert download provenance-prod --output /etc/ssl/certs/cc-provenance-ca.crt
```

Store the four URLs:

```bash
aws secretsmanager put-secret-value --secret-id provenance/db --secret-string "$(jq -n \
  --arg app "$APP_URL" --arg kernel "$KERNEL_URL" \
  --arg agent "$AGENT_URL" --arg migrator "$MIGRATOR_URL" \
  '{app_url:$app, kernel_url:$kernel, agent_url:$agent, migrator_url:$migrator}')"
```

`migrator_url` is read by CI and by `ops/migrate.sh` only. It is **not** in the App Runner secret list in §8.2, so the running service has no path to DDL.

### 11.4 Phase 0 probe: `feature.vector_index.enabled`

`specs/10_DATABASE_DDL.md` §1 owns the full probe battery. This is the deployment-facing subset and, critically, what to do with each outcome. Run it before migration 0002.

```sql
-- Connected as pv_migrator via: ccloud cluster sql provenance-prod --user pv_migrator

-- 1. Build and logical version, recorded verbatim in ops/cluster-probe.txt.
SELECT version();
SHOW CLUSTER SETTING version;

-- 2. Every vector-related cluster setting and its current value.
SELECT variable, value FROM [SHOW CLUSTER SETTINGS] WHERE variable ILIKE '%vector%';

-- 3. The specific gate.
SHOW CLUSTER SETTING feature.vector_index.enabled;

-- 4. If it is false, enable it. This is the documented prerequisite for CREATE VECTOR INDEX.
SET CLUSTER SETTING feature.vector_index.enabled = true;

-- 5. Beam size and partition tunables. Defaults: 32 / 16 / 128. Leave them alone for v1
--    and record the effective values so Judge Mode can display what retrieval ran with.
SHOW vector_search_beam_size;
SELECT variable, value FROM [SHOW CLUSTER SETTINGS]
WHERE variable IN ('sql.vecindex.min_partition_size', 'sql.vecindex.max_partition_size');

-- 6. Prove the index actually builds with a prefix column and the cosine opclass.
CREATE TABLE IF NOT EXISTS _pv_probe (id UUID NOT NULL PRIMARY KEY, k UUID NOT NULL, v VECTOR(1024));
CREATE VECTOR INDEX _pv_probe_a ON _pv_probe (k, v vector_cosine_ops);
SHOW INDEXES FROM _pv_probe;
DROP TABLE _pv_probe CASCADE;
```

Outcomes and the predetermined response. There is no branch here that requires a design decision at deploy time.

| Probe outcome | Action | Consequence recorded where |
|---|---|---|
| `feature.vector_index.enabled` is already `true`, and step 6 succeeds | Proceed with §5.1 Variant A: `CREATE VECTOR INDEX evidence_embedding_ann_idx ON evidence_items (user_id, embedding vector_cosine_ops)`. Nothing else changes. | `ops/cluster-probe.txt` |
| Setting exists and is `false`; `SET CLUSTER SETTING` succeeds; step 6 then succeeds | Proceed with Variant A. Add the `SET CLUSTER SETTING` line to `ops/migrate.sh` as a documented prerequisite so a fresh cluster reproduces the state. | `ops/migrate.sh`, probe file |
| **`SET CLUSTER SETTING` is refused on Basic** (insufficient privilege on a managed plan) | Open a CockroachDB Cloud support request to enable it, and in parallel evaluate an upgrade to Standard. Until it is enabled, the vector index cannot be created and Phase 6 cannot exit. This is a **stop condition**, not a workaround. | `quality/23_PHASE_GATES.md` G-6 as NOT RUN, with the refusal text pasted |
| Setting enabled but `vector_cosine_ops` is rejected in step 6 | Fall back to §5.3 Variant C: default (L2) opclass, set `EMBEDDING_NORMALIZATION=L2_UNIT`, and request Titan v2 embeddings with `normalize: true`. On unit vectors L2 ordering equals cosine ordering, so ranking is unchanged and the frozen embedding contract holds. | `EMBEDDING_NORMALIZATION` env var, probe file |
| `CREATE VECTOR INDEX` syntax rejected but `USING cspann` accepted | Use §5.2 Variant B. Semantics identical. | probe file |
| No vector index works at all, in any variant | Disclose a brute-force scan over the user's partition, and accept that the sponsor vector-index submission gate fails. Do **not** silently ship a scan and describe it as a vector index. | `CANONICAL_DECISIONS.md` Phase 0 table, submission disclosure |

The seeding order is a consequence of this section and is not optional: **`IMPORT INTO` is unsupported on a table that carries a vector index, and large batch inserts against a live vector index degrade badly.** The seed therefore bulk-loads `evidence_items` first and creates `evidence_embedding_ann_idx` afterwards.

```bash
# ops/seed.sh — order is load-bearing.
ops/migrate.sh --to 0001                      # identity and aggregates
ops/migrate.sh --to 0002 --skip-vector-index  # evidence tables, NO vector index yet
python -m scripts.seed --profile all --reset  # 32 curated + 18,000 synthetic rows
ccloud cluster sql provenance-prod --user pv_migrator \
  -e "CREATE VECTOR INDEX evidence_embedding_ann_idx ON evidence_items (user_id, embedding vector_cosine_ops);"
ops/migrate.sh --to head                      # 0003 through 0008, views and grants
python -m scripts.seed --verify               # every §18 verification query returns zero rows
```

Building the index after the load is also materially faster than maintaining it through 18,000 inserts, which matters because `specs/10_DATABASE_DDL.md` §19 note 10 identifies the seed as the longest pole in environment setup.

### 11.5 The four SQL roles and their complete grants

Copied from `specs/10_DATABASE_DDL.md` §2 and §15, which own them. Reproduced here because this document is what an operator runs, and an operator who has to open a second document to finish provisioning will not.

```sql
-- ===========================================================================
-- Roles. These are the actual permission boundary. Application-layer checks are
-- defence in depth on top of them, never a substitute.
-- Passwords are created with ccloud (§11.3), never in a migration file.
-- ===========================================================================
CREATE DATABASE IF NOT EXISTS provenance;
USE provenance;

CREATE ROLE IF NOT EXISTS pv_migrator          WITH LOGIN;  -- DDL only, never used by runtime
CREATE ROLE IF NOT EXISTS pv_app_reader_writer WITH LOGIN;  -- control plane, non-canonical writes
CREATE ROLE IF NOT EXISTS pv_kernel_writer     WITH LOGIN;  -- Memory Kernel: sole canonical writer
CREATE ROLE IF NOT EXISTS pv_agent_reader      WITH LOGIN;  -- MCP / LangGraph agents: views only

ALTER DATABASE provenance OWNER TO pv_migrator;
GRANT CONNECT ON DATABASE provenance TO pv_app_reader_writer, pv_kernel_writer, pv_agent_reader;
GRANT USAGE ON SCHEMA public TO pv_app_reader_writer, pv_kernel_writer, pv_agent_reader;
```

```sql
-- ===========================================================================
-- Object grants. Last statement block of migration 0008, after every table and
-- every view exists.
-- ===========================================================================

-- Migrator owns everything; runtime roles get nothing by default.
ALTER TABLE tenants, users, ingest_aliases, counterparties, relationships, contexts, cases,
             source_artifacts, evidence_items, claims, beliefs, belief_versions, belief_support,
             conflicts, commitments, fulfillments, state_transitions, memory_proposals,
             kernel_decisions, prospective_triggers, action_intents, action_executions,
             outbox_events, processed_events, agent_runs, idempotency_records
    OWNER TO pv_migrator;

-- ---- pv_app_reader_writer -------------------------------------------------
GRANT SELECT ON TABLE tenants, users, ingest_aliases, counterparties, relationships, contexts,
                      cases, source_artifacts, evidence_items, claims, beliefs, belief_versions,
                      belief_support, conflicts, commitments, fulfillments, state_transitions,
                      memory_proposals, kernel_decisions, prospective_triggers, action_intents,
                      action_executions, outbox_events, processed_events, agent_runs,
                      idempotency_records
    TO pv_app_reader_writer;

GRANT INSERT, UPDATE ON TABLE tenants, users, ingest_aliases, source_artifacts, action_intents,
                              action_executions, agent_runs, idempotency_records
    TO pv_app_reader_writer;

GRANT INSERT ON TABLE evidence_items, memory_proposals, processed_events TO pv_app_reader_writer;
GRANT UPDATE ON TABLE outbox_events TO pv_app_reader_writer;   -- dispatcher status only

-- ---- pv_kernel_writer -----------------------------------------------------
GRANT SELECT ON TABLE tenants, users, counterparties, relationships, contexts, cases,
                      source_artifacts, evidence_items, claims, beliefs, belief_versions,
                      belief_support, conflicts, commitments, fulfillments, state_transitions,
                      memory_proposals, kernel_decisions, prospective_triggers, action_intents,
                      outbox_events, agent_runs
    TO pv_kernel_writer;

GRANT INSERT, UPDATE ON TABLE counterparties, relationships, contexts, cases, beliefs,
                              belief_versions, conflicts, commitments, prospective_triggers,
                              kernel_decisions, evidence_items
    TO pv_kernel_writer;

GRANT INSERT ON TABLE claims, belief_support, fulfillments, state_transitions, outbox_events
    TO pv_kernel_writer;

GRANT UPDATE ON TABLE memory_proposals TO pv_kernel_writer;

-- The Kernel can never send anything, and can never mint an approval.
REVOKE ALL ON TABLE action_executions, ingest_aliases, idempotency_records, processed_events
    FROM pv_kernel_writer;
REVOKE INSERT, UPDATE ON TABLE action_intents FROM pv_kernel_writer;

-- ---- pv_agent_reader ------------------------------------------------------
-- Views only. Views execute with the owner's table privileges, so no base-table
-- grant is needed, and none is given.
GRANT SELECT ON agent_case_context_v1,
                agent_active_beliefs_v1,
                agent_belief_lineage_v1,
                agent_evidence_retrieval_v1,
                agent_open_obligations_v1
    TO pv_agent_reader;

-- Belt and braces: prove there is nothing else to reach.
REVOKE ALL ON TABLE tenants, users, ingest_aliases, counterparties, relationships, contexts,
                    cases, source_artifacts, evidence_items, claims, beliefs, belief_versions,
                    belief_support, conflicts, commitments, fulfillments, state_transitions,
                    memory_proposals, kernel_decisions, prospective_triggers, action_intents,
                    action_executions, outbox_events, processed_events, agent_runs,
                    idempotency_records
    FROM pv_agent_reader;

-- Nothing new is ever granted implicitly to a runtime role.
ALTER DEFAULT PRIVILEGES FOR ROLE pv_migrator IN SCHEMA public
    REVOKE ALL ON TABLES FROM pv_app_reader_writer, pv_kernel_writer, pv_agent_reader;
```

#### `pv_ops_reader` — created, because a real consumer exists

`CANONICAL_DECISIONS.md` permits an optional fifth role, `pv_ops_reader`. An earlier version of this section declined to create it on the grounds that nobody would use it. That is no longer true: `quality/21_OBSERVABILITY_ANALYTICS.md` §7.3 and §9 run `tools/trace_verify.py` and both analytics queries as `pv_ops_reader`, and `trace_verify` is the mechanism `submission/50_README_DRAFT.md` hands a sceptical judge to falsify "the Memory Trace is a hand-authored fixture." A verifier that runs as `pv_app_reader_writer` proves less, because that role can write the rows it is claiming to verify. So the role is created, strictly read-only, in migration `0008`.

```sql
-- ---- pv_ops_reader --------------------------------------------------------
-- Read-only operations and verification. No INSERT, no UPDATE, no DELETE,
-- no DDL, ever. This role exists so that trace verification is performed by a
-- principal that provably could not have authored what it verifies.
CREATE ROLE IF NOT EXISTS pv_ops_reader LOGIN;
GRANT CONNECT ON DATABASE provenance TO pv_ops_reader;
GRANT USAGE ON SCHEMA public TO pv_ops_reader;

-- The five agent-safe views, so an operator sees exactly what the agent sees.
GRANT SELECT ON agent_case_context_v1,
                agent_active_beliefs_v1,
                agent_belief_lineage_v1,
                agent_evidence_retrieval_v1,
                agent_open_obligations_v1
    TO pv_ops_reader;

-- The eleven operational tables the §7.2 row census and the §6.3 trace
-- assembly query read. Nothing else. In particular: no evidence_items,
-- no claims, no belief_versions bytes, no action_intents.draft_payload.
GRANT SELECT ON TABLE source_artifacts, agent_runs, memory_proposals, kernel_decisions,
                      state_transitions, outbox_events, processed_events,
                      prospective_triggers, action_intents, action_executions,
                      idempotency_records
    TO pv_ops_reader;

-- Provable read-only.
REVOKE INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM pv_ops_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE pv_migrator IN SCHEMA public
    REVOKE ALL ON TABLES FROM pv_ops_reader;
```

```bash
# G12.8 — the verifier's credential cannot write.
ccloud cluster sql provenance-prod --user pv_ops_reader -e "
  UPDATE agent_runs SET tool_calls = NULL WHERE id = (SELECT id FROM agent_runs LIMIT 1);"
#   → ERROR: user pv_ops_reader has no UPDATE privilege on relation agent_runs
ccloud cluster sql provenance-prod --user pv_ops_reader -e "SELECT count(*) FROM agent_runs;"
#   → 1 row
```

`pv_ops_reader` is **not** used by App Runner, by any Lambda, or by AgentCore. It is an operator and CI credential only, and it is not one of the pools in §8.6.

Verification, run immediately after the grants and again in CI:

```bash
# G11.1 — the agent role has no base-table reach at all.
ccloud cluster sql provenance-prod --user pv_migrator --format=csv -e "
  SELECT grantee, table_name, privilege_type
  FROM information_schema.role_table_grants
  WHERE grantee='pv_agent_reader' AND table_name NOT LIKE 'agent\_%\_v1';"
#   → header only; zero data rows

# G11.2 — the boundary is a grant, demonstrated by refusal, not by assertion.
ccloud cluster sql provenance-prod --user pv_agent_reader -e "SELECT id FROM evidence_items LIMIT 1;"
#   → ERROR: user pv_agent_reader has no SELECT privilege on relation evidence_items
ccloud cluster sql provenance-prod --user pv_agent_reader -e "SELECT * FROM agent_active_beliefs_v1 LIMIT 1;"
#   → 1 row
ccloud cluster sql provenance-prod --user pv_agent_reader -e "INSERT INTO claims (id) VALUES (gen_random_uuid());"
#   → ERROR: user pv_agent_reader has no INSERT privilege on relation claims
```

If the first query returns any row, `CANONICAL_DECISIONS.md`'s Phase 0 rule applies without negotiation: **stop Phase 11, do not weaken grants**, and use a controlled read API until the database boundary is proven.

### 11.6 The five agent-safe `_v1` views

The full DDL is owned by `specs/10_DATABASE_DDL.md` §14 and is not duplicated here. What matters operationally is that these five names are the **entire** surface reachable through MCP, that each bakes in a safety predicate the agent cannot omit, and that the names appear verbatim in Memory Trace nodes so a judge can read a trace and then read the view.

| View | What it exposes | The predicate baked in |
|---|---|---|
| `agent_case_context_v1` | case, relationship, counterparty, context title | `c.status <> 'SUPERSEDED'` |
| `agent_active_beliefs_v1` | current belief version joined to each grounding edge, with relation, weight, reason code | `bv.epistemic_status <> 'RETRACTED'`; only `beliefs.current_version_id` |
| `agent_belief_lineage_v1` | the supersession chain and the kernel decision behind each step | joins `kernel_decisions`, so an ungrounded version cannot appear |
| `agent_evidence_retrieval_v1` | normalized evidence text, validity interval, authority, artifact metadata | **`e.retraction_status = 'ACTIVE'`** |
| `agent_open_obligations_v1` | open commitments and open conflicts, unioned | commitments in `PROPOSED/ACTIVE/PARTIAL/DISPUTED`; conflicts in `OPEN/NEEDS_HUMAN` |

`agent_evidence_retrieval_v1`'s filter is the deployment-critical one. Retracted evidence keeps its embedding in the ANN index by design (`specs/10_DATABASE_DDL.md` §5.4), so if the filter were absent from the view, an agent reading through MCP would resurface evidence the user already disowned. The filter lives inside the view precisely so no prompt and no agent discipline is required for it to hold.

Deliberately absent from every view: `users.cognito_sub`, `ingest_aliases`, `action_intents.draft_payload`, `action_executions`, `memory_proposals.payload`, `idempotency_records`, `outbox_events.payload`, `evidence_items.exact_text`, `evidence_items.embedding`, and all `crdb_internal` and `information_schema` access.

### 11.7 CockroachDB Cloud Managed MCP Server

The hackathon's qualifying tool is the **CockroachDB Cloud Managed MCP Server**, which is a distinct product from the self-hosted `cockroachdb-mcp-server`. It is enabled per cluster in the CockroachDB Cloud console and reached over TLS.

Configuration, wired to `pv_agent_reader` **only**:

```json
// infra/agentcore/mcp.json — read by the agent container at startup.
// The connection string is resolved from Secrets Manager (MCP_AUTH_SECRET_ARN) and is
// never present in this file, in the image, or in a log line.
{
  "mcpServers": {
    "cockroachdb": {
      "type": "http",
      "url": "${MCP_SERVER_URL}",
      "headers": {
        "Authorization": "Bearer ${MCP_BEARER}"
      },
      "sqlRole": "pv_agent_reader",
      "accessMode": "READ_ONLY",
      "allowedRelations": [
        "agent_case_context_v1",
        "agent_active_beliefs_v1",
        "agent_belief_lineage_v1",
        "agent_evidence_retrieval_v1",
        "agent_open_obligations_v1"
      ]
    }
  }
}
```

The four properties that make this safe, in descending order of how much work they do:

1. **`pv_agent_reader` and nothing else.** The connection string in `provenance/mcp:agent_url` authenticates as `pv_agent_reader`. That role holds `SELECT` on five views and no privilege on any of the 26 base tables. **The SQL grant is the real permission boundary** — the CockroachDB Cloud Managed MCP Server says so itself, `allowedRelations` above is a convenience filter, and `specs/15_API_SPEC.md` §3.8 relies on the grant rather than on the config. If the config file were deleted entirely, the agent still could not read `evidence_items`.
2. **`CRDB_MCP_ENABLE_WRITE_QUERIES` is deliberately unset.** The managed server is read-only unless that variable is set. It appears nowhere in this repository, and a CI check enforces that:

```bash
# tools/check_mcp_readonly.sh — runs in CI on every push.
if grep -rn "CRDB_MCP_ENABLE_WRITE_QUERIES" . --exclude-dir=.git \
     --exclude=docs/ops/40_INFRA_IAC.md --exclude=tools/check_mcp_readonly.sh; then
  echo "FAIL: write queries must never be enabled on the MCP server"; exit 1
fi
echo "OK: CRDB_MCP_ENABLE_WRITE_QUERIES is unset everywhere"
```

   The two exclusions are this document, which names the variable in order to say it is unset, and the checker itself. Any other occurrence fails the build.
3. **`default_transaction_read_only = true` is forced by the server.** Every session the MCP server opens is read-only at the transaction level, so even a query that somehow reached a writable relation could not write. This is belt-and-braces on top of point 1, and the two together mean a write requires both a grant that does not exist and a setting that is not enabled.
4. **TLS in both directions.** Agent to MCP server over HTTPS; MCP server to cluster over the cluster's TLS endpoint. No plaintext hop exists.

The server exposes ten read tools. Every tool call the agent makes is recorded on the `agent_runs` row with the tool name, the view touched, `sql_role`, `access_mode`, latency, row count, and `denied: true` where a call was refused, and the Memory Trace renders them as first-class nodes including the denied ones (`quality/23_PHASE_GATES.md` §17). MCP is visible and load-bearing rather than decorative: `PV_MCP_ENABLED=false` degrades the Interpreter to the control-plane retrieval path and the trace renders "MCP UNAVAILABLE — degraded read path" instead of silently succeeding.

One honest caveat, carried forward from `specs/15_API_SPEC.md` §17.13: `agent_runs.tool_calls` is **self-reported by the agent runtime**. It is an accurate observability artifact and must not be presented as tamper-proof. The authoritative record of what the agent *could* access is the grant in §11.5; the authoritative record of what it *did* access would require CockroachDB audit logging or MCP server-side logging, neither of which is wired up in v1.

The canonical MCP call shape, for reference:

```sql
SELECT evidence_id, evidence_type, normalized_text, observed_at, source_authority
FROM agent_evidence_retrieval_v1
WHERE tenant_id = $1 AND user_id = $2 AND evidence_id = ANY($3)
ORDER BY observed_at DESC;
```

`$1` and `$2` are derived server-side from the agent's `agent_run_id`, never from the model. A model that emitted a different `user_id` would be emitting a value it was never given.

---

## 12. Environment variable manifest

Consumers: **CP** = App Runner control plane, **AG** = AgentCore agent runtime, **LW** = Lambda workers, **WEB** = Amplify Next.js build and runtime, **CI** = build pipeline and gate batteries.

Everything is loaded through one typed settings object (`provenance_contracts.settings.Settings`). Domain code never calls `os.environ`. `G0` asserts that a missing required variable produces a Pydantic `ValidationError` naming the variable at container start rather than a `None` at first request.

### 12.1 Core

| Name | Purpose | Example | Consumed by |
|---|---|---|---|
| `APP_ENV` | environment discriminator; gates fixture-mode banners | `prod` | CP, AG, LW, CI |
| `APP_BASE_URL` | public API origin the workers and agents call | `https://api.provenance.app` | CP, AG, LW |
| `WEB_BASE_URL` | allowed CORS origin and link base in notification email | `https://app.provenance.app` | CP |
| `AWS_REGION` | SDK region for every client | `us-east-1` | CP, AG, LW, CI |
| `BUILD_SHA` | surfaced as `git_sha` by `GET /v1/version`; string-equal to `git rev-parse HEAD` (G13.2) | `4f1c9a...` | CP, AG, WEB |
| `SCHEMA_REVISION` | Alembic head the image expects; reported by `GET /v1/version` | `0008` | CP |
| `LOG_LEVEL` | application log level | `INFO` | CP, AG, LW |
| `OTEL_SERVICE_NAME` | span service attribute | `provenance-control-plane` | CP, AG, LW |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | ADOT collector endpoint for CloudWatch export | `http://localhost:4317` | CP, AG |

### 12.2 Authentication

| Name | Purpose | Example | Consumed by |
|---|---|---|---|
| `COGNITO_USER_POOL_ID` | pool id; derives the issuer and JWKS URL | `us-east-1_A1b2C3d4E` | CP |
| `COGNITO_ISSUER` | expected `iss` claim | `https://cognito-idp.us-east-1.amazonaws.com/us-east-1_A1b2C3d4E` | CP |
| `COGNITO_JWKS_URL` | signing key set; cached 12 h | `https://cognito-idp.us-east-1.amazonaws.com/us-east-1_A1b2C3d4E/.well-known/jwks.json` | CP |
| `COGNITO_TOKEN_ENDPOINT` | client-credentials token endpoint | `https://provenance-auth.auth.us-east-1.amazoncognito.com/oauth2/token` | AG, LW |
| `COGNITO_WEB_CLIENT_ID` | accepted `client_id` on `/v1/**` | `1a2b3c4d5e6f7g8h9i0j` | CP |
| `COGNITO_AGENT_CLIENT_ID` | accepted `client_id` on `/internal/v1/**` for the agent | `2b3c4d5e6f7g8h9i0j1a` | CP, AG |
| `COGNITO_AGENT_CLIENT_SECRET_ARN` | agent client secret; delivered as a *value* through the App Runner secrets channel (§8.2) | `arn:aws:secretsmanager:...:provenance/cognito-AbCdEf` | CP, AG |
| `COGNITO_WORKER_CLIENT_ID` | accepted `client_id` on `/internal/v1/**` for workers; also the AgentCore authorizer's allowed client | `3c4d5e6f7g8h9i0j1a2b` | CP, LW |
| `COGNITO_WORKER_CLIENT_SECRET_ARN` | worker client secret | `arn:aws:secretsmanager:...:provenance/cognito-AbCdEf` | CP, LW |
| `COGNITO_JUDGE_GROUP` | group granting `judge_mode_enabled` | `provenance-judges` | CP |

### 12.3 Database

| Name | Purpose | Example | Consumed by |
|---|---|---|---|
| `COCKROACH_DATABASE_URL` | `pv_app_reader_writer` DSN; the app and read pools | `postgresql://pv_app_reader_writer:***@cluster-host:26257/provenance?sslmode=verify-full` | CP, `cognito_post_confirmation` |
| `COCKROACH_KERNEL_URL` | `pv_kernel_writer` DSN; the only canonical write pool | `postgresql://pv_kernel_writer:***@cluster-host:26257/provenance?sslmode=verify-full` | CP |
| `COCKROACH_MIGRATOR_URL` | `pv_migrator` DSN; DDL only | `postgresql://pv_migrator:***@cluster-host:26257/provenance?sslmode=verify-full` | CI |
| `PROVENANCE_TEST_DB_URL` | integration-test cluster | `postgresql://pv_migrator:***@test-host:26257/provenance_test?sslmode=verify-full` | CI |
| `COCKROACH_POOL_MIN` / `_MAX` | asyncpg pool bounds per role | `2` / `10` | CP |
| `COCKROACH_STATEMENT_TIMEOUT_MS` | per-connection `statement_timeout` | `15000` | CP |
| `PROVENANCE_KEEP_TEST_DBS` | leave a failed test database for post-mortem | `1` | CI |

### 12.4 Cryptographic material

| Name | Purpose | Example | Consumed by |
|---|---|---|---|
| `PROVENANCE_CAPABILITY_HMAC_KEY` | signs `X-Provenance-Capability-Proof` | base64, 32 bytes | CP, `ses_ingest` |
| `PROVENANCE_CAPABILITY_HMAC_KID` | key version stamped into the proof header (§8.7) | `k1` | CP, LW |
| `CURSOR_HMAC_KEY` | signs keyset pagination cursors | base64, 32 bytes | CP |
| `INGEST_ALIAS_HMAC_KEY` | derives `ingest_aliases.alias_hash` | base64, 32 bytes | CP, `ses_ingest`, `cognito_post_confirmation` |

### 12.5 Storage

| Name | Purpose | Example | Consumed by |
|---|---|---|---|
| `S3_ARTIFACT_BUCKET` | artifact bytes and parser output | `provenance-artifacts-us-east-1` | CP, LW |
| `S3_INBOUND_BUCKET` | SES staging writes | `provenance-inbound-us-east-1` | `ses_ingest` |
| `S3_KMS_KEY_ARN` | CMK for every artifact object | `arn:aws:kms:us-east-1:...:key/...` | CP, LW |
| `MAX_ARTIFACT_BYTES` | upload cap; mirrors §8.18 | `20971520` | CP, `ses_ingest` |
| `UPLOAD_URL_TTL_SECONDS` | pre-signed `PUT` lifetime | `900` | CP |
| `DOWNLOAD_URL_TTL_SECONDS` | pre-signed `GET` lifetime | `300` | CP |

### 12.6 Email

| Name | Purpose | Example | Consumed by |
|---|---|---|---|
| `SES_INGEST_DOMAIN` | inbound alias domain | `in.provenance.app` | CP, `ses_ingest` |
| `SES_FROM_ADDRESS` | outbound action sender; pinned by an IAM condition | `disputes@provenance.app` | CP |
| `SES_NOTIFICATION_FROM_ADDRESS` | user notification sender | `notifications@provenance.app` | `notification_dispatch` |
| `SES_CONFIGURATION_SET` | required on every send so bounce events fire | `provenance-outbound` | CP, `notification_dispatch` |
| `SES_DEMO_SINK_DOMAIN` | verified sandbox recipient | `demo-sink.provenance.app` | CP |
| `ACTION_RECIPIENT_MODE` | `DEMO_SINK` or `COUNTERPARTY`; never a silent rewrite | `DEMO_SINK` | CP |
| `PV_ACTION_EXECUTION_MODE` | kill switch; `ENABLED` or `DISABLED` (G-9 rollback) | `ENABLED` | CP |

### 12.7 Events and scheduling

| Name | Purpose | Example | Consumed by |
|---|---|---|---|
| `EVENTBRIDGE_BUS_NAME` | domain event bus | `provenance-domain-bus` | CP |
| `EVENTBRIDGE_SCHEDULER_GROUP` | one-time trigger schedules | `provenance-triggers` | `trigger_schedule_manager` |
| `SCHEDULER_TARGET_LAMBDA_ARN` | wakeup target | `arn:aws:lambda:us-east-1:...:function:provenance-trigger-wakeup` | `trigger_schedule_manager` |
| `SCHEDULER_ROLE_ARN` | role Scheduler assumes to invoke | `arn:aws:iam::...:role/provenance-scheduler-invoke-role` | `trigger_schedule_manager` |
| `SCHEDULER_DLQ_ARN` | schedule target DLQ | `arn:aws:sqs:us-east-1:...:provenance-scheduler-dlq` | `trigger_schedule_manager` |
| `SQS_DLQ_URL` | generic worker DLQ | `https://sqs.us-east-1.amazonaws.com/.../provenance-worker-dlq` | LW |
| `OUTBOX_SWEEP_BATCH_SIZE` | rows claimed per sweep | `50` | CP, `outbox_dispatch` |

### 12.8 Models, retrieval, agents

| Name | Purpose | Example | Consumed by |
|---|---|---|---|
| `BEDROCK_EXTRACTION_MODEL_ID` | Tier E | `anthropic.claude-haiku-4-5` | CP, AG |
| `BEDROCK_REASONING_MODEL_ID` | Tier R | `anthropic.claude-opus-5` | CP, AG |
| `BEDROCK_EMBEDDING_MODEL_ID` | frozen embedding model | `amazon.titan-embed-text-v2:0` | CP, AG, CI |
| `EMBEDDING_DIMENSIONS` | must equal the `VECTOR(n)` column | `1024` | CP, CI |
| `EMBEDDING_VERSION` | frozen; filters the ANN result set | `v1` | CP, CI |
| `EMBEDDING_NORMALIZATION` | `NONE` or `L2_UNIT`; set by the §11.4 probe outcome | `NONE` | CP, CI |
| `EMBEDDING_CACHE_TABLE` | cache keyed on normalized-text hash + version (§13.3) | `embedding_cache` | CP, CI |
| `VECTOR_SEARCH_BEAM_SIZE` | recorded in the trace; left at the cluster default for v1 | `32` | CP |
| `AGENTCORE_RUNTIME_ARN` | invocation target | `arn:aws:bedrock-agentcore:us-east-1:...:runtime/provenance_agents-XXXX` | CP |
| `AGENTCORE_QUALIFIER` | runtime version alias | `DEFAULT` | CP |
| `MCP_SERVER_URL` | Managed MCP Server endpoint | `https://mcp.<cluster>.cockroachlabs.cloud` | AG |
| `MCP_AUTH_SECRET_ARN` | `pv_agent_reader` credential for MCP; delivered as a value | `arn:aws:secretsmanager:...:provenance/mcp-XyZ` | CP, AG |
| `PV_MCP_ENABLED` | degradation switch; `false` forces the control-plane read path | `true` | AG |
| `PV_AGENT_MODE` | `LIVE` or `FIXTURE`; `FIXTURE` raises a non-dismissible banner | `LIVE` | CP, AG, WEB |

### 12.9 Frontend

| Name | Purpose | Example | Consumed by |
|---|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | API origin the browser calls directly | `https://api.provenance.app` | WEB |
| `NEXT_PUBLIC_AWS_REGION` | region for the Cognito client | `us-east-1` | WEB |
| `NEXT_PUBLIC_COGNITO_USER_POOL_ID` | pool id | `us-east-1_A1b2C3d4E` | WEB |
| `NEXT_PUBLIC_COGNITO_WEB_CLIENT_ID` | public client id; no secret exists | `1a2b3c4d5e6f7g8h9i0j` | WEB |
| `NEXT_PUBLIC_COGNITO_DOMAIN` | hosted UI host | `provenance-auth.auth.us-east-1.amazoncognito.com` | WEB |
| `NEXT_PUBLIC_COGNITO_SCOPES` | requested scopes | `openid email profile provenance.memory/read` | WEB |
| `NEXT_PUBLIC_BUILD_SHA` | rendered in the footer; matched against `GET /v1/version` | `4f1c9a...` | WEB |

### 12.10 CI and gates

`PV_REPO_ROOT`, `PV_GIT_SHA`, `PV_REGION`, `PV_API`, `PV_WEB`, `PV_GATE_LOG`, `PV_DB_MIGRATOR`, `PV_DB_APP`, `PV_DB_AGENT`, `PV_SABOTAGE`, `PV_FORBID_MOCKS`, `PV_PREVIOUS_IMAGE`, `PV_DB_COMPAT`, `PV_APPRUNNER_ARN`, `PV_TEARDOWN` — all defined by `quality/23_PHASE_GATES.md` §2.2 and §14. `PV_TEARDOWN` is set by `ops/teardown.sh` and by nothing else.

### 12.11 The rule that makes this list enforceable

```python
# packages/python/provenance_contracts/settings.py
class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid", frozen=True, case_sensitive=True)
    # ... every field above, required unless it has a default ...

    @model_validator(mode="after")
    def _issuer_matches_pool(self) -> "Settings":
        expected = f"https://cognito-idp.{self.aws_region}.amazonaws.com/{self.cognito_user_pool_id}"
        if str(self.cognito_issuer).rstrip("/") != expected:
            raise ValueError(f"COGNITO_ISSUER must be {expected}")
        if not str(self.cognito_jwks_url).startswith(expected):
            raise ValueError(f"COGNITO_JWKS_URL must begin with {expected}")
        return self

    @model_validator(mode="after")
    def _embedding_contract_frozen(self) -> "Settings":
        if self.embedding_dimensions != 1024:
            raise ValueError("EMBEDDING_DIMENSIONS is frozen at 1024")
        if self.bedrock_embedding_model_id != "amazon.titan-embed-text-v2:0":
            raise ValueError("BEDROCK_EMBEDDING_MODEL_ID is frozen")
        if self.embedding_version != "v1":
            raise ValueError("EMBEDDING_VERSION is frozen at v1")
        return self
```

`extra="forbid"` on the settings object means an environment variable that is set but not declared is a startup failure, so a stale variable left behind by a refactor cannot silently do nothing.

---

## 13. Cost controls

Demo scale is genuinely small. The risk is not steady-state cost; it is a loop. A retry storm against Opus 5, an embedding regeneration that ignores its cache, or an autoscaled App Runner behind an unbounded upload endpoint are the three ways this account produces a surprising bill.

### 13.1 AWS Budgets and alarms

```typescript
// infra/cdk/lib/foundation-stack.ts
import { CfnBudget } from 'aws-cdk-lib/aws-budgets';
import { Topic } from 'aws-cdk-lib/aws-sns';

const alerts = new Topic(this, 'BudgetAlerts', { topicName: 'provenance-budget-alerts' });

const notify = (threshold: number, type: 'ACTUAL' | 'FORECASTED') => ({
  notification: {
    notificationType: type,
    comparisonOperator: 'GREATER_THAN',
    threshold,
    thresholdType: 'PERCENTAGE',
  },
  subscribers: [{ subscriptionType: 'SNS', address: alerts.topicArn },
                { subscriptionType: 'EMAIL', address: process.env.PV_OWNER_EMAIL! }],
});

new CfnBudget(this, 'MonthlyBudget', {
  budget: {
    budgetName: 'provenance-monthly',
    budgetType: 'COST',
    timeUnit: 'MONTHLY',
    budgetLimit: { amount: 150, unit: 'USD' },
    costFilters: { TagKeyValue: ['user:Project$Provenance'] },
  },
  notificationsWithSubscribers: [
    notify(50, 'ACTUAL'), notify(80, 'ACTUAL'), notify(100, 'ACTUAL'),
    notify(100, 'FORECASTED'),
  ],
});

// Bedrock is the only line item that can move fast. It gets its own, tighter budget.
new CfnBudget(this, 'BedrockBudget', {
  budget: {
    budgetName: 'provenance-bedrock-monthly',
    budgetType: 'COST',
    timeUnit: 'MONTHLY',
    budgetLimit: { amount: 60, unit: 'USD' },
    costFilters: { Service: ['Amazon Bedrock'] },
  },
  notificationsWithSubscribers: [notify(50, 'ACTUAL'), notify(80, 'ACTUAL'), notify(100, 'FORECASTED')],
});
```

Budgets alert; they do not stop anything. The two controls that actually stop spend are `AWS/Billing EstimatedCharges` paired with a same-day check, and the hard per-run budgets in §13.2.

```typescript
// EstimatedCharges only publishes in us-east-1, which is where this stack lives anyway.
new Alarm(this, 'EstimatedChargesAlarm', {
  alarmName: 'provenance-estimated-charges',
  metric: new Metric({
    namespace: 'AWS/Billing', metricName: 'EstimatedCharges',
    dimensionsMap: { Currency: 'USD' }, statistic: 'Maximum', period: Duration.hours(6),
  }),
  threshold: 120, evaluationPeriods: 1,
  comparisonOperator: ComparisonOperator.GREATER_THAN_THRESHOLD,
  treatMissingData: TreatMissingData.NOT_BREACHING,
}).addAlarmAction(new SnsAction(alerts));
```

Alarms `G13.7` requires, all created in `PvObservabilityStack` and all expected in `OK` rather than `INSUFFICIENT_DATA` at the gate: `provenance-outbox-pending-age`, `provenance-dlq-depth`, `provenance-kernel-retry-rate`, `provenance-action-abort-rate`. The metric definitions come from `specs/15_API_SPEC.md` §13.8.

### 13.2 Per-artifact model-call budget

The cost surface is model calls per ingested artifact, and it is bounded in the one place a runaway graph cannot bypass: the `agent_runs` capability row, checked by the tool wrapper on every call.

| Budget | Limit | Enforced at |
|---|---|---|
| Model calls per artifact | 8 | `agent_runs.limits.max_model_calls` |
| Tier R escalations per artifact | 1 unless explicitly retried | `route_resolution_need` |
| Schema repair attempts per node | 1 | `validate_extraction_schema` |
| Tool calls per run | 50 | `agent_runs.limits.max_tool_calls` |
| Embedding calls per artifact | 40 | embedding cache (§13.3) |
| Concurrent agent runs per user | 3 | `agent_runs_concurrent` quota |
| Counterfactual reruns | 10 per 60 min per user | `counterfactual` rate bucket |

Exceeding a budget ends the run with `status = 'FAILED'`. Evidence stays admitted, canonical state is unchanged, and the artifact is retryable — a budget stop is never a corruption event.

```python
# agents/runtime/tools/budget.py
from dataclasses import dataclass


@dataclass
class RunBudget:
    max_model_calls: int
    max_tool_calls: int
    max_repair_attempts: int
    model_calls: int = 0
    tool_calls: int = 0

    def charge_model_call(self, model_id: str) -> None:
        self.model_calls += 1
        if self.model_calls > self.max_model_calls:
            raise BudgetExceeded("MAX_MODEL_CALLS", model_id=model_id,
                                 used=self.model_calls, limit=self.max_model_calls)

    def charge_tool_call(self, tool: str) -> None:
        self.tool_calls += 1
        if self.tool_calls > self.max_tool_calls:
            raise BudgetExceeded("MAX_TOOL_CALLS", tool=tool,
                                 used=self.tool_calls, limit=self.max_tool_calls)
```

The counter lives on the run, not in the process, so a graph that retries a node or restarts from a checkpoint cannot reset its own budget.

### 13.3 Embedding cache

The seed alone needs roughly 18,000 embeddings. Regenerating them on every environment rebuild is the single largest avoidable cost and, more importantly, the largest avoidable *delay*.

The cache key is `(sha256(normalized_text), embedding_version)` — never the artifact id, never the evidence id, because identical normalized text must produce one vector regardless of which artifact it came from.

```python
# packages/python/provenance_db/embeddings/cache.py
from __future__ import annotations
import hashlib
import struct


def embedding_cache_key(normalized_text: str, embedding_version: str) -> tuple[bytes, str]:
    """(sha256 of the normalized text, embedding version). Both halves are required:
    the same text under a new embedding version is a DIFFERENT vector space, and
    serving a v1 vector to a v2 index would silently corrupt every ranking."""
    digest = hashlib.sha256(normalized_text.encode("utf-8")).digest()
    return digest, embedding_version


async def get_or_create_embedding(conn, bedrock, normalized_text: str, version: str) -> list[float]:
    key, ver = embedding_cache_key(normalized_text, version)

    row = await conn.fetchrow(
        "SELECT embedding FROM embedding_cache WHERE text_sha256 = $1 AND embedding_version = $2",
        key, ver)
    if row is not None:
        return row["embedding"]

    vector = await bedrock.embed(normalized_text, model_id="amazon.titan-embed-text-v2:0",
                                 dimensions=1024, normalize=(EMBEDDING_NORMALIZATION == "L2_UNIT"))

    await conn.execute(
        """INSERT INTO embedding_cache (text_sha256, embedding_version, embedding, created_at)
           VALUES ($1, $2, $3, now()) ON CONFLICT DO NOTHING""",
        key, ver, vector)
    return vector
```

Two layers, both required:

1. **The database cache above**, shared by the control plane and by every seed run against the same cluster.
2. **The on-disk seed cache** at `scripts/seed/.embedding-cache/{sha256}.f32`, which survives a cluster rebuild and lets an interrupted seed resume. `specs/10_DATABASE_DDL.md` §17 requires it to batch, cache, and resume; a cold seed restarted three times otherwise eats an afternoon.

`idx_evidence_text_hash` on `evidence_items` exists for exactly this: never regenerate an embedding whose `normalized_text_sha256` is already present.

The cost itself is small — roughly 18,000 rows at about 40 tokens each is under a million tokens, single-digit US cents. The cache is about time and reproducibility, and saying otherwise would overstate the saving.

### 13.4 Demo-scale account limits

| Control | Value | Where |
|---|---|---|
| App Runner instances | min 1, max 2 | `provenance-apprunner-scaling` |
| App Runner size | 1 vCPU, 2 GB | instance configuration |
| Lambda reserved concurrency | 5 / 3 / 2 / 10 | §7.6 |
| Artifacts per user per day | 200 | `artifact_daily` quota |
| Artifact bytes per user per day | 500 MiB | `artifact_bytes_daily` quota |
| Upload intents per user per minute | 20 | `upload_intent` bucket |
| Counterfactual runs | 10 per hour per user | `counterfactual` bucket |
| Max artifact size | 20 MiB | `MAX_ARTIFACT_BYTES` |
| Cognito users | the hero user, two isolation-test users, judge accounts | seeded; self-signup enabled but the pool is not advertised |
| CockroachDB spend limit | USD 25/month | `ccloud cluster update --spend-limit` |
| S3 lifecycle | `normalized/` expires at 90 days; incomplete MPUs aborted at 1 day | §4.3 |
| CloudWatch retention | 30 days on every log group | `PvFoundationStack` |
| Textract | invoked only for scanned or image-heavy documents; text PDFs use deterministic extraction first | `implementation/01_SYSTEM_ARCHITECTURE_DETAILED.md` §6.2 |

The rate limits are in-process per App Runner instance, so with two instances a user gets double the intended limit. `specs/15_API_SPEC.md` §17.1 calls this a real gap rather than a deferred nicety, and it is repeated here because the two buckets that map most directly to spend — `upload_intent` and `counterfactual` — are exactly the ones the gap weakens. Pinning `maxSize: 2` bounds the error at 2x rather than unbounded.

Two further behavioural controls that cost nothing to implement and matter more than any of the above: **agents do not run on page refresh** (the dashboard reads committed state; nothing re-invokes a graph), and **every workflow outcome is persisted** so a re-render never re-derives anything.

---

## 14. Teardown

Goal: after this runs, nothing in the account or the CockroachDB Cloud organization bills. Run it after the submission is accepted and the video is recorded, not before.

### 14.1 Order

Reverse of §2.4, with the resources that block a stack delete removed first.

```bash
# ops/teardown.sh
set -euo pipefail
export AWS_REGION=us-east-1
export PV_TEARDOWN=1                 # flips STATEFUL_REMOVAL to DESTROY (§2.3)
cd infra/cdk

# ---- 0. Stop anything that could recreate a resource mid-teardown.
aws apprunner pause-service --service-arn "$PV_APPRUNNER_ARN" || true
aws ses set-active-receipt-rule-set --region us-east-1 || true   # no name = deactivate all

# ---- 1. Runtime-created resources that CDK does not own.
#         One-time trigger schedules are created by trigger_schedule_manager; a
#         non-empty schedule group cannot be deleted.
for g in provenance-triggers provenance-system; do
  for s in $(aws scheduler list-schedules --group-name "$g" --query 'Schedules[].Name' --output text); do
    aws scheduler delete-schedule --name "$s" --group-name "$g"
  done
done

# ---- 2. Redeploy the stateful stacks WITH destroy policies so the delete can proceed.
#         Without this pass, RETAIN leaves orphaned buckets, keys, and the user pool.
npx cdk deploy PvFoundationStack PvDataStack PvIdentityStack --require-approval never

# ---- 3. Delete in reverse dependency order.
npx cdk destroy PvObservabilityStack --force
npx cdk destroy PvWebStack           --force
npx cdk destroy PvEmailStack         --force
npx cdk destroy PvAgentStack         --force     # calls DeleteAgentRuntime
npx cdk destroy PvApiStack           --force
npx cdk destroy PvComputeStack       --force
npx cdk destroy PvMessagingStack     --force
npx cdk destroy PvDataStack          --force     # autoDeleteObjects empties both buckets
npx cdk destroy PvIdentityStack      --force
npx cdk destroy PvFoundationStack    --force     # KMS key enters a 7-day pending window
```

### 14.2 What CDK will not delete for you

```bash
# Secrets: default recovery is 30 days and a recoverable secret still occupies the name.
for s in provenance/db provenance/cognito provenance/crypto provenance/mcp; do
  aws secretsmanager delete-secret --secret-id "$s" --force-delete-without-recovery
done

# Log groups created by Lambda's implicit behaviour rather than by the stack.
for lg in $(aws logs describe-log-groups --log-group-name-prefix /aws/lambda/provenance- \
            --query 'logGroups[].logGroupName' --output text); do
  aws logs delete-log-group --log-group-name "$lg"
done
aws logs delete-log-group --log-group-name /provenance/control-plane || true
aws logs delete-log-group --log-group-name /provenance/domain-events || true

# SES identities and the rule set. Identities cost nothing but leave DNS expectations.
aws ses delete-receipt-rule --rule-set-name provenance-inbound-rules --rule-name provenance-ingest-rule || true
aws ses delete-receipt-rule-set --rule-set-name provenance-inbound-rules || true
for i in provenance.app in.provenance.app demo-sink.provenance.app; do
  aws sesv2 delete-email-identity --email-identity "$i" || true
done
aws sesv2 delete-configuration-set --configuration-set-name provenance-outbound || true

# Budgets are account-level and survive stack deletion in some configurations.
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
for b in provenance-monthly provenance-bedrock-monthly; do
  aws budgets delete-budget --account-id "$ACCOUNT" --budget-name "$b" || true
done

# X-Ray sampling rules, if any were added beyond the default.
aws xray get-sampling-rules --query 'SamplingRuleRecords[?SamplingRule.RuleName!=`Default`].SamplingRule.RuleName' --output text
```

### 14.3 The CockroachDB cluster

```bash
ccloud cluster delete provenance-prod --confirmation provenance-prod
ccloud cluster list          # → provenance-prod absent
ccloud info                  # → confirm no remaining spend against the organization
```

Deleting the cluster destroys the seeded corpus, the hero data, and every gate log's referenced row. Export anything the submission narrative depends on **before** this line:

```bash
ccloud cluster sql provenance-prod --user pv_migrator --format=csv \
  -e "SELECT * FROM kernel_decisions ORDER BY created_at;" > ops/final/kernel_decisions.csv
ccloud cluster sql provenance-prod --user pv_migrator --format=csv \
  -e "SELECT * FROM state_transitions ORDER BY created_at;" > ops/final/state_transitions.csv
```

### 14.4 DNS

Remove, at the registrar: the `in.provenance.app` MX record, the three DKIM CNAMEs for each verified identity, the SPF TXT, the `_dmarc` TXT, the App Runner `api.provenance.app` validation CNAMEs, and the Amplify `app.provenance.app` records. Leaving an MX record pointing at a deleted SES receiving endpoint causes silent mail loss for anyone who still has the alias.

### 14.5 Verification

```bash
# Nothing tagged Project=Provenance may remain.
aws resourcegroupstaggingapi get-resources \
  --tag-filters Key=Project,Values=Provenance \
  --query 'ResourceTagMappingList[].ResourceARN' --output text
#   → empty

# No stack remains.
aws cloudformation list-stacks \
  --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE ROLLBACK_COMPLETE \
  --query 'StackSummaries[?starts_with(StackName, `Pv`)].StackName' --output text
#   → empty

# No bucket, queue, function, or pool remains.
aws s3api list-buckets --query 'Buckets[?starts_with(Name,`provenance-`)].Name' --output text
aws sqs list-queues --queue-name-prefix provenance- --query 'QueueUrls' --output text
aws lambda list-functions --query 'Functions[?starts_with(FunctionName,`provenance-`)].FunctionName' --output text
aws cognito-idp list-user-pools --max-results 60 --query 'UserPools[?Name==`provenance-users`].Id' --output text
aws bedrock-agentcore-control list-agent-runtimes --query 'agentRuntimes[].agentRuntimeName' --output text
#   → all empty

# The KMS key is the one thing that lingers: it sits in PendingDeletion for 7 days.
aws kms list-keys --query 'Keys[].KeyId' --output text | tr '\t' '\n' | while read -r k; do
  aws kms describe-key --key-id "$k" --query 'KeyMetadata.[KeyId,KeyState,Description]' --output text
done | grep -i provenance || true
#   → PendingDeletion, or absent. A key in PendingDeletion does not bill.

# Final: the bill itself, checked the day after and again a week later.
aws ce get-cost-and-usage --time-period Start=$(date -d '-2 days' +%F),End=$(date +%F) \
  --granularity DAILY --metrics UnblendedCost \
  --filter '{"Tags":{"Key":"Project","Values":["Provenance"]}}' \
  --query 'ResultsByTime[].Total.UnblendedCost.Amount' --output text
#   → 0 (or a trailing partial-day amount that goes to 0 the next day)
```

A teardown is not complete when the commands have run. It is complete when the cost query returns zero on a day *after* the teardown, and that check is a calendar entry, not an assumption.

---

## 15. Risks and open questions

### 15.1 Two bucket-name spellings exist in the design pack

`00_PRODUCT.md` §2.3 writes `provenance-artifacts-use1` inside a worked example; `specs/15_API_SPEC.md` §8.18 and §9.1 write `provenance-artifacts-us-east-1` inside a pre-signed URL and a request body. This document deploys the API specification's spelling, because that is the one an HTTP contract commits to and the one a client parses. **Open question:** whether `00_PRODUCT.md` §2.3 should be corrected under the change-control rule in `README.md`. It is illustrative prose rather than a contract, so this is low-severity, but two spellings in a design pack is exactly the drift the canon rules exist to prevent.

### 15.2 The agent client's `ingest/write` scope is specified twice, differently

`specs/15_API_SPEC.md` §2.1 lists three scopes for `provenance-agent-runtime`; §9.0's footnote grants it a fourth, `provenance.ingest/write`, narrowed by `CLIENT_CAPABILITY_MATRIX` to the `AGENT_RUN` capability kind. This deployment follows §9.0, because §9.4 is unreachable without it and the more specific statement should win. **Assumption:** the §2.1 table is the stale one. If the intent was actually the reverse — that §9.4 should require a different mechanism — then the Cognito client configuration in §3.3 is wrong and must change before Phase 8. This is the single most consequential naming ambiguity in the pack, because it is a permission.

### 15.3 One required endpoint does not exist in the API specification

`POST /internal/v1/artifacts/{artifact_id}/parser-callback` (§7.3) is the only path by which a Textract completion can set `parser_status = 'PARSED'` without handing a Lambda a SQL credential. Without it, every scanned document stalls at `409 VALIDATION_FAILED` on `GET /internal/v1/agent-runs/{id}/artifact-content`. It uses only existing concepts (capability kind `ARTIFACT`, scope `provenance.ingest/write`, idempotency scope `internal.parser.callback`) and must be added to §9 of `specs/15_API_SPEC.md` before Phase 8. Until it is, `spec_lint` will fail on a documented path that is absent from `openapi.json`, which is the correct behaviour: this is a real gap, surfaced rather than papered over.

### 15.4 Append-only S3 is a policy statement, not Object Lock

§4.4 denies `DeleteObject` to everything except a teardown role. That is defeatable by anyone who can change the bucket policy, which is anyone with administrative IAM in the account. Object Lock in compliance mode would be genuinely immutable and is deliberately not used, because it cannot be enabled after bucket creation, cannot be disabled, and would make §14 require waiting out a retention period. **Decision:** accept the weaker control for the hackathon; state plainly in any Product Readiness discussion that S3-level immutability is policy-enforced, while *database*-level append-only is enforced by grants plus `ON DELETE RESTRICT` and is the stronger of the two.

### 15.5 `tlsPolicy: Optional` on inbound SES

Requiring STARTTLS would bounce mail from any sending MTA that will not negotiate it, and a bounced hero artifact during a recorded demo is unrecoverable. `Optional` accepts plaintext SMTP delivery. The compensating position is that SPF, DKIM, and DMARC verdicts are captured regardless and feed the source-authority band, and that inbound content is treated as hostile at every layer downstream. **This is a demo concession and should be `Require` in production.**

### 15.6 `ses_ingest` holds the capability-proof HMAC key

Every other capability proof is minted by the control plane before it dispatches a workload. Inbound mail has no preceding control-plane request, so `provenance-ses-ingest` computes its own proof over `("INGEST_ALIAS", alias_hash, expires_at)`, which requires read access to `provenance/crypto`. That widens the blast radius of a key `specs/15_API_SPEC.md` §17.2 already identifies as a single point of compromise. Two mitigations, neither complete: the proof is defence in depth rather than the primary control (the `ingest_aliases` row is), and the key is versioned with a `kid` so rotation does not require a coordinated deploy. **Open question:** whether the ingest path should use a separate, narrower HMAC key so that compromising the mail worker does not yield the key that protects agent runs and action intents. The answer is probably yes, and it is a one-line addition to `provenance/crypto`.

### 15.7 AgentCore Runtime is the most version-sensitive surface here

The `create-agent-runtime` request shape, the `customJWTAuthorizer` field names, the invocation URL form, and the session-id header are all newer than most of this stack and are the parts of this document most likely to be wrong in detail. `infra/agentcore/` isolates the exact request JSON so a shape change is a one-file edit, and the Phase 0 checklist diffs `--generate-cli-skeleton` against it. **Assumption:** AgentCore Runtime supports a Cognito-backed `customJWTAuthorizer` with `allowedClients` matching the `client_id` claim of a client-credentials token. If it turns out to require an `aud` claim, the fallback is SigV4 IAM inbound auth with `bedrock-agentcore:InvokeAgentRuntime` on the App Runner instance role and the worker role, which is already written in §8.3 and costs the JWT narrative but nothing functional.

### 15.8 Using `provenance-workers` as the AgentCore inbound client is a judgement call

The frozen design has three app clients and none of them is obviously "the thing that invokes the agent runtime". §9.3 uses `provenance-workers` because both invokers (the control plane and `advocate_dispatch`) are trusted control-plane-side workloads. The alternative — a fourth client — would contradict `specs/15_API_SPEC.md` §2.1. The residual weakness is that a leaked worker secret now also opens the runtime's front door, which is bounded by the fact that an invocation can only act within a server-written `agent_run_id`. **If a reviewer prefers a dedicated invoker identity, that is a change to §2.1 of the API specification, not to this document.**

### 15.9 The 30-second outbox sweep is really a 1-minute schedule with two passes

EventBridge Scheduler's minimum `rate()` granularity is one minute, so `specs/15_API_SPEC.md` §13.6's "every 30 seconds" cannot be expressed as a schedule. §6.4 fires once a minute and sweeps twice, 30 seconds apart, inside one invocation. The behaviour matches the specification; the mechanism does not match a naive reading of it, and a Lambda that dies between passes leaves a 60-second gap that the next tick closes. The immediate best-effort sweep after each Kernel commit is what actually makes the demo feel instant; the schedule is the guarantee, not the latency path.

### 15.10 The pre-signed `PUT` size cap may be advisory

§4.6 signs `Content-Length` in the hope that S3 rejects a mismatched body, and the probe in that section is the only way to know for certain on the deployed configuration. If it does not, the 20 MiB cap is enforced solely by `HeadObject` at `/complete` plus the 24-hour sweeper, which means a determined client can *store* oversized objects that are never admitted. The cost exposure is bounded by the `artifact_bytes_daily` quota of 500 MiB per user per day, which is checked at upload-intent time, before the URL is issued. **This must be stated accurately rather than described as a hard cap.**

### 15.11 In-process rate limiting scales wrongly, and it is a cost control here

Repeating `specs/15_API_SPEC.md` §17.1 because it lands differently in an infrastructure document: with `maxSize: 2`, every per-user limit is effectively doubled, and the two buckets that matter most for spend (`upload_intent`, `counterfactual`) are the ones affected. The limits are cost guards, not security controls. The production fix is a `rate_counters` table in CockroachDB (one write per request) or WAF rate rules in front of App Runner. Neither is deployed.

### 15.12 Nothing here has been run

No stack has been synthesized, no image built, no cluster created, no probe executed. Every command, policy, and construct in this document is written to be run and has not been. The first `cdk synth` will surface type errors in the snippets; the first `cdk deploy` will surface at least one IAM permission that is too narrow, because least-privilege policies written from a specification always are. Both are expected, and `quality/23_PHASE_GATES.md` §3's evidence-before-assertion rule applies to this document exactly as it applies to code: no line above may be reported as working until its output is pasted into a gate log.
