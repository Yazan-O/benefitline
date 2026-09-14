"""One-shot: add the lambda:InvokeFunction statement a public Function URL needs
since October 2025, then check the live URL. Idempotent."""
import os, sys, time, urllib.request
import boto3
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
lam = boto3.client("lambda", region_name="us-east-1")
FN = "benefitline-proxy"
try:
    lam.add_permission(FunctionName=FN, StatementId="public-function-url-invoke",
                       Action="lambda:InvokeFunction", Principal="*", InvokedViaFunctionUrl=True)
    print("added statement public-function-url-invoke")
except lam.exceptions.ResourceConflictException:
    print("statement public-function-url-invoke already present")
url = lam.get_function_url_config(FunctionName=FN)["FunctionUrl"]
for i in range(6):
    try:
        code = urllib.request.urlopen(url, timeout=20).status
    except urllib.error.HTTPError as e:
        code = e.code
    print(f"GET {url} -> {code}")
    if code == 200:
        sys.exit(0)
    time.sleep(15)
sys.exit(1)
