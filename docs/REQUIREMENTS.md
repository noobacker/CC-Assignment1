# Rubric and remaining submission evidence

Source: `CC_Fall2026_Assignment1.pdf` supplied with this project.
Updated after successful live verification on 2026-10-09.
See `VERIFICATION.md` and `data/live-verification.json` for evidence.

| Requirement | Points | Implementation | Current verification |
|---|---:|---|---|
| Starter frontend hosted in S3 | 10 | Repurposed starter, S3 website + upload script | Deployed; HTTP 200 |
| API Gateway, Swagger, LF0, CORS, staged boilerplate | 15 | Original Swagger imported/augmented by CloudFormation; LF0 supports boilerplate flag | Local contracts and live API/CORS pass |
| Lex bot, LF1, three intents, slots, SQS confirmation | 20 | Greeting, thanks, dining, fallback; validation; FIFO Q1 | Production locale Built; full live conversation passes |
| LF0 → Lex → API response | 10 | Lex V2 recognize_text with stable session ID | Live round trip passes |
| 1,000+ unique Manhattan Yelp restaurants, 5+ cuisines, DynamoDB fields/timestamp | 15 | Resumable genuine Yelp importer, ZIP filtering, ID deduplication, coverage gate | 1,788 unique records; eight cuisines; all required fields verified |
| Search index `restaurants`, type `Restaurant`, IDs/cuisines only | 15 | Managed Elasticsearch 7.10 + explicit typed mapping | Mapping, fields and matching count verified live |
| LF2, SQS polling, random IDs, DynamoDB enrichment, SES, one-minute trigger | 15 | Worker, SES, retries/DLQ, EventBridge schedule | Schedule verified; two sends accepted by SES; confirm inbox receipt |
| Remember last location/category and offer the same recommendations | +10 | DynamoDB user history and optional repeat-choice slot | Live repeat prompt and identical restaurant IDs pass |

## Remaining user checks

- Confirm both recommendation emails arrived in the group member's inbox or spam folder.
- Capture any evidence requested by the instructor and submit the ZIP and live URL.

The authorized AWS profile is `dining-saurabh`, account `834519512969`, in
`us-east-1`. The complete stack, databases and frontend are deployed. The group
member's SES identity is verified and configured as sender/test recipient.

## Capture after deployment

These are useful proof for the rubric, not invented submission requirements:

1. S3 website URL and a completed browser conversation (including rejecting New
   Delhi and accepting Manhattan).
2. Lex console: three required intents, required slots, built locale and LF1
   association. Test greeting, dining and thanks in the console.
3. API Gateway `/chatbot` POST and OPTIONS; Lambda LF0/LF1/LF2; EventBridge
   `rate(1 minute)` target.
4. DynamoDB `yelp-restaurants`: 1,000+ unique records and one item with all required
   fields, including `insertedAtTimestamp`. Save `data/ingestion-report.json`.
5. Search `restaurants` mapping showing `Restaurant`, `_count`, and a document
   containing only `RestaurantID` and `Cuisine`.
6. Received SES email containing restaurant names/addresses and your preferences.
7. Returning-browser conversation offering the same recommendation, followed by
   an email with the same restaurant IDs/names. Save `data/live-verification.json`.
8. Submit through the course's actual portal and keep the stack alive until
   grading is finished; then run cleanup to stop ongoing search-instance costs.
