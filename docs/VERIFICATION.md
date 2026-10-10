# Verification record

## Updated 2026-10-09

- 59 pytest tests pass. CloudFormation lint, Python compilation and frontend
  JavaScript syntax checks pass.
- Base stack and S3 frontend deployed in authorized account 834519512969,
  us-east-1. Website and API CORS preflight return HTTP 200.
- Fixed a live Lex build failure: ReusePrevious lacked a slot priority.
  Deployment now checks the published locale build status before reporting success.
- Moved the returning-user question immediately after matching location/cuisine,
  rather than after email collection. Added a regression test for partially
  collected slots. The live repeat test now retains Lex session state, like a
  page refresh, instead of deleting the session, and records the repeat dialogue.
- Real API Gateway -> LF0 -> Lex -> LF1 calls pass greetings, thanks,
  Manhattan-only location validation, cuisine collection and party-size prompts.
  See data/base-verification.json for the live transcript.
- Collected 1,743 genuine Yelp records and preserved 45 additional existing
  restaurant records. The reconciled dataset contains 1,788 unique Manhattan
  restaurants across eight cuisines, with 221-229 entries per cuisine.
- Full live verification passes: required DynamoDB fields/ZIPs; matching search
  count; restaurants index with Restaurant mapping; ID/cuisine-only search
  documents; website; API CORS; greetings/thanks; enabled one-minute schedule.
- The live chatbot queued a dining request, and the scheduled worker completed
  its SES send. A returning-user request offered and reused the same restaurant
  IDs, and SES accepted the second send. See data/live-verification.json.
- Sender and test recipient use the group member's verified SES identity.
  Actual inbox receipt still needs the group member's confirmation.
- Source ZIP refreshed with live evidence. Credentials are excluded.

## Historical local verification

Checked locally on 2026-10-07:

- 53 pytest cases passed, including a cross-Lambda integration test with
  in-memory AWS transports. Tests cover slot validation, API envelopes,
  session IDs, invalid payloads, SES failures, queue deletion rules, saved
  restaurant IDs, and returning-user consent/reuse.
- CloudFormation template passed cfn-lint with no findings.
- AWS CloudFormation `validate-template` succeeded using the existing `harshal`
  profile in us-east-1; the template declares CAPABILITY_IAM.
- Python sources compile successfully; frontend JavaScript syntax check passes.
- Local frontend rendered in the browser; typing/sending before deployment
  correctly explains that an API endpoint has not been configured.
- The supplied Swagger URL rendered in Swagger Editor with POST `/chatbot`
  and the five original schema models.
- AWS STS authenticated the `harshal` and `eb-cli` profiles to the same account;
  the default profile returned InvalidClientTokenId.
- The inspected region has no SES verified identities, no DynamoDB tables and
  no completed CloudFormation stacks. Elasticsearch_7.10 is available according
  to the OpenSearch list-versions API.

Not performed: cloud provisioning, real Lex NLU/build, Yelp collection, database
imports, live browser-to-AWS conversation, scheduled worker execution, SES
sending, or inbox receipt. These remain dependent on the email address,
verification link and Yelp key. Local mocks do not verify those cloud behaviors.

No scores, screenshots of deployed resources, restaurant data or email delivery
results have been fabricated. A grade cannot be guaranteed.
