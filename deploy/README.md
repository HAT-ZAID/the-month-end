# Deploying the read-only kernel API

**Live endpoint**

```
https://73rb5yi7skfyh5y2ivmkay2ivi0pvihx.lambda-url.ap-southeast-2.on.aws
```

Every route requires `Authorization: Bearer <token>`. The token lives in the
function's environment, never in this repo:

```bash
aws lambda get-function-configuration \
  --profile agent-toolkit --region ap-southeast-2 \
  --function-name month-end-api \
  --query "Environment.Variables.BUY_WAIT_API_TOKEN" --output text
```

## Routes

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | liveness |
| GET | `/sample` | the 25 calibration request ids |
| GET | `/sample/{id}` | that request solved live by the kernel |
| POST | `/evaluate` | `{balance, floor, amount, date}` → largest safe payment |
| GET | `/explain/{id}` | kernel decision + a plain-language rewrite |

`/explain` calls Kilo's free gateway (`https://api.kilo.ai/api/gateway`,
`nvidia/nemotron-3-ultra-550b-a55b:free`) which needs **no API key** and allows
200 requests/hour per IP. The prompt contains only the kernel decision, never an
account row, and the reply is discarded unless every figure in it already appears
in that decision — so the model cannot introduce a number. Kilo's free pool may
log prompts and route to providers that train on them; that is why only kernel
output is sent. Set `BUY_WAIT_KILO_URL` / `BUY_WAIT_KILO_MODEL` to override.

```bash
curl -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"balance":"10000","floor":"2000","amount":"5000","date":"2026-01-10"}' \
  https://73rb5yi7skfyh5y2ivmkay2ivi0pvihx.lambda-url.ap-southeast-2.on.aws/evaluate
```

## What is deliberately not deployed

The agent loop. `/evaluate` and `/sample` are the deterministic kernel: they
answer from arithmetic and cannot invent a figure. The conversational layer
(intent reading, tool choice, prose) stays client-side, where the model runs
against your own key — see the README. Adding a public `/ask` would make every
request a paid model call with no authentication beyond the bearer token.

## Cost

The account budget is **`month-end-monthly-10`** ($10/month, alerts at 50/80/100%).
Lambda is the cheapest shape available here:

- no idle cost (scales to zero),
- 1M requests and 400,000 GB-s per month in the free tier,
- 1024 MB, 30 s timeout per invocation.

`iam:CreateOpenIDConnectProvider` is denied by the org SCP, so GitHub Actions
cannot assume a deploy role without long-lived keys. CI therefore **builds and
publishes** the package; deploying is one command:

```bash
aws s3 cp function.zip s3://month-end-api-316658412595/function.zip
aws lambda update-function-code \
  --profile agent-toolkit --region ap-southeast-2 \
  --function-name month-end-api \
  --s3-bucket month-end-api-316658412595 --s3-key function.zip
```

The package must be built for Linux (`pip install --platform manylinux2014_x86_64`),
which is why it is produced in CI rather than on a Windows machine.

## Infrastructure as deployed

| Piece | Name |
|---|---|
| Function | `month-end-api` (python3.11, 1024 MB, 30 s) |
| URL | Function URL, `auth-type NONE`, guarded by the bearer token |
| Package | `s3://month-end-api-316658412595/function.zip` |
| Role | `month-end-api-lambda` (basic execution only) |
| Budget | `month-end-monthly-10` |
| Region | `ap-southeast-2` |
