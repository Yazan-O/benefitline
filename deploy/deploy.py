"""One command that puts Benefitline online, and can be run again without harm.

    python deploy/deploy.py

Order of work:

1. Check the caller's permissions. Anything missing is written to
   `_runs/2026-09-13_phase4_deploy/BLOCKER.md` with the console step that fixes it,
   and nothing is created.
2. Create the DynamoDB case table (`store.DynamoStore.ensure_table`).
3. Build and deploy the AgentCore runtime with the `agentcore` CLI, then put the
   runtime's environment variables and its execution-role permissions in place.
4. Create or update the Lambda proxy, give it a Function URL with no auth, and print
   the URL. The same URL serves `web/index.html` and the JSON API.
5. Write `_runs/2026-09-13_phase4_deploy/DEPLOY.json`.

Credentials come from `.env` in the project root and are never printed.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

RUN_DIR = ROOT / "_runs" / "2026-09-13_phase4_deploy"
REGION = "us-east-1"
TABLE = "benefitline-cases"
RUNTIME_NAME = "benefitline"
LAMBDA_NAME = "benefitline-proxy"
# One request is a runtime cold start plus a Sonnet turn; 30 s cut that off mid-answer.
# The proxy client read timeout (handler.py) sits just under this so the SDK, not
# Lambda, is what gives up, and the browser gets JSON instead of a Lambda timeout.
PROXY_TIMEOUT = 120
PROJECT_NAME = "benefitlineruntime"   # `agentcore create` makes a directory of this name
PROJECT_DIR = ROOT / PROJECT_NAME
LAMBDA_ROLE = "benefitline-proxy-role"
RUNTIME_POLICY = "benefitline-runtime-access"
MODEL_PRIMARY = "us.anthropic.claude-sonnet-4-6"
MODEL_PRIMARY_ALT = "global.anthropic.claude-sonnet-4-6"  # same model, other profile (daily quota is per profile)
MODEL_FALLBACK = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
CENTRAL = ZoneInfo("America/Chicago")

# Every permission this script needs, with the AWS managed policy that grants it.
REQUIRED = [
    ("dynamodb", "list_tables", {}, "AmazonDynamoDBFullAccess"),
    ("lambda", "list_functions", {"MaxItems": 1}, "AWSLambda_FullAccess"),
    ("codebuild", "list_projects", {}, "AWSCodeBuildAdminAccess"),
    ("cloudformation", "list_stacks", {}, "AWSCloudFormationFullAccess"),
    ("s3", "list_buckets", {}, "AmazonS3FullAccess"),
    ("ecr", "get_authorization_token", {}, "AmazonEC2ContainerRegistryFullAccess"),
    ("iam", "list_roles", {"MaxItems": 1}, "IAMFullAccess"),
    ("bedrock-agentcore-control", "list_agent_runtimes", {"maxResults": 1},
     "BedrockAgentCoreFullAccess"),
]


def now() -> str:
    return datetime.now(CENTRAL).strftime("%Y-%m-%d %H:%M %Z")


def say(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def load_env() -> None:
    """Read `.env` if it is there. Values are used, never printed."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env = ROOT / ".env"
    if env.exists():
        load_dotenv(env)


# --- 1. permissions ---------------------------------------------------------


def preflight() -> list[tuple[str, str]]:
    """Returns [(api call, managed policy)] for every permission the caller lacks."""
    missing: list[tuple[str, str]] = []
    for service, call, kwargs, policy in REQUIRED:
        client = boto3.client(service, region_name=REGION)
        try:
            getattr(client, call)(**kwargs)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("AccessDenied", "AccessDeniedException",
                        "UnauthorizedOperation", "AuthorizationError"):
                missing.append((f"{service}:{call}", policy))
            # any other error (a throttle, an empty account) is not a permission problem
    return missing


def write_blocker(missing: list[tuple[str, str]], identity: dict) -> Path:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    path = RUN_DIR / "BLOCKER.md"
    user = identity.get("Arn", "the deploy user")
    policies = sorted({policy for _, policy in missing})
    lines = [
        "# Blocked: the deploy user cannot create the resources",
        "",
        f"Checked {now()} as `{user}`.",
        "",
        "Denied calls:",
        "",
    ]
    lines += [f"- `{call}` (granted by `{policy}`)" for call, policy in missing]
    lines += [
        "",
        "## The console step, exactly",
        "",
        "1. Sign in to the AWS console as the account owner of 280710007724.",
        "2. Open IAM, then Users, then the user this script runs as "
        f"(`{user.rsplit('/', 1)[-1]}`).",
        "3. Add permissions, then Attach policies directly.",
        "4. Attach each of these AWS managed policies:",
        "",
    ]
    lines += [f"   - {policy}" for policy in policies]
    lines += [
        "",
        "5. Save, wait about a minute for the change to propagate, then run "
        "`python deploy/deploy.py` again.",
        "",
        "Nothing was created; re-running the script after the policies are attached is safe.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# --- 2. table ---------------------------------------------------------------


def ensure_table() -> None:
    from benefitline.store import DynamoStore

    DynamoStore(TABLE, region=REGION).ensure_table()
    say(f"dynamodb table {TABLE} ready")


# --- 3. runtime -------------------------------------------------------------


def agentcore(args: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess:
    say(f"agentcore {' '.join(args)}  (in {cwd})")
    # npm installs the CLI as agentcore.cmd / agentcore.ps1 on Windows; CreateProcess only
    # resolves .exe, so look the shim up with the PATHEXT-aware which().
    exe = shutil.which("agentcore.cmd") or shutil.which("agentcore")
    if exe is None:
        raise SystemExit("agentcore CLI not found on PATH (npm i -g @aws/agentcore)")
    return subprocess.run(
        [exe, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
    )


def find_runtime_arn() -> str:
    """The runtime ARN from the control plane, matched on name."""
    client = boto3.client("bedrock-agentcore-control", region_name=REGION)
    token = None
    while True:
        page = client.list_agent_runtimes(**({"nextToken": token} if token else {}))
        for item in page.get("agentRuntimes", []):
            if item.get("agentRuntimeName") == RUNTIME_NAME:
                return item.get("agentRuntimeArn", "")
        token = page.get("nextToken")
        if not token:
            return ""


def deploy_runtime() -> str:
    """Create the project and the agent if they are new, then deploy. Idempotent.

    `agentcore create --project-name X` (CLI 0.29.0) makes a NEW directory `X/` holding
    `agentcore/agentcore.json`, and every later command has to run inside it, with
    `--code-location ..` pointing back at this repo. `add agent --type byo` also refuses
    to run without `--framework` and `--model-provider`. All four were checked against
    `agentcore --help` and a throwaway project on this machine on 2026-09-13.
    """
    if not (ROOT / "Dockerfile").exists():
        raise SystemExit(
            "deploy needs a Dockerfile at the repo root: `--build Container` makes the "
            "CLI look for one at the code location, and the dependency closure "
            "(policyengine-us and pandas alone are ~110 MB installed) is far past the "
            "250 MB CodeZip limit, so Container is the only route. "
            "See _runs/2026-09-13_phase4_deploy/Dockerfile.suggested."
        )
    if not (PROJECT_DIR / "agentcore" / "agentcore.json").exists():
        agentcore(["create", "--no-agent", "--project-name", PROJECT_NAME, "--defaults"])
        agentcore([
            "add", "agent", "--name", RUNTIME_NAME, "--type", "byo",
            "--framework", "Strands", "--model-provider", "Bedrock",
            "--code-location", "..", "--entrypoint", "src/app.py",
            "--build", "Container", "--language", "Python", "--protocol", "HTTP",
        ], cwd=PROJECT_DIR)
    result = agentcore(["deploy", "-y", "--json"], cwd=PROJECT_DIR)
    print(result.stdout[-4000:])
    if result.returncode != 0:
        print(result.stderr[-4000:], file=sys.stderr)
        raise SystemExit(f"agentcore deploy failed with exit code {result.returncode}")

    found = re.findall(r"arn:aws:bedrock-agentcore:[^\s\"']+runtime/[A-Za-z0-9_\-]+",
                       result.stdout)
    arn = found[-1] if found else find_runtime_arn()
    if not arn:
        raise SystemExit("agentcore deploy printed no runtime ARN and none is listed")
    say(f"runtime {arn}")
    return arn


def runtime_environment(arn: str) -> str:
    """Set the runtime's environment variables and return its execution role ARN."""
    client = boto3.client("bedrock-agentcore-control", region_name=REGION)
    runtime_id = arn.rsplit("/", 1)[-1]
    current = client.get_agent_runtime(agentRuntimeId=runtime_id)
    env = {
        "BENEFITLINE_TABLE": TABLE,
        "MODEL_PRIMARY": MODEL_PRIMARY,
        "MODEL_PRIMARY_ALT": MODEL_PRIMARY_ALT,
        "MODEL_FALLBACK": MODEL_FALLBACK,
        "BYPASS_TOOL_CONSENT": "true",
        "AWS_REGION": REGION,
    }
    kwargs = {
        "agentRuntimeId": runtime_id,
        "agentRuntimeArtifact": current["agentRuntimeArtifact"],
        "roleArn": current["roleArn"],
        "networkConfiguration": current["networkConfiguration"],
        "environmentVariables": env,
    }
    if "protocolConfiguration" in current:
        kwargs["protocolConfiguration"] = current["protocolConfiguration"]
    # UpdateAgentRuntime is a full replacement, so every field that must survive the
    # update is sent back. MMDSv2 is mandatory since 2026-06-30 (../12_AGENTCORE_RUNTIME.md):
    # a runtime without it cannot be invoked and returns a ValidationException.
    metadata = current.get("metadataConfiguration") or {}
    kwargs["metadataConfiguration"] = {"requireMMDSV2": True} if not metadata else dict(
        metadata, requireMMDSV2=True)
    client.update_agent_runtime(**kwargs)
    say("runtime environment variables set: " + ", ".join(sorted(env)))
    return current["roleArn"]


def runtime_permissions(role_arn: str) -> None:
    """The runtime role calls two models and one table, and nothing else."""
    account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokeTheTwoModels",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                "Resource": [
                    f"arn:aws:bedrock:*::foundation-model/{MODEL_PRIMARY.split('.', 1)[-1]}*",
                    f"arn:aws:bedrock:*:{account}:inference-profile/{MODEL_PRIMARY}",
                    f"arn:aws:bedrock:*:{account}:inference-profile/{MODEL_PRIMARY_ALT}",
                    f"arn:aws:bedrock:*:{account}:inference-profile/{MODEL_FALLBACK}",
                    f"arn:aws:bedrock:*::foundation-model/{MODEL_FALLBACK.split('.', 1)[-1]}",
                ],
            },
            {
                "Sid": "TheCaseTable",
                "Effect": "Allow",
                "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem",
                           "dynamodb:Scan", "dynamodb:Query", "dynamodb:DescribeTable"],
                "Resource": f"arn:aws:dynamodb:{REGION}:{account}:table/{TABLE}",
            },
        ],
    }
    boto3.client("iam").put_role_policy(
        RoleName=role_arn.rsplit("/", 1)[-1],
        PolicyName=RUNTIME_POLICY,
        PolicyDocument=json.dumps(document),
    )
    say(f"runtime role {role_arn.rsplit('/', 1)[-1]} may invoke the models and the table")


# --- 4. the public door -----------------------------------------------------


def proxy_zip() -> bytes:
    """handler.py plus the page and the demo scripts it serves."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(ROOT / "deploy" / "lambda_proxy" / "handler.py", "handler.py")
        zf.write(ROOT / "web" / "index.html", "index.html")
        zf.write(ROOT / "gallery" / "scripts.json", "scripts.json")
    return buffer.getvalue()


def ensure_proxy_role(runtime_arn: str) -> str:
    iam = boto3.client("iam")
    trust = {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow",
                       "Principal": {"Service": "lambda.amazonaws.com"},
                       "Action": "sts:AssumeRole"}],
    }
    try:
        role = iam.create_role(
            RoleName=LAMBDA_ROLE,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Benefitline demo proxy: logs, and one runtime it may invoke",
        )["Role"]["Arn"]
        say(f"created role {LAMBDA_ROLE}")
        time.sleep(12)   # a brand new role is not usable by Lambda for a few seconds
    except iam.exceptions.EntityAlreadyExistsException:
        role = iam.get_role(RoleName=LAMBDA_ROLE)["Role"]["Arn"]
    iam.attach_role_policy(
        RoleName=LAMBDA_ROLE,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )
    iam.put_role_policy(
        RoleName=LAMBDA_ROLE,
        PolicyName="benefitline-invoke-runtime",
        PolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": "bedrock-agentcore:InvokeAgentRuntime",
                "Resource": [runtime_arn, f"{runtime_arn}/*"],
            }],
        }),
    )
    return role


def ensure_proxy(role_arn: str, runtime_arn: str) -> str:
    """Create or update the function, then return its Function URL."""
    lam = boto3.client("lambda", region_name=REGION)
    code = proxy_zip()
    env = {"Variables": {"AGENT_ARN": runtime_arn}}
    try:
        lam.get_function(FunctionName=LAMBDA_NAME)
        lam.update_function_code(FunctionName=LAMBDA_NAME, ZipFile=code)
        waiter = lam.get_waiter("function_updated_v2")
        waiter.wait(FunctionName=LAMBDA_NAME)
        lam.update_function_configuration(
            FunctionName=LAMBDA_NAME, Timeout=PROXY_TIMEOUT, MemorySize=512, Environment=env,
        )
        waiter.wait(FunctionName=LAMBDA_NAME)
        say(f"updated lambda {LAMBDA_NAME}")
    except lam.exceptions.ResourceNotFoundException:
        lam.create_function(
            FunctionName=LAMBDA_NAME,
            Runtime="python3.13",
            Role=role_arn,
            Handler="handler.handler",
            Code={"ZipFile": code},
            Timeout=PROXY_TIMEOUT,
            MemorySize=512,
            Environment=env,
            Description="Benefitline demo: serves the page and forwards the JSON API",
        )
        lam.get_waiter("function_active_v2").wait(FunctionName=LAMBDA_NAME)
        say(f"created lambda {LAMBDA_NAME}")

    cors = {
        "AllowOrigins": ["*"],
        # Function URLs answer preflight themselves; "OPTIONS" is rejected (max 6 chars per member).
        "AllowMethods": ["GET", "POST"],
        "AllowHeaders": ["content-type"],
        "MaxAge": 3600,
    }
    try:
        url = lam.create_function_url_config(
            FunctionName=LAMBDA_NAME, AuthType="NONE", Cors=cors,
        )["FunctionUrl"]
    except lam.exceptions.ResourceConflictException:
        url = lam.update_function_url_config(
            FunctionName=LAMBDA_NAME, AuthType="NONE", Cors=cors,
        )["FunctionUrl"]
    # Since October 2025 a public Function URL needs two statements:
    # InvokeFunctionUrl (auth type NONE) and InvokeFunction scoped to URL calls.
    # https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html
    statements = [
        dict(StatementId="public-function-url", Action="lambda:InvokeFunctionUrl",
             Principal="*", FunctionUrlAuthType="NONE"),
        dict(StatementId="public-function-url-invoke", Action="lambda:InvokeFunction",
             Principal="*", InvokedViaFunctionUrl=True),
    ]
    for st in statements:
        try:
            lam.add_permission(FunctionName=LAMBDA_NAME, **st)
        except lam.exceptions.ResourceConflictException:
            pass
    return url


# --- main -------------------------------------------------------------------


def main() -> int:
    load_env()
    identity = boto3.client("sts", region_name=REGION).get_caller_identity()
    say(f"account {identity['Account']} in {REGION}")

    missing = preflight()
    if missing:
        path = write_blocker(missing, identity)
        print(f"\nBLOCKED. {len(missing)} permission(s) missing; nothing was created.")
        print(f"The exact console step is in {path}")
        return 2

    ensure_table()
    runtime_arn = deploy_runtime()
    role_arn = runtime_environment(runtime_arn)
    runtime_permissions(role_arn)
    proxy_role = ensure_proxy_role(runtime_arn)
    url = ensure_proxy(proxy_role, runtime_arn)

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    record = {"url": url, "runtime_arn": runtime_arn, "table": TABLE,
              "region": REGION, "time": now()}
    (RUN_DIR / "DEPLOY.json").write_text(json.dumps(record, indent=2), encoding="utf-8")

    print()
    print("Benefitline is live at:")
    print(f"  {url}")
    print(f"written to {RUN_DIR / 'DEPLOY.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
