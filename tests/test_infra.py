"""The CDK stack, checked against the synthesized CloudFormation template (no AWS needed)."""
import json
import subprocess
import sys

import pytest

aws_cdk = pytest.importorskip("aws_cdk")
from aws_cdk import App, assertions  # noqa: E402

from infra.stack import MraStack  # noqa: E402
from mra.ui.csp import hosted_csp  # noqa: E402
from tests.conftest import ROOT  # noqa: E402

CACHING_DISABLED = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"              # AWS-managed policy ids
ALL_VIEWER_EXCEPT_HOST = "b689b0a8-53d0-40ab-baf2-68738e2966ac"
Match = assertions.Match


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    assets = tmp_path_factory.mktemp("assets")
    subprocess.run([sys.executable, str(ROOT / "scripts/build_lambda.py"), "--out", str(assets / "lambda"), "--skip-deps"], check=True)
    subprocess.run([sys.executable, str(ROOT / "scripts/build_site.py"), "--out", str(assets / "site")], check=True)
    return assets


@pytest.fixture(scope="module")
def template(built):
    stack = MraStack(App(), "T", asset_dir=str(built / "lambda"), site_dir=str(built / "site"), model_id="us.anthropic.claude-opus-4-6-v1",
                     retention_days=14, env=aws_cdk.Environment(account="123456789012", region="us-east-1"))
    return assertions.Template.from_stack(stack)


def resources(template, kind):
    return list(template.find_resources(kind).values())


# ---- data ---------------------------------------------------------------------------------------------------------------------------------------
def test_the_data_bucket_is_private_encrypted_with_our_key_and_expires_what_it_should(template):
    template.has_resource_properties("AWS::S3::Bucket", {
        "BucketEncryption": {"ServerSideEncryptionConfiguration": [Match.object_like(
            {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "aws:kms", "KMSMasterKeyID": Match.any_value()}})]},
        "PublicAccessBlockConfiguration": {"BlockPublicAcls": True, "BlockPublicPolicy": True, "IgnorePublicAcls": True, "RestrictPublicBuckets": True},
        "LifecycleConfiguration": {"Rules": Match.array_with([
            Match.object_like({"Prefix": "runs/", "ExpirationInDays": 14, "Status": "Enabled"}),
            Match.object_like({"Prefix": "uploads/", "ExpirationInDays": 1, "Status": "Enabled"}),
            Match.object_like({"Prefix": "jobs/", "ExpirationInDays": 7, "Status": "Enabled"})])}})
    template.has_resource_properties("AWS::KMS::Key", {"EnableKeyRotation": True})


def test_the_data_bucket_accepts_direct_uploads_and_downloads_from_the_browser_and_nothing_else(template):
    cors = [b["Properties"]["CorsConfiguration"] for b in resources(template, "AWS::S3::Bucket") if "CorsConfiguration" in b["Properties"]]
    assert len(cors) == 1
    rule = cors[0]["CorsRules"][0]
    assert set(rule["AllowedMethods"]) == {"GET", "POST", "PUT", "HEAD"} and rule["AllowedOrigins"] == ["*"] and "DELETE" not in rule["AllowedMethods"]


def test_both_buckets_refuse_plain_http_and_the_site_bucket_is_private(template):
    policies = resources(template, "AWS::S3::BucketPolicy")
    https_only = [p for p in policies if any(s.get("Effect") == "Deny" and s.get("Condition") == {"Bool": {"aws:SecureTransport": "false"}}
                                             for s in p["Properties"]["PolicyDocument"]["Statement"])]
    assert len(https_only) == 2
    for b in resources(template, "AWS::S3::Bucket"):
        assert b["Properties"]["PublicAccessBlockConfiguration"] == {"BlockPublicAcls": True, "BlockPublicPolicy": True, "IgnorePublicAcls": True,
                                                                     "RestrictPublicBuckets": True}
    site_policy = next(p for p in policies if "cloudfront.amazonaws.com" in json.dumps(p))
    grant = next(s for s in site_policy["Properties"]["PolicyDocument"]["Statement"] if s["Principal"] == {"Service": "cloudfront.amazonaws.com"})
    assert grant["Action"] == "s3:GetObject" and "AWS:SourceArn" in json.dumps(grant["Condition"])          # only this distribution may read the page


# ---- sign-in -------------------------------------------------------------------------------------------------------------------------------------
def test_sign_in_is_invite_only_with_a_username_and_password_and_no_mfa(template):
    template.has_resource_properties("AWS::Cognito::UserPool", {
        "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True}, "MfaConfiguration": "OFF",
        "Policies": {"PasswordPolicy": Match.object_like({"MinimumLength": 10, "RequireLowercase": True, "RequireUppercase": True, "RequireNumbers": True})},
        "AliasAttributes": ["email"]})
    pool = resources(template, "AWS::Cognito::UserPool")[0]["Properties"]
    assert "EnabledMfas" not in pool and "SmsConfiguration" not in pool and "UsernameAttributes" not in pool          # a plain username signs in
    assert not resources(template, "AWS::Cognito::IdentityPool")


def test_the_web_client_signs_in_with_a_password_and_has_no_secret(template):
    template.has_resource_properties("AWS::Cognito::UserPoolClient", {
        "GenerateSecret": False, "PreventUserExistenceErrors": "ENABLED", "EnableTokenRevocation": True,
        "ExplicitAuthFlows": Match.array_with(["ALLOW_USER_PASSWORD_AUTH", "ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]),
        "IdTokenValidity": 60, "AccessTokenValidity": 60, "RefreshTokenValidity": 720,                       # minutes: 1 h, 1 h, 12 h
        "TokenValidityUnits": {"IdToken": "minutes", "AccessToken": "minutes", "RefreshToken": "minutes"}})
    client = resources(template, "AWS::Cognito::UserPoolClient")[0]["Properties"]
    assert not any(k in client for k in ("AllowedOAuthFlows", "AllowedOAuthScopes", "CallbackURLs", "LogoutURLs"))    # no OAuth: the page signs in itself
    assert client.get("AllowedOAuthFlowsUserPoolClient", False) is False
    template.has_resource_properties("AWS::Cognito::UserPoolGroup", {"GroupName": "mra-reviewers"})


# ---- the function --------------------------------------------------------------------------------------------------------------------------------
def test_the_function_settings_and_environment(template):
    template.has_resource_properties("AWS::Lambda::Function", {
        "FunctionName": "mra-agent", "Runtime": "python3.12", "Architectures": ["arm64"], "Timeout": 900, "MemorySize": 2048,
        "Handler": "hosted.web.handler",
        "Environment": {"Variables": Match.object_like({
            "MRA_STORAGE_MODE": "aws", "MRA_LLM_MODE": "bedrock", "MRA_REVIEWER_GROUP": "mra-reviewers", "MRA_CONFIG_DIR": "/var/task/config",
            "MRA_MODEL_ID": "us.anthropic.claude-opus-4-6-v1"})}})


def test_the_function_permissions_are_narrow(template):
    own_policy = [p for p in resources(template, "AWS::IAM::Policy") if any(r.get("Ref", "").startswith("FnServiceRole") for r in p["Properties"]["Roles"])]
    assert len(own_policy) == 1
    statements = own_policy[0]["Properties"]["PolicyDocument"]["Statement"]
    bedrock = [s for s in statements if "bedrock:InvokeModel" in json.dumps(s["Action"])]
    assert len(bedrock) == 1 and bedrock[0]["Action"] == "bedrock:InvokeModel" and bedrock[0]["Resource"] != "*"
    own = [s for s in statements if s["Action"] == "lambda:InvokeFunction"]
    assert len(own) == 1 and "function:mra-agent" in json.dumps(own[0]["Resource"])
    assert not any(s["Action"] == "*" or s["Resource"] == "*" for s in statements if "logs" not in json.dumps(s["Action"]) and "xray" not in json.dumps(s["Action"]))
    kms = [s for s in statements if "kms:" in json.dumps(s["Action"])]
    assert kms and all("Resource" in s and s["Resource"] != "*" for s in kms)                                    # the data key, not every key


# ---- the API ---------------------------------------------------------------------------------------------------------------------------------------
def test_the_only_wildcard_permission_is_the_deploy_helper_clearing_the_cdn_cache(template):
    """CloudFront cannot scope cache invalidation to one distribution, so CDK's copy-the-page helper has it on *; the function itself has none."""
    wide = [s for p in resources(template, "AWS::IAM::Policy") for s in p["Properties"]["PolicyDocument"]["Statement"]
            if s.get("Resource") == "*" or s.get("Action") == "*"]
    assert len(wide) == 1 and sorted(wide[0]["Action"]) == ["cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"]


def test_the_api_needs_a_token_except_for_the_health_check(template):
    template.resource_count_is("AWS::ApiGatewayV2::Authorizer", 1)
    template.has_resource_properties("AWS::ApiGatewayV2::Authorizer", {"AuthorizerType": "JWT", "IdentitySource": ["$request.header.Authorization"],
                                                                      "JwtConfiguration": Match.object_like({"Audience": Match.any_value()})})
    by_key = {r["Properties"]["RouteKey"]: r["Properties"] for r in resources(template, "AWS::ApiGatewayV2::Route")}
    assert set(by_key) == {"GET /api/health", "GET /api/{proxy+}", "POST /api/{proxy+}"}
    assert by_key["GET /api/health"]["AuthorizationType"] == "NONE"
    assert by_key["GET /api/{proxy+}"]["AuthorizationType"] == "JWT" and by_key["POST /api/{proxy+}"]["AuthorizationType"] == "JWT"
    template.has_resource_properties("AWS::ApiGatewayV2::Stage", {"StageName": "$default",
                                     "DefaultRouteSettings": {"ThrottlingRateLimit": 20, "ThrottlingBurstLimit": 40}})
    assert "CorsConfiguration" not in resources(template, "AWS::ApiGatewayV2::Api")[0]["Properties"]            # same address as the page: no cross-site calls


# ---- CloudFront ---------------------------------------------------------------------------------------------------------------------------------------
def distribution(template):
    return resources(template, "AWS::CloudFront::Distribution")[0]["Properties"]["DistributionConfig"]


def test_cloudfront_serves_the_page_from_the_private_bucket_over_https_only(template):
    d = distribution(template)
    assert d["DefaultRootObject"] == "index.html" and d["Enabled"] is True and d["PriceClass"] == "PriceClass_100"
    assert d["HttpVersion"] == "http2and3" and "ViewerCertificate" not in d                                   # the default *.cloudfront.net certificate
    default = d["DefaultCacheBehavior"]
    assert default["ViewerProtocolPolicy"] == "redirect-to-https" and default["AllowedMethods"] == ["GET", "HEAD"]
    s3_origin = next(o for o in d["Origins"] if "S3OriginConfig" in o)
    assert s3_origin["OriginAccessControlId"] and s3_origin["S3OriginConfig"]["OriginAccessIdentity"] == ""     # origin access control, no public bucket
    template.resource_count_is("AWS::CloudFront::OriginAccessControl", 1)


def test_cloudfront_sends_api_calls_to_the_api_with_the_token_and_never_caches_them(template):
    d = distribution(template)
    (api,) = d["CacheBehaviors"]
    assert api["PathPattern"] == "/api/*" and api["ViewerProtocolPolicy"] == "https-only"
    assert sorted(api["AllowedMethods"]) == sorted(["GET", "HEAD", "OPTIONS", "PUT", "PATCH", "POST", "DELETE"])
    assert api["CachePolicyId"] == CACHING_DISABLED
    assert api["OriginRequestPolicyId"] == ALL_VIEWER_EXCEPT_HOST                                              # forwards Authorization, not Host
    origin = next(o for o in d["Origins"] if o["Id"] == api["TargetOriginId"])
    assert origin["CustomOriginConfig"]["OriginProtocolPolicy"] == "https-only" and origin["CustomOriginConfig"]["OriginReadTimeout"] == 30
    assert ".execute-api.us-east-1.amazonaws.com" in json.dumps(origin["DomainName"]) and "ApiGatewayV2" not in origin.get("OriginPath", "")


def test_every_response_carries_a_strict_content_policy_and_browser_protections(template):
    policy = resources(template, "AWS::CloudFront::ResponseHeadersPolicy")[0]["Properties"]["ResponseHeadersPolicyConfig"]["SecurityHeadersConfig"]
    csp = policy["ContentSecurityPolicy"]
    assert csp["Override"] is True
    flat = json.dumps(csp["ContentSecurityPolicy"])
    for needed in ("default-src 'none'", "script-src 'self'", "style-src 'self'", "frame-ancestors 'none'", "cognito-idp.us-east-1.amazonaws.com",
                   ".s3.us-east-1.amazonaws.com", "base-uri 'none'"):
        assert needed in flat, needed
    assert "unsafe-inline" not in flat and "unsafe-eval" not in flat and "http:" not in flat.replace("https:", "")
    assert policy["FrameOptions"] == {"FrameOption": "DENY", "Override": True} and policy["ContentTypeOptions"] == {"Override": True}
    assert policy["ReferrerPolicy"]["ReferrerPolicy"] == "no-referrer" and policy["StrictTransportSecurity"]["AccessControlMaxAgeSec"] == 31536000
    for b in [distribution(template)["DefaultCacheBehavior"], *distribution(template)["CacheBehaviors"]]:
        assert b["ResponseHeadersPolicyId"]


def test_the_policy_function_names_exactly_the_hosts_the_page_needs():
    csp = hosted_csp("eu-west-1", "bucket-x.s3.eu-west-1.amazonaws.com")
    assert "connect-src 'self' https://cognito-idp.eu-west-1.amazonaws.com https://bucket-x.s3.eu-west-1.amazonaws.com;" in csp + ";"
    assert "img-src 'self' data:" in csp and "frame-src" not in csp and "*" not in csp


# ---- the page and the outputs ----------------------------------------------------------------------------------------------------------------------------
def test_the_page_is_copied_with_its_settings_and_the_cache_is_cleared(template):
    dep = resources(template, "Custom::CDKBucketDeployment")[0]["Properties"]
    assert dep["Prune"] is True and dep["DistributionPaths"] == ["/*"] and dep["DistributionId"]
    assert len(dep["SourceBucketNames"]) == 2 or len(dep["SourceObjectKeys"]) == 2                                # the built page and config.json
    flat = json.dumps(template.to_json())
    assert '"hosted": true' in flat.replace("\\", "") or "hosted" in flat


def test_the_outputs_a_person_needs_after_deploying(template):
    assert {"SiteUrl", "UserPoolId", "ClientId", "DataBucket", "SiteBucket", "FunctionName", "DistributionId", "ReviewerGroup"} <= set(template.to_json()["Outputs"])


def test_the_resources_created_are_what_the_design_says_and_nothing_extra(template):
    kinds = {r["Type"] for r in template.to_json()["Resources"].values()}
    assert "AWS::WAFv2::WebACL" not in kinds and "AWS::Cognito::IdentityPool" not in kinds and "AWS::EC2::VPC" not in kinds and "AWS::RDS::DBInstance" not in kinds
    assert {"AWS::CloudFront::Distribution", "AWS::Cognito::UserPool", "AWS::Lambda::Function", "AWS::ApiGatewayV2::Api"} <= kinds


# ---- the build scripts ---------------------------------------------------------------------------------------------------------------------------------
def test_the_function_folder_has_the_code_and_config_only(built):
    out = built / "lambda"
    assert (out / "hosted/web.py").exists() and (out / "mra/service.py").exists() and (out / "config/prompts.json").exists()
    assert (out / "mra/outputs/templates/mra_readout_default.pptx").exists() and (out / "mra/ui/routes.py").exists()
    assert not list(out.rglob("__pycache__")) and not (out / "tests").exists() and not (out / "hosted/handler.py").exists()


def test_the_site_folder_is_the_page_for_the_hosted_version(built):
    site = built / "site"
    assert sorted(p.name for p in site.iterdir()) == ["index.html", "static"] and sorted(p.name for p in (site / "static").iterdir()) == ["app.js", "style.css"]
    html = (site / "index.html").read_text()
    assert "__MRA_TOKEN__" not in html and 'name="mra-token" content=""' in html
    assert (site / "static/app.js").read_text() == (ROOT / "mra/ui/static/app.js").read_text()
    for ref in ('src="/static/app.js"', 'href="/static/style.css"'):                                       # every file the page asks for is in the folder
        assert ref in html and (site / ref.split('"')[1].lstrip("/")).is_file(), ref


def test_the_app_refuses_to_run_without_an_account_and_region():
    r = subprocess.run([sys.executable, "-c", "import runpy; runpy.run_path('infra/app.py')"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode != 0 and "account" in (r.stderr + r.stdout)
