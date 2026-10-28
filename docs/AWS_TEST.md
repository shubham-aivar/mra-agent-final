# Testing the real AWS services from your laptop (nothing is deployed)

You run the same code as the laptop demo, but with the **real model (Amazon Bedrock)** and **real storage (Amazon S3)**.
No CDK, no Lambda, no API, no Cognito. Synthetic data only.

## What this creates and costs
- **One private S3 bucket** (you create it in Step 1) and a few small objects in it. You delete both in Step 7.
- **Bedrock model calls** (billed per token): the preflight uses a few hundred tokens; the full run and the three agent tasks use
  more. Cost was not estimated; `python -m mra kpis <run>` shows the tokens used so you can see for yourself after each stage.
- Nothing else. Everything you create is removed in Step 7.

## Step 0. Setup (once)
    cd aivar-mra-agent && source .venv/bin/activate
    pip install -r requirements.txt boto3
    export AWS_PROFILE=aivar-vision-admin
    aws sso login --profile $AWS_PROFILE
    aws sts get-caller-identity          # confirm it is the Aivar Vision account

## Step 1. Create the test bucket (a change in AWS: one private bucket)
    BUCKET=mra-test-<yourname>-$(date +%s)          # must be lowercase and globally unique
    aws s3api create-bucket --bucket $BUCKET --region us-east-1
    aws s3api put-public-access-block --bucket $BUCKET --public-access-block-configuration \
      BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
S3 encrypts new objects by default. (The CDK stack would use a KMS key; this test does not.)

## Step 2. Preflight: test each link alone
    python3 scripts/aws_check.py --bucket $BUCKET
Expected: PASS for identity, bedrock text, bedrock tool use, s3 bucket, s3 read/write, s3 no-overwrite write, s3 cleanup and
s3 public access (encryption is INFO). **Fix the first FAIL before going on**; each line says what to do. The two that matter most:
- *bedrock tool use* proves the agent's tool calls work on this model. If it fails, the three agent tasks will not work either.
- *s3 no-overwrite write* proves the audit log's protection works on real S3. If it fails, tell me before continuing.

## Step 3. A full run on the real model and real S3
    export MRA_STORAGE_MODE=aws MRA_BUCKET=$BUCKET MRA_LLM_MODE=bedrock MRA_REGION=us-east-1
    python3 -m mra run samples/sample_questionnaire.xlsx --evidence samples/notes --set-id aws-test
Look at the last lines: `Text: bedrock (calls ..., cached 0, fallbacks ...)` and `Stored under s3://...`.
    python3 -m mra audit aws-test --show
`fallbacks 0` is the goal. The audit view lists any model text the checker rejected and why. Rejections are the checker working;
many of them mean the prompt needs tuning.

## Step 4. The agent on the real model
    python3 -m mra agent aws-test --task evidence
    python3 -m mra proposals aws-test --kind evidence_link
    python3 -m mra audit aws-test --show
Check: did it stop with `done`? Are the quotes real and the links sensible? The audit view shows each refused tool call
(for example "that quote does not appear word for word"): that is the guardrail catching the model.
Then the other two, one at a time:
    python3 -m mra agent aws-test --task investigate
    python3 -m mra agent aws-test --task context
    python3 -m mra proposals aws-test
    python3 -m mra kpis aws-test              # model use shows the tokens spent so far

## Step 5. Review and export from S3
    python3 -m mra decide-proposals aws-test --accept P0001 P0002        # use ids you actually agree with
    python3 -m mra status aws-test                                         # lists what the release gate still needs
    python3 -m mra export aws-test --context --xlsx --docx --out aws_out    # files are read from S3 and written here

## Step 6. Audit
    python3 -m mra audit aws-test --show       # must say "chain intact"

## Step 7. Clean up (deletes data permanently; check the name first)
    python3 -m mra purge aws-test --confirm
    aws s3 rm s3://$BUCKET --recursive
    aws s3api delete-bucket --bucket $BUCKET
    unset MRA_STORAGE_MODE MRA_BUCKET MRA_LLM_MODE MRA_REGION

## What to send me
Step 2's full output; Step 3's summary lines; the output of `audit --show` after Steps 3 and 4; and the `proposals` list.

## Reading the results
| You see | It means | Do |
|---|---|---|
| `bedrock tool use` FAIL | model, Region or request format problem | send me the message |
| `fallbacks` above 0 | the checker rejected model text more than the retries allowed | send me the audit view; the prompts may need tuning |
| Many "not appear word for word" refusals | the model paraphrases instead of quoting | the prompt needs a stronger instruction |
| Agent stop reason `budget_*` or `truncated` | a limit was hit (config/settings.json) | raise the limit or tell me |
| Agent stops `done` with 0 proposals | nothing matched or the model was cautious | read its summary; try the other tasks |
