# Jev decision gate vs the deterministic kernel

- endpoint: `https://opencode.ai/inference/systemone/v1/systemone`
- model: `jev-1.13-free`
- requests asked: 25, answered: 25, errors: 0

Machine fields are provably untouched: the gate reads the solved rows and
writes nothing back.  Every number below is a *comparison*, not a source.

| request | kernel status | Jev status (noul) | match | kernel method | Jev method (noul) | match | safety (noul, lower-better) | evidence (noul) | tokens |
|---|---|---|---|---|---|---|---|---|---|
| request_01 | not_affordable | not_affordable (0.54) | yes | not_recommended | wait (0.57) | no | 0.65 | 0.43 | in=1463 out=228 |
| request_02 | not_affordable | not_affordable (0.61) | yes | not_recommended | wait (0.65) | no | 0.53 | 0.4 | in=1409 out=228 |
| request_03 | not_affordable | not_affordable (0.61) | yes | not_recommended | not_recommended (0.62) | yes | 0.57 | 0.35 | in=1402 out=228 |
| request_04 | not_affordable | not_affordable (0.71) | yes | not_recommended | not_recommended (0.78) | yes | 0.19 | 0.33 | in=1248 out=228 |
| request_05 | not_affordable | not_affordable (0.94) | yes | not_recommended | not_recommended (0.94) | yes | 0.1 | 0.56 | in=1334 out=228 |
| request_06 | affordable_later | affordable_later (0.85) | yes | wait | wait (0.81) | yes | 0.81 | 0.41 | in=1366 out=228 |
| request_07 | affordable_with_plan | affordable_with_plan (0.91) | yes | installments | installments (0.84) | yes | 0.9 | 0.54 | in=1422 out=228 |
| request_08 | not_affordable | not_affordable (0.92) | yes | not_recommended | not_recommended (0.91) | yes | 0.3 | 0.43 | in=1212 out=228 |
| request_09 | affordable_now | affordable_now (0.96) | yes | full_payment | full_payment (0.95) | yes | 0.95 | 0.74 | in=1346 out=228 |
| request_10 | not_affordable | not_affordable (0.62) | yes | not_recommended | not_recommended (0.68) | yes | 0.19 | 0.32 | in=1236 out=228 |
| request_11 | affordable_later | affordable_later (0.86) | yes | wait | wait (0.77) | yes | 0.86 | 0.47 | in=1546 out=228 |
| request_12 | affordable_with_plan | affordable_with_plan (0.88) | yes | installments | installments (0.64) | yes | 0.92 | 0.55 | in=1426 out=228 |
| request_13 | not_affordable | not_affordable (0.81) | yes | not_recommended | not_recommended (0.78) | yes | 0.72 | 0.5 | in=1323 out=228 |
| request_14 | not_affordable | not_affordable (0.93) | yes | not_recommended | not_recommended (0.91) | yes | 0.68 | 0.41 | in=1232 out=228 |
| request_15 | not_affordable | not_affordable (0.97) | yes | not_recommended | not_recommended (0.96) | yes | 0.12 | 0.48 | in=1322 out=228 |
| request_16 | affordable_now | affordable_now (0.91) | yes | full_payment | full_payment (0.91) | yes | 0.87 | 0.63 | in=1372 out=228 |
| request_17 | affordable_with_plan | affordable_with_plan (0.88) | yes | installments | installments (0.75) | yes | 0.87 | 0.53 | in=1453 out=228 |
| request_18 | affordable_later | affordable_later (0.75) | yes | wait | wait (0.75) | yes | 0.52 | 0.31 | in=1246 out=228 |
| request_19 | affordable_with_plan | affordable_with_plan (0.88) | yes | partial_payment | partial_payment (0.83) | yes | 0.87 | 0.46 | in=1412 out=228 |
| request_20 | not_affordable | not_affordable (0.96) | yes | not_recommended | not_recommended (0.94) | yes | 0.5 | 0.58 | in=1225 out=228 |
| request_21 | not_affordable | not_affordable (0.8) | yes | not_recommended | not_recommended (0.83) | yes | 0.55 | 0.49 | in=1469 out=228 |
| request_22 | not_affordable | not_affordable (0.71) | yes | not_recommended | not_recommended (0.68) | yes | 0.59 | 0.4 | in=1327 out=228 |
| request_23 | not_affordable | not_affordable (0.83) | yes | not_recommended | not_recommended (0.87) | yes | 0.2 | 0.37 | in=1349 out=228 |
| request_24 | not_affordable | not_affordable (0.93) | yes | not_recommended | not_recommended (0.93) | yes | 0.32 | 0.42 | in=1219 out=228 |
| request_25 | not_affordable | not_affordable (0.89) | yes | not_recommended | not_recommended (0.81) | yes | 0.44 | 0.43 | in=1372 out=228 |

## Summary

- status agreement: 25/25 answered (25 asked)
- method agreement: 23/25 answered
- input tokens: 33731, output tokens: 5700, cost: 0.0
- mean safety noul: 0.569
- mean evidence score: 0.462

## Reading this

The free decision model answers only `noul` propositions - the probability
that a stated fact is true - and refuses choice/score questions with
422 "Endpoint is unavailable".  So the gate asks one noul question per
candidate value (`status_is_affordable_now`, `method_is_wait`, ...) and the
"Jev status/method" columns are the argmax of those probabilities, with
the winning probability in parentheses.

A `noul` near 0 for safety means Jev sees no plausible way the recorded
decision violates the minimum-balance rule; a high value flags the row
for a human.  Method/status mismatches are not errors in either side:
they are where a typed System One model and a rule-following kernel
disagree, and are the starting point for the next kernel calibration
round.  `output.csv` was not touched.

