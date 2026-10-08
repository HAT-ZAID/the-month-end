## Commits
ceefe2b feat: evaluate spending changes via category suppression

## Diffstat
 code/changes.py       | 41 +++++++++++++++++++----------------------
 tests/test_changes.py | 40 ++++++++++++++++++++++++++++++++++++++++
 2 files changed, 59 insertions(+), 22 deletions(-)

## Full diff (U10)
diff --git a/code/changes.py b/code/changes.py
index 5b34efb..f11a04d 100644
--- a/code/changes.py
+++ b/code/changes.py
@@ -45,30 +45,24 @@ def search_changes(
     events: list[dict],
     fx_rows: list[dict],
     recurrence_flags=None,
 ) -> list[str]:
     """Exhaustive search for the best set of spending changes.
 
     Priority:
     1. Fewest number of changes.
     2. Max total amount saved.
     """
-    # 1. Get all possible single-action candidates, filtered to permitted future
-    # recurring flexible expense events owned by this user.
-    from datetime import date, timedelta
-    try:
-        req_d = date.fromisoformat(str(request_date))
-        win_end = req_d + timedelta(days=90)
-    except Exception:
-        req_d = None
-        win_end = None
-
+    # 1. Get all possible single-action candidates, filtered to permitted
+    # recurring flexible expense/subscription events owned by this user.
+    # A change acts on the category's forward recurrence, not one historical
+    # row, so a past-dated event remains a valid target.
     def _categories(key: str) -> set[str]:
         raw = str(profile.get(key, "") or "")
         return {part.strip().lower() for part in raw.split("|")
                 if part.strip() and part.strip().lower() != "nan"}
 
     prot = _categories("expense_categories_to_protect")
     reduce_ok = _categories("expense_categories_user_is_willing_to_reduce")
     stop_ok = _categories("expense_categories_user_is_willing_to_stop")
     flags = set()
     for flag in recurrence_flags or ():
@@ -82,70 +76,73 @@ def search_changes(
         ev = next((x for x in events if x.get("event_id") == eid), None)
         if ev is None:
             continue
         event_user = str(ev.get("user_id", "") or "")
         event_type = str(ev.get("event_type", "") or "").strip().lower()
         cat_raw = str(ev.get("category", "") or "")
         cat = cat_raw.strip().lower()
         flex = str(ev.get("flexibility", "") or "").strip().lower()
         direction = str(ev.get("direction", "") or "").strip().lower()
         status = str(ev.get("status", "") or "").strip().lower()
-        if event_user != str(user_id) or event_type != "expense":
+        if event_user != str(user_id) or event_type not in ("expense", "subscription"):
             continue
         if (event_user, cat, event_type) not in flags:
             continue
         if direction != "debit" or status not in {"settled", "scheduled", "pending"}:
             continue
         if status == "pending" and direction != "debit":
             continue
         if flex not in ("stoppable", "reducible", "reducible_or_stoppable"):
             continue
         if cat in prot:
             continue
         if action == "stop" and cat not in stop_ok:
             continue
         if action == "reduce_to" and cat not in reduce_ok:
             continue
-        if req_d is not None:
-            sds = ev.get("settlement_date") or ev.get("event_date")
-            try:
-                ed = date.fromisoformat(str(sds))
-            except Exception:
-                continue
-            if not (req_d <= ed <= win_end):
-                continue
         candidates.append((eid, action, val))
 
     # 2. Try combinations of size 1, then 2, then 3
     for size in range(1, 4):
         safe_combinations = []
         for combo in combinations(candidates, size):
             # Spec :198 ΓÇö stop+reduce on the same event are mutually exclusive.
             if len({c[0] for c in combo}) != len(combo):
                 continue
-            # Apply combo to a copy of events
+            # Two mechanisms act on disjoint date sets:
+            # 1. Copy edit ΓÇö an in-window row of a targeted event is real
+            #    upcoming cash that a stop genuinely removes.
             modified_events = []
             for e in events:
                 e_copy = dict(e)
                 for eid, action, val in combo:
                     if e_copy["event_id"] == eid:
                         if action == "stop":
                             e_copy["amount"] = "0"
                         elif action == "reduce_to":
                             e_copy["amount"] = str(val)
                 modified_events.append(e_copy)
-
+            # 2. Suppression ΓÇö pre-request history sits outside the window, so
+            #    the copy edit is a no-op there; the oracle drops that
+            #    category's forward forecast instead.
+            cat_by_eid = {
+                str(e.get("event_id", "")): str(e.get("category", "") or "").strip().lower()
+                for e in events
+            }
+            suppressed = {cat_by_eid.get(eid, "") for eid, _action, _val in combo}
+            suppressed.discard("")
             # Check safety using the same recurrence evidence used for filtering.
             flags_to_use = recurrence_flags if recurrence_flags is not None else set()
             res = solve_safe_amount(
                 user_id, request_date, str(requested_amount),
-                profile, modified_events, fx_rows, flags_to_use
+                profile, modified_events, fx_rows, flags_to_use,
+                suppressed_categories=suppressed,
             )
             if res["safe_amount"] >= requested_amount:
                 # Calculate total savings by eid lookup (order-independent).
                 amt_by_eid: dict[str, Decimal] = {}
                 for e in events:
                     try:
                         amt_by_eid[e["event_id"]] = Decimal(str(e["amount"]))
                     except Exception:
                         continue
                 savings = sum(
diff --git a/tests/test_changes.py b/tests/test_changes.py
index 0328695..dce371c 100644
--- a/tests/test_changes.py
+++ b/tests/test_changes.py
@@ -156,10 +156,50 @@ def test_one_time_event_without_recurrence_evidence_is_rejected():
     events = [_exp("one_time", amt="4000")]
     assert search_changes("u1", "2026-01-01", Decimal("5000"), profile, events, [],
                           {("u1", "other", "expense")}) == []
 
 
 def test_cross_user_event_is_rejected_even_with_matching_recurrence_evidence():
     profile = _base_profile(expense_categories_user_is_willing_to_stop="dining")
     events = [_exp("other_user", amt="4000", user_id="u2")]
     assert search_changes("u1", "2026-01-01", Decimal("5000"), profile, events, [],
                           {("u2", "dining", "expense")}) == []
+
+
+def test_search_changes_stops_a_subscription_that_only_exists_in_history():
+    """Gold samples prove subscriptions are valid change targets even when every
+    historical row predates request_date."""
+    from decimal import Decimal
+    from code.changes import search_changes
+
+    profile = {
+        "home_currency": "USD",
+        "current_available_balance": "4600",
+        "minimum_balance_to_keep": "4000",
+        "expense_categories_to_protect": "rent",
+        "expense_categories_user_is_willing_to_stop": "streaming",
+        "expense_categories_user_is_willing_to_reduce": "",
+        "max_installment_months": "",
+    }
+    events = []
+    for i, month in enumerate(("2026-01", "2026-02", "2026-03")):
+        events.append({
+            "event_id": f"s{i}",
+            "user_id": "u1",
+            "event_type": "subscription",
+            "category": "streaming",
+            "direction": "debit",
+            "amount": "100",
+            "currency": "USD",
+            "event_date": f"{month}-05",
+            "settlement_date": f"{month}-05",
+            "status": "settled",
+            "linked_event_id": "",
+            "flexibility": "stoppable",
+            "minimum_allowed_amount": "0",
+        })
+    result = search_changes(
+        "u1", "2026-04-01", Decimal("600"), profile, events, [],
+        {("u1", "streaming", "subscription")},
+    )
+    assert result, "expected a stop action for a stoppable recurring subscription"
+    assert result[0].startswith("stop:")
