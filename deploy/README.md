# Deploying Benefitline

One AgentCore runtime holds the whole agent; one Lambda Function URL is the only public
address. That URL serves `web/index.html` on GET and forwards the JSON API on POST, so a
judge opens one link on a phone and nothing else has to exist.

## Before the first run

- `.env` in the project root holds `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
  `AWS_REGION=us-east-1`, `MODEL_PRIMARY`, `MODEL_FALLBACK`. It is never committed and
  the script never prints it.
- Bedrock model access for `us.anthropic.claude-sonnet-4-6` and
  `us.anthropic.claude-haiku-4-5-20251001-v1:0` must be enabled in the Bedrock console
  in us-east-1. That is an account-owner step and it is what blocks a first run.
- The deploy user needs these AWS managed policies:
  `AmazonDynamoDBFullAccess`, `AWSLambda_FullAccess`, `AWSCodeBuildAdminAccess`,
  `AWSCloudFormationFullAccess`, `AmazonS3FullAccess`,
  `AmazonEC2ContainerRegistryFullAccess`, `IAMFullAccess`, `BedrockAgentCoreFullAccess`.
  `deploy.py` checks all eight before it creates anything and writes the missing ones,
  with the console step, to `_runs/2026-09-13_phase4_deploy/BLOCKER.md`.

## The commands, in order

```
python -m pytest evals/test_service_units.py -q          # the API surface, no model
python src/app.py                                        # local runtime on :8080
curl -s http://127.0.0.1:8080/ping
curl -s -X POST http://127.0.0.1:8080/invocations \
  -H "Content-Type: application/json" \
  -d '{"action":"gallery","session_id":"<36 characters or more>"}'
python deploy/deploy.py                                  # the whole cloud path
```

`deploy.py` is idempotent: run it again after any code change and it updates the same
runtime, the same table, and the same Function URL. It does, in order:

1. checks permissions (nothing is created when one is missing);
2. `store.DynamoStore.ensure_table()` for the `benefitline-cases` table;
3. `agentcore create` / `agentcore add agent` on the first run, then `agentcore deploy`,
   which builds for ARM64 through CodeBuild and returns the runtime ARN;
4. sets the runtime environment (`BENEFITLINE_TABLE`, `MODEL_PRIMARY`, `MODEL_FALLBACK`,
   `BYPASS_TOOL_CONSENT=true`, `AWS_REGION=us-east-1`) and gives the runtime role
   `bedrock:InvokeModel*` on the two model ids plus the case table;
5. creates or updates `benefitline-proxy` (Python 3.13, 30 s, Function URL, auth NONE,
   CORS) with `AGENT_ARN` in its environment and
   `bedrock-agentcore:InvokeAgentRuntime` on that one runtime in its role;
6. prints the URL and writes `_runs/2026-09-13_phase4_deploy/DEPLOY.json`.

## What is proven and what is not

Proven offline: the permission preflight (it ran and refused), the proxy's routing and
`window.BENEFITLINE_API` injection (`_runs/2026-09-13_phase4_deploy/check_proxy.py`), and
the `UpdateAgentRuntime` / `CreateAgentRuntime` request shapes, read from the local
botocore model (`environmentVariables`, `networkConfiguration` and `protocolConfiguration`
are members; `agentRuntimeId`, `agentRuntimeArtifact`, `roleArn` are required, which is why
step 4 reads the runtime first and sends them back).

Unproven until the deploy user has the four missing policies: the `agentcore` CLI flags in
step 3 (`add agent --type byo --code-location . --entrypoint src/app.py --build Container`).
If those flags are wrong the script stops at that subprocess with the CLI's own usage text
and nothing after step 2 is created; fix the flag and re-run.

## Checking a deploy

```
curl -s "<function url>" | head -5                       # the page
curl -s -X POST "<function url>" -H "Content-Type: application/json" \
  -d '{"action":"gallery","session_id":"<36 characters or more>"}'
```

A `session_id` shorter than 33 characters is refused on purpose: AgentCore rejects a
shorter `runtimeSessionId`, so the proxy catches it before the call.

## Rolling back

```
aws lambda delete-function-url-config --function-name benefitline-proxy
aws lambda delete-function --function-name benefitline-proxy
aws iam delete-role-policy --role-name benefitline-proxy-role --policy-name benefitline-invoke-runtime
aws iam detach-role-policy --role-name benefitline-proxy-role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam delete-role --role-name benefitline-proxy-role
agentcore destroy                                        # or: aws bedrock-agentcore-control delete-agent-runtime --agent-runtime-id <id>
aws dynamodb delete-table --table-name benefitline-cases
```

Deleting the table deletes every demo case; the gallery itself lives in the repo, so a
later deploy starts clean.
