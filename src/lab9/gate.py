"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, aggregate, load_spec, per_item
from lab9.graders import GRADERS


def _is_bad_number(v: Any) -> bool:
    return v is None or isinstance(v, bool) or (isinstance(v, (int, float)) and (math.isnan(v) or math.isinf(v)))


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems: list[str] = []

    for rname, rep in (("PR", pr), ("baseline", base)):
        if rep.get("evaluator_failures"):
            problems.append(f"{rname} has evaluator_failures: {rep['evaluator_failures'][:1]}")
        manifest = rep.get("manifest") or {}
        n_items = int(manifest.get("items") or 0)
        seeds = list(manifest.get("seeds") or [])
        rows = rep.get("rows") or []
        if len(rows) != n_items * len(seeds):
            problems.append(f"{rname} row count {len(rows)} != manifest items ({n_items}) x seeds ({len(seeds)})")
        pairs = [(r.get("item_id"), r.get("seed")) for r in rows]
        if len(set(pairs)) != len(pairs):
            problems.append(f"{rname} contains duplicate (item_id, seed) rows")
        for mk, mv in (rep.get("metrics") or {}).items():
            if _is_bad_number(mv):
                problems.append(f"{rname} metric {mk!r} is NaN, inf, or None")

    pr_rows = pr.get("rows") or []
    base_rows = base.get("rows") or []
    pr_pairs = [(r.get("item_id"), r.get("seed")) for r in pr_rows]
    base_pairs = [(r.get("item_id"), r.get("seed")) for r in base_rows]
    if set(pr_pairs) != set(base_pairs) or len(pr_pairs) != len(base_pairs):
        problems.append("PR (item_id, seed) set or count differs from baseline")

    pr_manifest = pr.get("manifest") or {}
    base_manifest = base.get("manifest") or {}
    if pr_manifest.get("items") != base_manifest.get("items") or list(pr_manifest.get("seeds") or []) != list(base_manifest.get("seeds") or []):
        problems.append("PR manifest items/seeds differ from baseline manifest")

    spec_metrics = spec.get("metrics") or []
    row_keys = set(pr_rows[0].keys()) | set(base_rows[0].keys()) if (pr_rows and base_rows) else set()
    graded_ids = [
        m["id"] for m in spec_metrics
        if m["id"] in GRADERS or (m["id"] in row_keys and m["id"] not in ("item_id", "seed", "slices", "cost"))
    ]

    for rname, rows in (("PR", pr_rows), ("baseline", base_rows)):
        for r in rows:
            for mid in graded_ids:
                val = r.get(mid)
                if val is None or (isinstance(val, float) and (math.isnan(val) or math.isinf(val))):
                    problems.append(f"{rname} row {r.get('item_id')}@{r.get('seed')} has missing/NaN score for {mid!r}")
                    break

    # Verify slice tags match baseline per item_id and are consistent across seeds
    base_slices: dict[Any, list[str]] = {}
    for r in base_rows:
        iid = r.get("item_id")
        stags = sorted(r.get("slices") or [])
        if iid in base_slices and base_slices[iid] != stags:
            problems.append(f"baseline item {iid} has inconsistent slice tags across seeds")
        base_slices[iid] = stags
    for r in pr_rows:
        iid = r.get("item_id")
        stags = sorted(r.get("slices") or [])
        if iid in base_slices and base_slices[iid] != stags:
            problems.append(f"PR item {iid} slice tags {stags} differ from baseline {base_slices[iid]}")
            break

    # Verify summary metrics in PR match recomputed values from rows when rows have all graded metrics
    if pr_rows and not problems:
        row_metric_ids = [m["id"] for m in spec_metrics if m["id"] in pr_rows[0]]
        if row_metric_ids:
            safe_rows = [{**r, "cost": float(r.get("cost") or 0.0)} for r in pr_rows]
            recomputed = aggregate(safe_rows, row_metric_ids, k_values=spec.get("pass_k", []))
            for mk, rval in recomputed.items():
                if mk == "cost_per_success" and "cost" not in pr_rows[0]:
                    continue
                if mk in (pr.get("metrics") or {}) and rval is not None:
                    pval = pr["metrics"][mk]
                    if isinstance(pval, (int, float)) and not math.isnan(pval) and not math.isinf(pval):
                        if abs(float(pval) - float(rval)) > 1e-3:
                            problems.append(f"PR metric {mk}={pval} does not match rows aggregate {rval}")

    # Ratchet check when base_spec is given and on the same eval_set_version
    if base_spec is not None and spec.get("eval_set_version") == base_spec.get("eval_set_version"):
        cur_m = {m["id"]: m for m in (spec.get("metrics") or [])}
        for bm in base_spec.get("metrics") or []:
            mid = bm["id"]
            if mid not in cur_m:
                problems.append(f"ratchet: metric {mid!r} removed from spec")
                continue
            cm = cur_m[mid]
            if bm.get("blocking") and not cm.get("blocking"):
                problems.append(f"ratchet: metric {mid!r} changed from blocking to non-blocking")
            bth = bm.get("threshold") or {}
            cth = cm.get("threshold") or {}
            if "min" in bth:
                if "min" not in cth or cth["min"] < bth["min"]:
                    problems.append(f"ratchet: metric {mid!r} min threshold lowered ({cth.get('min')} < {bth['min']})")
            if "max" in bth:
                if "max" not in cth or cth["max"] > bth["max"]:
                    problems.append(f"ratchet: metric {mid!r} max threshold raised ({cth.get('max')} > {bth['max']})")
            if "max_increase" in bth:
                if "max_increase" not in cth or cth["max_increase"] > bth["max_increase"]:
                    problems.append(f"ratchet: metric {mid!r} max_increase raised ({cth.get('max_increase')} > {bth['max_increase']})")
            if bth.get("no_significant_drop") and not cth.get("no_significant_drop"):
                problems.append(f"ratchet: metric {mid!r} dropped no_significant_drop")

        cur_s = {(s.get("name"), s.get("metric")): s for s in (spec.get("slices") or [])}
        for bs_item in base_spec.get("slices") or []:
            skey = (bs_item.get("name"), bs_item.get("metric"))
            if skey not in cur_s:
                problems.append(f"ratchet: slice {skey[0]}/{skey[1]} removed from spec")
                continue
            cs_item = cur_s[skey]
            if bs_item.get("blocking") and not cs_item.get("blocking"):
                problems.append(f"ratchet: slice {skey[0]}/{skey[1]} changed from blocking to non-blocking")
            if "min" in bs_item:
                if "min" not in cs_item or cs_item["min"] < bs_item["min"]:
                    problems.append(f"ratchet: slice {skey[0]}/{skey[1]} min lowered ({cs_item.get('min')} < {bs_item['min']})")
            if "max" in bs_item:
                if "max" not in cs_item or cs_item["max"] > bs_item["max"]:
                    problems.append(f"ratchet: slice {skey[0]}/{skey[1]} max raised ({cs_item.get('max')} > {bs_item['max']})")
            if bs_item.get("no_significant_drop") and not cs_item.get("no_significant_drop"):
                problems.append(f"ratchet: slice {skey[0]}/{skey[1]} dropped no_significant_drop")

    return problems


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked: list[str] = []
    lines: list[str] = []
    bs = spec.get("bootstrap", {})
    for s in spec.get("slices") or []:
        sname = s["name"]
        mid = s["metric"]
        sid = f"slice:{sname}/{mid}"
        a = per_item(pr, mid, slice_name=sname)
        b = per_item(base, mid, slice_name=sname)
        val = round(sum(a.values()) / len(a), 4) if a else None
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        else:
            if "min" in s and val < s["min"]:
                verdict = "fail"
            if "max" in s and val > s["max"]:
                verdict = "fail"
            if s.get("no_significant_drop"):
                keys = sorted(set(a) & set(b))
                if keys:
                    d, lo, hi = stats.paired_bootstrap(
                        [a[k] for k in keys],
                        [b[k] for k in keys],
                        n_resamples=bs.get("n_resamples", 1000),
                        rng=bs.get("rng", 2027),
                    )
                    note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                    if hi < 0:
                        verdict = "fail"
        if verdict != "ok" and s.get("blocking"):
            blocked.append(sid)
        th_view = {k: s[k] for k in ("min", "max", "no_significant_drop") if k in s}
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if s.get("blocking") else "warn")
        lines.append(f"  {sid:<22} {val!s:<8} {json.dumps(th_view):<40} {note:<28} {label}")
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
