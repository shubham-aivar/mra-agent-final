#!/usr/bin/env bash
# Deploy the hosted MRA agent (CloudFront + page + API + Cognito + Lambda + S3) with the AWS CDK.
#
#   scripts/deploy.sh                                   # asks before it changes anything
#   scripts/deploy.sh --profile aivar-vision-admin --region us-east-1 --yes
#   scripts/deploy.sh --dry-run                         # prints what it would do, changes nothing
#
# Needs: python3 (3.12+), node (18+), the AWS CLI signed in (aws sso login --profile NAME), and Bedrock model access for the model in this Region.
set -euo pipefail
cd "$(dirname "$0")/.."

REGION="${AWS_REGION:-us-east-1}"
MODEL="us.anthropic.claude-opus-4-6-v1"
STACK="MraAgent"
YES=0
DRY=0
ACCOUNT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) export AWS_PROFILE="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --model-id) MODEL="$2"; shift 2 ;;
    --account) ACCOUNT="$2"; shift 2 ;;
    --yes) YES=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)"; exit 2 ;;
  esac
done

run() { if [ "$DRY" = 1 ]; then echo "[dry run] $*"; else "$@"; fi; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1 ($2)"; exit 1; }; }

need python3 "install Python 3.12 or newer"
need node "install Node 18 or newer, for example: brew install node"
if [ "$DRY" = 0 ]; then
  need aws "install the AWS CLI"
  [ -n "$ACCOUNT" ] || ACCOUNT="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)" || {
    echo "Not signed in to AWS. Run: aws sso login --profile <name>   then use --profile <name>"; exit 1; }
else
  [ -n "$ACCOUNT" ] || ACCOUNT="<your 12-digit account id>"
fi
CTX="-c account=$ACCOUNT -c region=$REGION -c model_id=$MODEL"
CDK="npx --yes aws-cdk@2"

echo "Account: $ACCOUNT   Region: $REGION   Stack: $STACK   Model: $MODEL   Profile: ${AWS_PROFILE:-<default>}"
echo "This creates: CloudFront, two S3 buckets, a KMS key, Cognito (no MFA), an API Gateway HTTP API and a Lambda function."
echo "Use synthetic data only. Costs: Bedrock tokens (main), CloudFront/Lambda/API requests, S3 storage; nothing runs while idle."
if [ "$YES" = 0 ] && [ "$DRY" = 0 ]; then
  printf "Continue? [y/N] "; read -r ans; case "$ans" in y|Y|yes) ;; *) echo "Cancelled."; exit 0 ;; esac
fi

# A dedicated environment, whatever is active in your shell: the CDK is started with this exact Python (quoted, so paths with spaces or & are fine).
VENV=".venv-deploy"
if ! "$VENV/bin/python3" -c "import sys" >/dev/null 2>&1; then
  run python3 -m venv --clear "$VENV"
fi
PY="$PWD/$VENV/bin/python3"
APP="'$PY' -m infra.app"
run "$PY" -m pip install -q -r requirements-hosted.txt
if [ "$DRY" = 0 ]; then
  "$PY" -c "import aws_cdk, boto3" 2>/dev/null || { echo "The CDK library could not be installed into $VENV. Try: $PY -m pip install -r requirements-hosted.txt"; exit 1; }
  echo "Using Python: $("$PY" --version 2>&1) at $PY"
fi
run "$PY" scripts/build_lambda.py
run "$PY" scripts/build_site.py
run $CDK bootstrap "aws://$ACCOUNT/$REGION" $CTX --app "$APP"
run $CDK deploy "$STACK" $CTX --app "$APP" --outputs-file build/cdk-outputs.json --require-approval never

if [ "$DRY" = 0 ]; then
  "$PY" - <<'PY'
import json
o = json.load(open("build/cdk-outputs.json"))["MraAgent"]
print("\nDeployed.\n  Site:       " + o["SiteUrl"] + "\n  User pool:  " + o["UserPoolId"] + "\n  Data bucket:" + " " + o["DataBucket"])
PY
fi
echo
echo "Next:"
echo "  1. $PY scripts/add_user.py --username mra-analyst --permanent   # a sign-in (add --reader for a read-only one)"
echo "  2. python3 scripts/smoke_test.py --url <Site> --username mra-analyst   # checks the whole path with synthetic data"
echo "  3. Open the Site address in a browser and sign in."
