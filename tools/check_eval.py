"""Quick evaluation audit: check output against sample_requests and spec rules."""
import pandas as pd
from decimal import Decimal, InvalidOperation

samples = pd.read_csv('dataset/sample_requests.csv', dtype=str)
output = pd.read_csv('output.csv', dtype=str)
requests = pd.read_csv('dataset/requests.csv', dtype=str)

# 1. Completeness
print("=== 1. COMPLETENESS ===")
print(f"  Total requests: {len(requests)}")
print(f"  Output rows:    {len(output)}")
missing = set(requests['request_id']) - set(output['request_id'])
extra   = set(output['request_id']) - set(requests['request_id'])
print(f"  Missing IDs:    {missing}")
print(f"  Extra IDs:      {extra}")

# 2. Column order
print("\n=== 2. COLUMN ORDER ===")
expected_cols = ['request_id','amount_safe_to_pay','affordability_status',
                 'recommended_payment_method','payment_plan',
                 'earliest_date_for_full_payment','spending_changes_needed',
                 'decision_explanation']
actual_cols = list(output.columns)
print(f"  Match: {actual_cols == expected_cols}")
if actual_cols != expected_cols:
    print(f"  Expected: {expected_cols}")
    print(f"  Got:      {actual_cols}")

# 3. Allowed values
print("\n=== 3. ALLOWED VALUE VIOLATIONS ===")
valid_status = {'affordable_now','affordable_with_plan','affordable_later','not_affordable'}
valid_method = {'full_payment','partial_payment','installments','wait','not_recommended'}

bad_status = output[~output['affordability_status'].isin(valid_status)]
bad_method = output[~output['recommended_payment_method'].isin(valid_method)]
print(f"  Bad affordability_status: {len(bad_status)}")
if len(bad_status): print(bad_status[['request_id','affordability_status']].to_string())
print(f"  Bad recommended_payment_method: {len(bad_method)}")
if len(bad_method): print(bad_method[['request_id','recommended_payment_method']].to_string())

# 4. amount_safe_to_pay bounds: 0 <= amt <= requested_amount
print("\n=== 4. AMOUNT_SAFE_TO_PAY BOUNDS (0 <= safe <= requested) ===")
req_merged = requests[['request_id','requested_amount']].merge(output, on='request_id')
violations = []
for _, row in req_merged.iterrows():
    try:
        safe = Decimal(str(row['amount_safe_to_pay']).strip())
        req  = Decimal(str(row['requested_amount']).strip())
        if safe < 0 or safe > req:
            violations.append((row['request_id'], str(safe), str(req)))
    except Exception as e:
        violations.append((row['request_id'], row['amount_safe_to_pay'], f'PARSE_ERROR:{e}'))
print(f"  Violations: {len(violations)}")
for v in violations[:10]:
    print(f"    {v}")

# 5. earliest_date_for_full_payment = request_date when affordable_now
print("\n=== 5. EARLIEST_DATE == REQUEST_DATE WHEN AFFORDABLE_NOW ===")
affordable_now = output[output['affordability_status'] == 'affordable_now']
merged_now = requests[['request_id','request_date']].merge(affordable_now, on='request_id')
mismatch = merged_now[merged_now['earliest_date_for_full_payment'] != merged_now['request_date']]
print(f"  Mismatches: {len(mismatch)}")
if len(mismatch): print(mismatch[['request_id','request_date','earliest_date_for_full_payment']].to_string())

# 6. payment_plan = 'none' when not_recommended
print("\n=== 6. PAYMENT_PLAN CONSISTENCY ===")
not_rec = output[output['recommended_payment_method'] == 'not_recommended']
bad_plan_notrec = not_rec[not_rec['payment_plan'] != 'none']
print(f"  not_recommended but plan != 'none': {len(bad_plan_notrec)}")

# 7. Status/method consistency
print("\n=== 7. STATUS / METHOD CONSISTENCY ===")
# affordable_now → full_payment
an = output[output['affordability_status'] == 'affordable_now']
an_bad = an[an['recommended_payment_method'] != 'full_payment']
print(f"  affordable_now but method != full_payment: {len(an_bad)}")
if len(an_bad): print(an_bad[['request_id','recommended_payment_method']].to_string())

# affordable_later → wait
al = output[output['affordability_status'] == 'affordable_later']
al_bad = al[al['recommended_payment_method'] != 'wait']
print(f"  affordable_later but method != wait: {len(al_bad)}")

# not_affordable → not_recommended
na = output[output['affordability_status'] == 'not_affordable']
na_bad = na[na['recommended_payment_method'] != 'not_recommended']
print(f"  not_affordable but method != not_recommended: {len(na_bad)}")
if len(na_bad): print(na_bad[['request_id','recommended_payment_method']].to_string())

# 8. Status distribution
print("\n=== 8. AFFORDABILITY DISTRIBUTION ===")
print(output['affordability_status'].value_counts().to_string())
print("\n=== 9. PAYMENT METHOD DISTRIBUTION ===")
print(output['recommended_payment_method'].value_counts().to_string())

# 10. Compare with sample_requests for overlap
print("\n=== 10. SAMPLE vs OUTPUT COMPARISON ===")
# samples may have same request_ids
sample_ids = set(samples['request_id'])
output_ids = set(output['request_id'])
common = sample_ids & output_ids
print(f"  Samples in output: {len(common)} / {len(sample_ids)}")
if common:
    merged_sample = samples[samples['request_id'].isin(common)].merge(
        output[output['request_id'].isin(common)], on='request_id', suffixes=('_gold','_pred'))
    exact_matches = 0
    field_match = {f: 0 for f in ['affordability_status','recommended_payment_method','payment_plan','spending_changes_needed']}
    for _, row in merged_sample.iterrows():
        match = True
        for f in field_match:
            gold_val = str(row.get(f+'_gold', '')).strip()
            pred_val = str(row.get(f+'_pred', '')).strip()
            if gold_val == pred_val:
                field_match[f] += 1
            else:
                match = False
        if match:
            exact_matches += 1
    print(f"  Exact full-row matches: {exact_matches} / {len(merged_sample)}")
    for f, cnt in field_match.items():
        print(f"    {f}: {cnt}/{len(merged_sample)} match")

# 11. Spending changes format check
print("\n=== 11. SPENDING_CHANGES FORMAT CHECK ===")
non_none_changes = output[output['spending_changes_needed'] != 'none']
print(f"  Rows with spending changes: {len(non_none_changes)}")
bad_format = 0
for _, row in non_none_changes.iterrows():
    parts = str(row['spending_changes_needed']).split('|')
    if len(parts) > 3:
        bad_format += 1
        continue
    for p in parts:
        p = p.strip()
        if not (p.startswith('stop:') or p.startswith('reduce_to:')):
            bad_format += 1
            break
print(f"  Bad format: {bad_format}")
