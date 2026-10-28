# Deploying the hosted version (CloudFront + page + API)

    Browser --HTTPS--> CloudFront --/        --> private S3 bucket (the page)
                                  --/api/*   --> API Gateway (needs a Cognito token) --> Lambda --> Bedrock, S3 (KMS)

One address for the page and the API. Sign-in is a username and password in the page (Cognito, **no MFA**, invite only). Files go straight to S3 with a
presigned form (no size limit from the API) and finished files come back through a short-lived signed link. Scoring and agent tasks run as background jobs
(API Gateway cuts a request at 30 seconds); the page waits for them. The Lambda function, S3 and Cognito are created by one CDK stack, `MraAgent`.

**Use synthetic data only** (decision: no real customer data in Aivar Vision).

## Before you start
1. AWS CLI signed in to Aivar Vision as an admin: `aws sso login --profile aivar-vision-admin`.
2. Node 18+ (`brew install node`) and Python 3.12+.
3. **Bedrock model access** for the model in the Region (console, Bedrock, Model access). Without it the model calls fail with AccessDenied.

## Deploy (about 10 minutes the first time)
    scripts/deploy.sh --profile aivar-vision-admin --region us-east-1        # add --yes to skip the question; --dry-run to see the steps only
It uses its own Python environment (`.venv-deploy`), so it does not depend on what is active in your shell. It builds the function and the page, runs `cdk bootstrap` (once per account and Region), deploys, and prints the **Site** address.
CloudFront takes a few minutes to be reachable the first time.

## Make a sign-in, then check everything
    python3 scripts/add_user.py --username mra-analyst --permanent           # any username works; hidden password prompt; add --reader for a read-only user
    python3 scripts/smoke_test.py --url https://dxxxxxxxx.cloudfront.net --username mra-analyst
The smoke test signs in, uploads a synthetic questionnaire to storage, scores it, runs an agent task on the real model, changes a rating, creates the
Excel, Word and PowerPoint files, downloads each, then deletes its run. Every line must say PASS. Then open the Site address in a browser.

## What it creates
CloudFront (default `*.cloudfront.net` certificate, HTTP/2 and 3; the default certificate's security policy is fixed by CloudFront, so enforcing TLS 1.2+ needs a custom domain and certificate); two private S3 buckets (the page; the data, encrypted with our own rotating
KMS key, TLS only, with lifecycle rules that delete `uploads/` after 1 day, `jobs/` after 7, `runs/` and `cache/` after 30); a Cognito user pool (invite only,
username and password, 10+ characters with a capital, a small letter and a digit, **MFA off**), one web client with no OAuth and no secret, and the
`mra-reviewers` group; an HTTP API (throttled to 20 requests a second) whose routes need a valid token except `GET /api/health`; one Lambda function
(Python 3.12, ARM64, 2 GB, 15 minutes). The function may read and write the data bucket and its key, call `bedrock:InvokeModel` on inference profiles in this
account and Anthropic foundation models, and invoke itself for background jobs. Every response carries a strict Content-Security-Policy, HSTS and the
usual protections; the page uses no inline script or style.

## Who can do what
Signed-in users in `mra-reviewers` can change things; a user without the group can read everything and download finished files but cannot change anything.
Who did what comes from the token (shown in the audit log), never from anything the page sends.

## Cost
Bedrock tokens are the main cost. CloudFront, API Gateway and Lambda are per request; S3 and the KMS key are small. Nothing runs while idle. Costs were not estimated.

## Remove it
    npx --yes aws-cdk@2 destroy MraAgent -c account=<id> -c region=<region>      # deletes both buckets and their contents, the pool and its users

## Security notes (read these)
- **No MFA, by request.** A password is the only protection of the page. Use a strong one, create users only as needed, and use synthetic data.
- Sign-in uses Cognito's password flow over HTTPS from the page. The session lives in the browser tab only (`sessionStorage`), so closing the tab signs out;
  tokens last 1 hour and renew for up to 12 hours.
- The data bucket allows any origin to *attempt* an upload or download, because the signed form or link is the credential (one file, a size limit, minutes).
  Restricting it to the site's address would make the stack depend on itself.
- No WAF, no custom domain (so CloudFront's default TLS policy applies), no access logs. Add them if this is kept beyond a demo.
- Deleting a run removes its files; the audit trail of the deletion is kept for a year.

## What was and was not verified
Verified without AWS: the function's behaviour with real S3 semantics (moto); the stack's template (assertions on CloudFront behaviours, MFA off, no OAuth,
the policy text, IAM narrowness; cfn-lint); the page in a real browser against a stand-in for CloudFront, API Gateway, Cognito and S3 under the production
security policy; the three scripts. **Never run on real AWS:** IAM at run time, real CloudFront and API Gateway behaviour, real Cognito, Bedrock tool use.
`scripts/smoke_test.py` is the check for those: run it right after deploying.

## If something goes wrong
| You see | Likely cause |
|---|---|
| `AccessDenied` or `ValidationException` from Bedrock in a task | model access not enabled, or the model id is wrong for the Region (`--model-id`) |
| The page loads but is blank | CloudFront still propagating (wait 5 minutes), or the page files were not copied: check the stack events |
| Sign-in says "Incorrect username or password" | the user does not exist or the password is wrong: `add_user.py --reset --permanent` |
| Sign-in asks for a new password | the user was made without `--permanent`: choose one |
| An upload fails with a CORS or 403 error | the data bucket's CORS rule or the function's S3/KMS permission: check the stack was deployed whole |
| A task says it did not finish | Lambda ran out of its 15 minutes: try again with fewer notes; see the function's log in CloudWatch |
