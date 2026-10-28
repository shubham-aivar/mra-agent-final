"""CDK app. Every `cdk` command runs this file, so pass the same -c options every time, for example:

    cdk deploy -c account=123456789012 -c region=us-east-1 -c model_id=us.anthropic.claude-opus-4-6-v1

Options: account, region (both required), model_id, retention_days (default 30), asset_dir (default build/lambda) and site_dir (default build/site).
Build both folders first: `python scripts/build_lambda.py` and `python scripts/build_site.py` (scripts/deploy.sh does everything).
"""
import aws_cdk as cdk

from infra.stack import MraStack

app = cdk.App()
get = app.node.try_get_context
account, region = get("account"), get("region")
if not account or not region:
    raise SystemExit("pass -c account=<12 digits> -c region=<region> (see the docstring in infra/app.py)")
MraStack(app, "MraAgent", asset_dir=get("asset_dir") or "build/lambda", site_dir=get("site_dir") or "build/site",
         model_id=get("model_id") or "us.anthropic.claude-opus-4-6-v1", retention_days=int(get("retention_days") or 30),
         env=cdk.Environment(account=str(account), region=region))
app.synth()
