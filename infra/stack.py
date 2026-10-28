"""Hosted deployment (AWS CDK, Python): the review page and its API, behind CloudFront.

    Browser --HTTPS--> CloudFront --/            --> private S3 bucket (the page; origin access control)
                                  --/api/*       --> API Gateway HTTP API (Cognito JWT) --> Lambda --> Bedrock, S3 (KMS)
    Cognito: invite-only, username and password (no MFA by design), one group that may change things.

The page and the API share one address, so there is no cross-site (CORS) traffic except the browser's direct upload to the data bucket. Large files
never pass through the API (API Gateway and Lambda limit a request to a few MB): the page uploads to S3 with a presigned form and downloads
finished files with a presigned link. Nothing is always-on: you pay per request, per token and per GB.
"""
from __future__ import annotations

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_apigatewayv2_authorizers as authorizers
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from constructs import Construct

from mra.ui.csp import hosted_csp


class MraStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, asset_dir: str, site_dir: str, model_id: str, retention_days: int = 30,
                 function_name: str = "mra-agent", reviewer_group: str = "mra-reviewers", **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ---- data: one private bucket, encrypted with our own key --------------------------------------------------------------
        key = kms.Key(self, "DataKey", enable_key_rotation=True, removal_policy=RemovalPolicy.DESTROY,
                      description="Encrypts MRA agent data at rest")
        data = s3.Bucket(
            self, "Data", encryption=s3.BucketEncryption.KMS, encryption_key=key, bucket_key_enabled=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL, enforce_ssl=True, versioned=False,
            removal_policy=RemovalPolicy.DESTROY, auto_delete_objects=True,
            # The browser POSTs a file straight to this bucket using a presigned form. The form is the credential (one key, a size limit, 15 minutes),
            # so any origin may attempt it; without a valid signature S3 refuses. The site's address is not used here because it would make the
            # bucket depend on CloudFront, which depends on the API, which depends on this bucket.
            cors=[s3.CorsRule(allowed_methods=[s3.HttpMethods.GET, s3.HttpMethods.POST, s3.HttpMethods.PUT, s3.HttpMethods.HEAD],
                              allowed_origins=["*"], allowed_headers=["*"], exposed_headers=["ETag"], max_age=3000)],
            lifecycle_rules=[
                s3.LifecycleRule(id="runs", prefix="runs/", expiration=Duration.days(retention_days)),
                s3.LifecycleRule(id="cache", prefix="cache/", expiration=Duration.days(retention_days)),
                s3.LifecycleRule(id="uploads", prefix="uploads/", expiration=Duration.days(1)),
                s3.LifecycleRule(id="jobs", prefix="jobs/", expiration=Duration.days(7)),
                s3.LifecycleRule(id="purge-log", prefix="purge_log/", expiration=Duration.days(365)),
                s3.LifecycleRule(id="ui", prefix="ui/", expiration=Duration.days(365)),
            ])
        site = s3.Bucket(self, "Site", encryption=s3.BucketEncryption.S3_MANAGED, block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                         enforce_ssl=True, removal_policy=RemovalPolicy.DESTROY, auto_delete_objects=True)

        # ---- sign-in: invite-only, username and password, no MFA ---------------------------------------------------------------------
        pool = cognito.UserPool(
            self, "Users", self_sign_up_enabled=False, sign_in_aliases=cognito.SignInAliases(username=True, email=True),
            mfa=cognito.Mfa.OFF,
            password_policy=cognito.PasswordPolicy(min_length=10, require_lowercase=True, require_uppercase=True, require_digits=True,
                                                   require_symbols=False, temp_password_validity=Duration.days(7)),
            account_recovery=cognito.AccountRecovery.NONE, removal_policy=RemovalPolicy.DESTROY)
        client = pool.add_client("Web", generate_secret=False, auth_flows=cognito.AuthFlow(user_password=True, user_srp=True),
                                 prevent_user_existence_errors=True, enable_token_revocation=True,
                                 disable_o_auth=True,                                                  # the page signs in itself
                                 id_token_validity=Duration.hours(1),
                                 access_token_validity=Duration.hours(1), refresh_token_validity=Duration.hours(12))
        cognito.CfnUserPoolGroup(self, "ReviewerGroupResource", user_pool_id=pool.user_pool_id, group_name=reviewer_group,
                                 description="May run assessments and record review decisions")

        # ---- the function -----------------------------------------------------------------------------------------------------------
        logs_group = logs.LogGroup(self, "Logs", retention=logs.RetentionDays.ONE_MONTH, removal_policy=RemovalPolicy.DESTROY)
        fn = lambda_.Function(
            self, "Fn", function_name=function_name, runtime=lambda_.Runtime.PYTHON_3_12, architecture=lambda_.Architecture.ARM_64,
            handler="hosted.web.handler", code=lambda_.Code.from_asset(asset_dir), timeout=Duration.minutes(15), memory_size=2048,
            log_group=logs_group,
            environment={"MRA_STORAGE_MODE": "aws", "MRA_BUCKET": data.bucket_name, "MRA_LLM_MODE": "bedrock", "MRA_MODEL_ID": model_id,
                         "MRA_REGION": self.region, "MRA_REVIEWER_GROUP": reviewer_group, "MRA_CONFIG_DIR": "/var/task/config"})
        data.grant_read_write(fn)                        # also grants use of the bucket's KMS key (needed to sign upload and download links)
        fn.add_to_role_policy(iam.PolicyStatement(       # Converse needs InvokeModel; cross-Region inference profiles route across Regions
            actions=["bedrock:InvokeModel"],
            resources=[f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/*", "arn:aws:bedrock:*::foundation-model/anthropic.*"]))
        fn.add_to_role_policy(iam.PolicyStatement(       # the background half of a task: the function calls itself
            actions=["lambda:InvokeFunction"], resources=[f"arn:aws:lambda:{self.region}:{self.account}:function:{function_name}"]))

        # ---- the API (reachable only through CloudFront in practice, and always needs a valid token) ---------------------------------------
        api = apigw.HttpApi(self, "Api", create_default_stage=False)
        apigw.HttpStage(self, "Stage", http_api=api, stage_name="$default", auto_deploy=True,
                        throttle=apigw.ThrottleSettings(rate_limit=20, burst_limit=40))
        integration = integrations.HttpLambdaIntegration("Integration", fn)
        auth = authorizers.HttpUserPoolAuthorizer("Auth", pool, user_pool_clients=[client])
        api.add_routes(path="/api/health", methods=[apigw.HttpMethod.GET], integration=integration)
        api.add_routes(path="/api/{proxy+}", methods=[apigw.HttpMethod.GET, apigw.HttpMethod.POST], integration=integration, authorizer=auth)

        # ---- CloudFront: one address for the page and the API --------------------------------------------------------------------------
        csp = hosted_csp(self.region, f"{data.bucket_name}.s3.{self.region}.amazonaws.com")
        security = cloudfront.ResponseHeadersPolicy(
            self, "Security", comment="MRA agent: strict content policy and browser protections",
            security_headers_behavior=cloudfront.ResponseSecurityHeadersBehavior(
                content_security_policy=cloudfront.ResponseHeadersContentSecurityPolicy(content_security_policy=csp, override=True),
                content_type_options=cloudfront.ResponseHeadersContentTypeOptions(override=True),
                frame_options=cloudfront.ResponseHeadersFrameOptions(frame_option=cloudfront.HeadersFrameOption.DENY, override=True),
                referrer_policy=cloudfront.ResponseHeadersReferrerPolicy(
                    referrer_policy=cloudfront.HeadersReferrerPolicy.NO_REFERRER, override=True),
                strict_transport_security=cloudfront.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=Duration.days(365), include_subdomains=True, override=True)))
        api_origin = origins.HttpOrigin(f"{api.api_id}.execute-api.{self.region}.amazonaws.com",
                                        protocol_policy=cloudfront.OriginProtocolPolicy.HTTPS_ONLY, read_timeout=Duration.seconds(30))
        cdn = cloudfront.Distribution(
            self, "Cdn", comment="MRA agent review page", default_root_object="index.html", price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            minimum_protocol_version=cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021, http_version=cloudfront.HttpVersion.HTTP2_AND_3,
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD, cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                response_headers_policy=security, compress=True),
            additional_behaviors={"/api/*": cloudfront.BehaviorOptions(
                origin=api_origin, viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,                                    # never cache an API answer
                origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,       # passes Authorization on
                response_headers_policy=security, compress=False)})

        # ---- the page itself: copied to the site bucket, with its settings, and the CDN cache cleared --------------------------------------
        s3deploy.BucketDeployment(
            self, "SiteFiles", destination_bucket=site, distribution=cdn, distribution_paths=["/*"], prune=True,
            sources=[s3deploy.Source.asset(site_dir),
                     s3deploy.Source.json_data("config.json", {"hosted": True, "region": self.region, "userPoolId": pool.user_pool_id,
                                                                "clientId": client.user_pool_client_id})])

        outputs = {"SiteUrl": f"https://{cdn.distribution_domain_name}", "UserPoolId": pool.user_pool_id,
                   "ClientId": client.user_pool_client_id, "DataBucket": data.bucket_name, "SiteBucket": site.bucket_name,
                   "FunctionName": fn.function_name, "DistributionId": cdn.distribution_id, "ReviewerGroup": reviewer_group}
        for name, value in outputs.items():
            CfnOutput(self, name, value=value)
