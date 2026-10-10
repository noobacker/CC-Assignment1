# Dining Concierge — CC Fall 2026, Assignment 1

A serverless dining chatbot following the supplied assignment: S3 frontend → API
Gateway → LF0 → Lex V2 → LF1 → SQS Q1 → scheduled LF2 → OpenSearch + DynamoDB → SES.
Includes the 10-point extra-credit memory feature.

**Current status:** AWS deployment, database import and automated live verification
are complete. DynamoDB and OpenSearch each contain 1,788 unique Manhattan
restaurants across eight cuisines (221-229 entries per cuisine). All 59 local
tests pass. The scheduled worker sent two requests accepted by SES, and the
extra-credit repeat request reused the original recommendations. Confirm both
emails arrived in the group member's inbox before submitting. See
[the verification record](docs/VERIFICATION.md) and `data/live-verification.json`.

Website: http://dining-concierge-website-2pdsdord8d48.s3-website-us-east-1.amazonaws.com

## Reproduce setup

Python 3.12 or later is required. This workspace already has `.venv` with the
required tools and a private, ignored `.env` file. Temporary login credentials
may be renewed with `aws login --profile dining-saurabh --region us-east-1`. The configured deployment profile is `dining-saurabh` in `us-east-1`, authenticated
through the Chrome console session using `aws login` for account 834519512969.
The complete AWS deployment is running, including search. The following steps
reproduce setup; they are not outstanding configuration tasks for this workspace.

1. Open `.env` and fill `SES_SENDER_EMAIL`, `TEST_RECIPIENT_EMAIL` (use your own
   email for both), and `YELP_API_KEY`. Keep credentials out of source control.
   `AWS_PROFILE=dining-saurabh` is configured locally. On another machine, copy
   `.env.example` to `.env` and select your own AWS profile.
2. Run:

   ```bash
   .venv/bin/python scripts/complete.py --verify-email
   ```

3. Click the Amazon SES verification link(s) in your inbox when prompted. The
   script collects genuine Yelp data first, verifies your email, deploys all AWS
   resources, imports both databases, then tests the live conversation and a
   returning-user request. It sends two recommendation emails to your test inbox.
4. Open the S3 `WebsiteUrl` printed during deployment. Confirm the two emails
   arrived, capture the evidence in `docs/REQUIREMENTS.md`, and submit the project
   and live URL according to your course portal's submission instructions.

The supplied PDF does not specify a submission portal, filename, due date, or
required report format. None has been invented.

For a new environment:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env
```

Create a Yelp API key through the [Yelp developer portal](https://www.yelp.com/developers/v3/manage_app).
The importer needs an API plan/quota that can retrieve enough businesses. It
caches progress in `data/restaurants.json` and safely resumes after API limits.
It refuses to claim success with fewer than 1,000 unique businesses or fewer
than five cuisines with approximately 200 businesses (at least 180) each. The
default target is 220 per cuisine across eight cuisines. Actual availability is
reported; no synthetic data is used to satisfy the assignment.

## Individual commands

```bash
# Validate locally (no AWS services used by tests)
.venv/bin/pytest -q
node --check frontend/assets/js/chat.js
.venv/bin/python scripts/build_template.py
.venv/bin/cfn-lint infrastructure/template.json

# Collect before creating the continuously billed search domain
.venv/bin/python scripts/ingest_yelp.py --collect-only

# Deploy all services and publish the frontend
.venv/bin/python scripts/deploy.py --verify-email

# After clicking the SES email verification link
.venv/bin/python scripts/ingest_yelp.py --load-only
.venv/bin/python scripts/verify_live.py --email YOUR_VERIFIED_EMAIL

# Preview frontend; this does not simulate Lex or a deployed backend
.venv/bin/python -m http.server 8765 --directory frontend

# Create a source ZIP, excluding secrets, caches and dependencies
.venv/bin/python scripts/package.py
```

Run commands from the project root. To manually verify a different recipient in
an SES sandbox account, use `aws ses verify-email-identity --email-address EMAIL
--profile dining-saurabh --region us-east-1` and click that recipient's verification link.
The full workflow handles this for the configured test recipient automatically.

## Project files

| File / directory | Purpose |
|---|---|
| `frontend/chat.html`, `frontend/index.html` | Repurposed assignment starter; S3 entry points |
| `frontend/assets/js/chat.js` | API conversation, persistent browser session, safe rendering |
| `swagger/swagger.yaml` | Unmodified assignment API specification |
| `src/lf0.py` | API Gateway request validation and Lex V2 runtime bridge |
| `src/lf1.py` | Intents, slot validation, repeat-choice prompt and SQS fulfillment |
| `src/lf2.py` | Scheduled polling, recommendations, SES and saved history |
| `src/common.py` | AWS clients and SigV4-signed search requests |
| `infrastructure/template.json` | Complete generated CloudFormation stack |
| `scripts/build_template.py` | Editable source for infrastructure definition |
| `scripts/ingest_yelp.py` | Yelp collection, deduplication and database loading |
| `scripts/complete.py` | Complete setup and live verification workflow |
| `scripts/verify_live.py` | Live resource, data, API, email and memory checks |
| `scripts/cleanup.py` | Remove project resources after grading |
| `tests/` | Unit tests and cross-Lambda integration with mocked AWS transports |

## Assignment-specific decisions

- The explicit requirement is **email through SES**. The example's phone-number
  prompt is inconsistent with this; this project collects email, not phone.
- It collects location, cuisine, party size, dining date, dining time and email.
  It rejects unsupported locations/cuisines, party sizes outside 1–20, malformed
  email addresses and past dates/times. All times are `America/New_York`.
- Eight supported cuisines: Chinese, Japanese, Italian, Mexican, Indian, Thai,
  French and American. Recommendations filter only by cuisine, as instructed.
- Lex V2 replaces the old Lex V1 links. All required intents have Lambda hooks;
  an additional fallback intent helps recover from unrecognized messages.
- The original Swagger request/response envelope is preserved. Session identity
  uses an `X-Session-Id` header, so no changes to the message schema are needed.
  CORS is configured on preflight, Lambda replies and API Gateway error replies.
- The staged boilerplate response is retained behind `BOILERPLATE_MODE=true` on
  LF0; the deployed default uses Lex, as required by the final integration step.
- Default search engine: **Elasticsearch 7.10 managed by Amazon OpenSearch
  Service**, preserving the literal index `restaurants` and mapping type
  `Restaurant`. Index documents contain only `RestaurantID` and `Cuisine`.
  Modern OpenSearch has removed custom mapping types. A configurable
  `--engine OpenSearch_2.19` option uses a typeless index with entity metadata;
  use that option only if your instructor accepts this adaptation.
- OpenSearch uses one `t3.small.search` node, one AZ, 10 GiB EBS and zero index
  replicas. Access requires signed IAM requests; the endpoint is not open to
  anonymous clients. IAM policy access is used instead of a shared master password.
- LF2 polls SQS once per minute through EventBridge. SQS is FIFO; successfully
  sent requests are deduplicated, failures remain for retry, and five failures
  send the message to a dead-letter queue. A conditional DynamoDB lease allows only one active worker, including on new AWS accounts with small concurrency quotas.
- Extra credit: successful recommendations persist in DynamoDB keyed by the
  browser's localStorage identity. Reloading/returning preserves the identity.
  Matching location/cuisine prompts yes/no immediately after cuisine, before
  collecting party size, date, time and email; yes reuses actual restaurant IDs,
  while no performs a new random search. “Start over” creates a new identity.
  This is per browser, not an account-based login feature.
  History becomes available after the scheduled worker successfully sends the
  first recommendations through SES. Wait for the first email before repeating
  a search on the same website in the same browser.

## Deployment notes and limits

The AWS principal must be able to create CloudFormation, IAM roles and the
services in the template. There must not already be an unrelated DynamoDB table
named `yelp-restaurants` in the chosen account/region. The script does not alter
existing unrelated resources or account-level S3 block-public-access settings.
An organization policy blocking public S3 websites will require an allowed
account or an instructor-approved hosting alternative.

SES is currently in sandbox mode in the inspected account; sender and test
recipient verification are required. Acceptance by SES is not proof of inbox
delivery: check your inbox and spam folder. A failure after SES accepts email
but before DynamoDB records success can result in a duplicate on retry; SES has
no transaction shared with DynamoDB. This is an at-least-once workflow.

The S3 website uses HTTP as required by S3 website hosting; the API uses HTTPS.
There are no AWS credentials in the frontend. API throttling is configured; this
is a course demonstration, not a production authentication system.

OpenSearch incurs ongoing instance charges, and legacy Elasticsearch can also
incur extended-support charges. Provision it only after data collection is ready
and delete the stack after grading. This follows the assignment's cost warning.

```bash
.venv/bin/python scripts/cleanup.py --confirm-stack dining-concierge
```

Cleanup deletes this project's website, databases, queue, Lambdas, API, bot,
search domain and artifact bucket. It retains SES identities. Do not run it
before the grader has finished using the application.

References: [starter](https://github.com/aditya491929/cloud-hw1-starter),
[Lex hooks](https://docs.aws.amazon.com/lexv2/latest/dg/lambda-attach-api.html),
[Yelp search API](https://docs.developer.yelp.com/reference/v3_business_search),
[Elasticsearch mapping types](https://www.elastic.co/blog/moving-from-types-to-typeless-apis-in-elasticsearch-7-0),
[OpenSearch support and billing](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/what-is.html).
