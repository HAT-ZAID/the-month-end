## Commits
4faecd3 feat: forecast flexible recurring categories with category suppression

## Diffstat
 code/oracle.py               | 100 +++++++++++++++++++++++++------------------
 tests/test_oracle_changes.py |  94 ++++++++++++++++++++++++++++++++++++++++
 2 files changed, 152 insertions(+), 42 deletions(-)

## Full diff (U10)
diff --git a/code/oracle.py b/code/oracle.py
index d7d2024..769c2c5 100644
--- a/code/oracle.py
+++ b/code/oracle.py
@@ -130,21 +130,22 @@ def _median_interval(intervals: list[int]) -> int:
     mid = n // 2
     if n % 2 == 1:
         return s[mid]
     # Even-n median uses integer floor division (//2): a .5 average is floored
     # to a whole day (conservative timing kept; no behavior change here).
     return (s[mid - 1] + s[mid]) // 2
 
 
 def _history_eligible(e: dict, for_expense: bool) -> bool:
     if for_expense:
-        if str(e.get("event_type", "") or "").strip().lower() != "expense":
+        # Subscriptions are recurring spending: count them as expense history.
+        if str(e.get("event_type", "") or "").strip().lower() not in ("expense", "subscription"):
             return False
     else:
         if str(e.get("event_type", "") or "").strip().lower() != "income":
             return False
     status = _norm_status(e)
     d = _norm_dir(e)
     if d == "non_cash":
         return False
     if status not in ("settled", "scheduled", "pending"):
         return False
@@ -156,20 +157,21 @@ def _history_eligible(e: dict, for_expense: bool) -> bool:
 
 
 def solve_safe_amount(
     user_id: str,
     request_date_str: str,
     requested_amount_str: str,
     profile: dict,
     events: list[dict],
     fx_rows: list[dict],
     recurrence_flags: set[tuple[str, str, str]],
+    suppressed_categories: set[str] | None = None,
 ) -> dict:
     req_date = date.fromisoformat(request_date_str)
 
     try:
         req_amt = Decimal(str(requested_amount_str))
     except (InvalidOperation, TypeError):
         req_amt = Decimal("0")
 
     try:
         min_bal = Decimal(str(profile.get("minimum_balance_to_keep", "0")))
@@ -190,70 +192,79 @@ def solve_safe_amount(
         prot_s = str(prot_raw).strip()
         if prot_s and prot_s.lower() != "nan":
             for part in prot_s.split("|"):
                 p = part.strip()
                 if not p:
                     continue
                 if p.lower() == "nan":
                     continue
                 protected_cats.add(p)
 
+    # Normalize suppressed categories: strip, drop empties, case-fold.
+    suppressed = {
+        str(c or "").strip().lower()
+        for c in (suppressed_categories or set())
+        if str(c or "").strip()
+    }
+
     # 1. Filter and normalize events for the 90-day window [req_date, req_date + 90]
     window_end = req_date + timedelta(days=90)
     # Hoisted constants (perf): single user filter, single window bound.
     user_events_raw = [e for e in events if str(e.get("user_id", "") or "") == str(user_id)]
     # Dedup before the window loop: collapse event_id + linked_event_id retries.
     user_events = _dedup_events(user_events_raw)
 
-    # Identify recurring protected essential categories
-    protected_recurring_cats = set()
+    # Identify recurring expense categories (protected AND flexible: a spending
+    # change only matters if the category is forecast at all) + income ones.
+    recurring_expense_cats = set()
     recurring_income_cats: set[str] = set()
     for e in user_events:
         cat = e.get("category")
-        if cat in protected_cats and str(e.get("event_type", "") or "").strip().lower() == "expense":
+        et = str(e.get("event_type", "") or "").strip().lower()
+        if et in ("expense", "subscription"):
             key = (str(e.get("user_id", "") or ""), cat, str(e.get("event_type", "") or ""))
             if key in recurrence_flags:
-                protected_recurring_cats.add(cat)
-        if str(e.get("event_type", "") or "").strip().lower() == "income":
+                recurring_expense_cats.add(cat)
+        if et == "income":
             key = (str(e.get("user_id", "") or ""), cat, str(e.get("event_type", "") or ""))
             if key in recurrence_flags:
                 recurring_income_cats.add(cat)
 
-    # Build frequency map for protected recurring categories + recurring income
+    # Build frequency map for recurring expense categories + recurring income
     # Only countable history (same event_type, settled/scheduled +pending-debit for expenses).
-    protected_cat_freq = {}
+    recurring_expense_freq = {}
     income_cat_freq: dict[str, list[date]] = {}
     for e in user_events:
         cat = e.get("category")
-        if cat in protected_recurring_cats or cat in recurring_income_cats:
-            is_prot = cat in protected_recurring_cats
+        if cat in recurring_expense_cats or cat in recurring_income_cats:
+            is_exp = cat in recurring_expense_cats
             # Require same event_type as the forecast kind.
-            if is_prot and not _history_eligible(e, for_expense=True):
-                # Allow income cats to still be checked below; skip prot-only.
+            if is_exp and not _history_eligible(e, for_expense=True):
+                # Allow income cats to still be checked below; skip expense-only.
                 if cat not in recurring_income_cats or not _history_eligible(e, for_expense=False):
                     continue
-            if cat in recurring_income_cats and cat not in protected_recurring_cats:
+            if cat in recurring_income_cats and cat not in recurring_expense_cats:
                 if not _history_eligible(e, for_expense=False):
                     continue
-            if cat in protected_recurring_cats and cat in recurring_income_cats:
+            if cat in recurring_expense_cats and cat in recurring_income_cats:
                 # Dual-category: keep row for each kind it is eligible for.
-                prot_ok = _history_eligible(e, for_expense=True)
+                exp_ok = _history_eligible(e, for_expense=True)
                 inc_ok = _history_eligible(e, for_expense=False)
-                if not prot_ok and not inc_ok:
+                if not exp_ok and not inc_ok:
                     continue
             s_date_str = e.get("settlement_date") or e.get("event_date")
             if s_date_str and isinstance(s_date_str, str):
                 try:
                     e_date = date.fromisoformat(s_date_str)
                     if e_date < req_date:
-                        if cat in protected_recurring_cats and _history_eligible(e, for_expense=True):
-                            protected_cat_freq.setdefault(cat, []).append(e_date)
+                        if cat in recurring_expense_cats and _history_eligible(e, for_expense=True):
+                            recurring_expense_freq.setdefault(cat, []).append(e_date)
                         if cat in recurring_income_cats and _history_eligible(e, for_expense=False):
                             income_cat_freq.setdefault(cat, []).append(e_date)
                 except ValueError:
                     pass
 
     processed_events = []
     for e in user_events:
         status = _norm_status(e)
         dir_norm = _norm_dir(e)
         s_date_str = e.get("settlement_date") or e.get("event_date")
@@ -297,59 +308,64 @@ def solve_safe_amount(
                 continue
 
             multiplier = -1 if dir_norm == "debit" else 1
             processed_events.append({
                 "date": e_date,
                 "delta": amt * multiplier,
                 "flag": fx_res["flag"],
                 "category": e.get("category"),
             })
 
-    # Add forecasted protected recurring expenses and incomes for the 90ΓÇæday window.
+    # Add forecasted recurring expenses and incomes for the 90ΓÇæday window.
     # Median intervals (arrears-outlier robust); require >=2 pre-request dates;
     # Decimal averages over settled/scheduled (+pending+debit for expenses), same event_type.
-    for cat in sorted(protected_recurring_cats):
-        dates = protected_cat_freq.get(cat, [])
+    for cat in sorted(recurring_expense_cats):
+        dates = recurring_expense_freq.get(cat, [])
         if len(dates) < 2:
             continue
         sorted_dates = sorted(dates)
         intervals = [(sorted_dates[i+1] - sorted_dates[i]).days for i in range(len(sorted_dates)-1)]
         if not intervals:
             continue
         avg_interval = _median_interval(intervals)
         if avg_interval <= 0:
             avg_interval = 30
 
         cat_events = [e for e in user_events
                       if e.get("category") == cat and _history_eligible(e, for_expense=True)]
-        cat_amounts: list[Decimal] = []
-        for ce in cat_events:
-            d = _amount_abs(ce)
-            if d is None:
-                continue
-            # FX: history was averaged in raw units while in-window events are
-            # FX-converted; convert each history amount at its own settlement date.
-            # Choice: on missing rate skip that observation (don't invent a rate,
-            # don't pollute the average with raw foreign units).
-            s_hist = ce.get("settlement_date") or ce.get("event_date")
-            if not s_hist or not isinstance(s_hist, str):
-                continue
-            fx_hist = resolve_rate(fx_rows, ce.get("currency"), home_ccy, s_hist)
-            if fx_hist["rate"] is None:
+        if cat in suppressed:
+            # A spending change stops this category going forward. Historical
+            # amounts stay in the ledger; only future forecast occurrences vanish.
+            avg_amount = Decimal("0")
+        else:
+            cat_amounts: list[Decimal] = []
+            for ce in cat_events:
+                d = _amount_abs(ce)
+                if d is None:
+                    continue
+                # FX: history was averaged in raw units while in-window events are
+                # FX-converted; convert each history amount at its own settlement date.
+                # Choice: on missing rate skip that observation (don't invent a rate,
+                # don't pollute the average with raw foreign units).
+                s_hist = ce.get("settlement_date") or ce.get("event_date")
+                if not s_hist or not isinstance(s_hist, str):
+                    continue
+                fx_hist = resolve_rate(fx_rows, ce.get("currency"), home_ccy, s_hist)
+                if fx_hist["rate"] is None:
+                    continue
+                d_home = d * fx_hist["rate"]
+                # Recurring expenses forecast the full debit average: use -abs(d_home)
+                # so a refund/credit row cannot lower (optimistically shrink) the forecast.
+                cat_amounts.append(-d_home)
+            if not cat_amounts:
                 continue
-            d_home = d * fx_hist["rate"]
-            # Protected expenses forecast the full debit average: use -abs(d_home)
-            # so a refund/credit row cannot lower (optimistically shrink) the forecast.
-            cat_amounts.append(-d_home)
-        if not cat_amounts:
-            continue
-        avg_amount = sum(cat_amounts, Decimal("0")) / Decimal(len(cat_amounts))
+            avg_amount = sum(cat_amounts, Decimal("0")) / Decimal(len(cat_amounts))
         # Determine first forecast date by rolling forward from last history date
         last_date = max(dates)
         next_date = last_date + timedelta(days=avg_interval)
         while next_date < req_date:
             next_date += timedelta(days=avg_interval)
 
         while next_date <= window_end:
             if not any(e.get("category") == cat and e["date"] == next_date for e in processed_events):
                 processed_events.append({
                     "date": next_date,
diff --git a/tests/test_oracle_changes.py b/tests/test_oracle_changes.py
new file mode 100644
index 0000000..fa194ed
--- /dev/null
+++ b/tests/test_oracle_changes.py
@@ -0,0 +1,94 @@
+from code.oracle import solve_safe_amount
+
+
+PROFILE = {
+    "home_currency": "USD",
+    "current_available_balance": "10000",
+    "minimum_balance_to_keep": "1000",
+    "expense_categories_to_protect": "rent",
+    "expense_categories_user_is_willing_to_stop": "streaming",
+    "expense_categories_user_is_willing_to_reduce": "",
+    "max_installment_months": "",
+}
+
+# Recurrence evidence for a stoppable subscription. Production obtains this from
+# code.recurrence.detect_recurrence; tests pin it so they do not depend on that
+# detector's internals.
+FLAGS = {("u1", "streaming", "subscription")}
+
+
+def _streaming(month: str) -> dict:
+    return {
+        "event_id": f"stream_{month}",
+        "user_id": "u1",
+        "event_type": "subscription",
+        "category": "streaming",
+        "direction": "debit",
+        "amount": "100",
+        "currency": "USD",
+        "event_date": f"{month}-05",
+        "settlement_date": f"{month}-05",
+        "status": "settled",
+        "linked_event_id": "",
+        "flexibility": "stoppable",
+        "minimum_allowed_amount": "0",
+    }
+
+
+def _history():
+    # Four monthly observations so the median interval is 30 days and at least
+    # two forecast occurrences land inside [request_date, request_date+90].
+    return [_streaming(m) for m in ("2025-10", "2025-11", "2025-12", "2026-01")]
+
+
+def test_baseline_forecasts_flexible_recurring_category():
+    events = _history()
+    base = solve_safe_amount("u1", "2026-02-01", "9900", PROFILE, events, [], FLAGS)
+    # Without the fix the flexible subscription is never forecast, so the ledger
+    # is flat at the starting balance.
+    assert min(base["ledger"]) < 10000
+
+
+def test_suppressing_flexible_category_raises_safe_amount():
+    events = _history()
+    base = solve_safe_amount("u1", "2026-02-01", "9900", PROFILE, events, [], FLAGS)
+    stopped = solve_safe_amount(
+        "u1", "2026-02-01", "9900", PROFILE, events, [],
+        FLAGS, suppressed_categories={"streaming"},
+    )
+    assert min(stopped["ledger"]) > min(base["ledger"])
+    assert stopped["safe_amount"] >= base["safe_amount"]
+    # Suppressing the category removes every forecast occurrence of it.
+    assert min(stopped["ledger"]) == 10000
+
+
+def test_suppression_preserves_ledger_shape_and_day_zero():
+    events = _history()
+    base = solve_safe_amount("u1", "2026-02-01", "9900", PROFILE, events, [], FLAGS)
+    stopped = solve_safe_amount(
+        "u1", "2026-02-01", "9900", PROFILE, events, [],
+        FLAGS, suppressed_categories={"streaming"},
+    )
+    assert len(stopped["ledger"]) == 91
+    assert len(base["ledger"]) == 91
+    assert base["ledger"][0] == stopped["ledger"][0]
+
+
+def test_no_suppression_matches_default_behavior():
+    events = _history()
+    explicit_none = solve_safe_amount(
+        "u1", "2026-02-01", "9900", PROFILE, events, [], FLAGS, suppressed_categories=None
+    )
+    default = solve_safe_amount("u1", "2026-02-01", "9900", PROFILE, events, [], FLAGS)
+    assert explicit_none["safe_amount"] == default["safe_amount"]
+    assert explicit_none["ledger"] == default["ledger"]
+
+
+def test_unknown_suppressed_category_is_ignored():
+    events = _history()
+    default = solve_safe_amount("u1", "2026-02-01", "9900", PROFILE, events, [], FLAGS)
+    ignored = solve_safe_amount(
+        "u1", "2026-02-01", "9900", PROFILE, events, [],
+        FLAGS, suppressed_categories={"no_such_category", ""},
+    )
+    assert ignored["ledger"] == default["ledger"]
