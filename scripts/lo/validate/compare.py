"""
Compare CSV outputs of hist_auto_gt_V5.py with the l1b_bgrates + l1b_goodtimes CDFs.

Files compared
--------------
imap_lo_HO_cnts_expo_*.csv  (csv_path, required)
  begin          -> gt_start_met       [goodtimes CDF]
  end            -> gt_end_met         [goodtimes CDF]
  h_proxy_counts -> h_proxy_floor      [bgrates CDF]  (last row = grand total)
  o_proxy_counts -> o_proxy_floor      [bgrates CDF]  (last row = grand total)
  proxy_exposure -> derived from h/o_synthetic_floor  (no direct CDF variable)
  pivot          -> pivot              [goodtimes CDF]
  pivot_de       -> pivot_de           [goodtimes CDF]

imap_lo_goodtimes_*.csv  (auto-discovered)
  begin          -> gt_start_met       [goodtimes CDF]  (same print_lines call as HO_cnts_expo)
  end            -> gt_end_met         [goodtimes CDF]

imap_lo_H_background_*.csv  (auto-discovered)
  rate row  (cols 7-13) -> h_background_rates     [bgrates CDF]
  sigma row (cols 7-13) -> h_background_variance  [bgrates CDF]
  begin/end timing (first_begin-120 / last_end-270) has no CDF equivalent

imap_lo_O_background_*.csv  (auto-discovered)
  rate row  (cols 7-13) -> o_background_rates     [bgrates CDF]
  sigma row (cols 7-13) -> o_background_variance  [bgrates CDF]
  begin/end timing has no CDF equivalent
"""


import re
from pathlib import Path
from datetime import datetime
import cdflib
import numpy as np
import pandas as pd

from imap_processing.lo.constants import LoConstants


HO_COLS = [
    "date", "begin", "end", "bin_lo", "bin_hi", "instrument",
    "h_proxy_counts", "o_proxy_counts", "proxy_exposure", "pivot", "pivot_de",
]

GT_COLS = [
    "date", "begin", "end", "bin_lo", "bin_hi", "instrument",
    "flag1", "flag2", "flag3", "flag4", "flag5", "flag6", "flag7", "comment",
]

BG_COLS = [
    "date", "begin", "end", "bin_lo", "bin_hi", "instrument",
    "esa1", "esa2", "esa3", "esa4", "esa5", "esa6", "esa7", "tag",
]

# Friendly names matching the CDF variable -> CSV column pairing for each tag
_ESA_COLS = ["esa1", "esa2", "esa3", "esa4", "esa5", "esa6", "esa7"]


def load_csv(path: str) -> pd.DataFrame:
    return pd.read_csv(path, header=None, names=HO_COLS)


def load_gt_csv(path: str) -> pd.DataFrame:
    return pd.read_csv(path, header=None, names=GT_COLS)


def load_bg_csv(path: str) -> pd.DataFrame:
    return pd.read_csv(path, header=None, names=BG_COLS)


def load_cdf(path: str) -> dict:
    cdf = cdflib.CDF(path)
    info = cdf.cdf_info()
    data = {v: cdf.varget(v) for v in info.zVariables + info.rVariables}
    return data


def _scalar(x) -> float:
    return float(np.atleast_1d(np.asarray(x, dtype=float)).ravel()[0])


def _array(x) -> np.ndarray:
    return np.atleast_1d(np.asarray(x, dtype=float)).ravel()


def find_sibling_cdf(bgrates_path: str, tag: str) -> Path | None:
    candidate = Path(str(bgrates_path).replace("bgrates", tag))
    return candidate if candidate.exists() else None


def find_sibling_csv(ho_cnts_path: str, prefix: str) -> Path | None:
    """Find a sibling CSV in the same directory sharing the YYYYDDD date string."""
    p = Path(ho_cnts_path)
    m = re.search(r"(\d{7})", p.name)
    if m:
        candidate = p.parent / f"{prefix}_{m.group(1)}.csv"
        return candidate if candidate.exists() else None
    return None


def check(label: str, csv_val, cdf_val, rtol: float, atol: float) -> bool:
    csv_arr = _array(csv_val)
    cdf_arr = _array(cdf_val)
    if csv_arr.shape != cdf_arr.shape:
        print(f"  [MISMATCH] {label}")
        print(f"             shape: CSV={csv_arr.shape}, CDF={cdf_arr.shape}")
        return False
    ok = bool(np.allclose(csv_arr, cdf_arr, rtol=rtol, atol=atol))
    if ok:
        print(f"  [OK]       {label}")
    else:
        diff = np.abs(csv_arr - cdf_arr)
        rel = diff / (np.abs(cdf_arr) + 1e-30)
        print(f"  [MISMATCH] {label}")
        print(f"             CSV : {csv_arr}")
        print(f"             CDF : {cdf_arr}")
        print(f"             max |diff|={diff.max():.4g}  max rel={rel.max():.4g}")
    return ok


def compare_data(csv_path: Path, bgrates_cdf_path: Path, rtol: float = 1e-4, atol: float = 1e-6, current: datetime | None = None) -> bool:
    df = load_csv(str(csv_path))
    if df.empty:
        print("why?")
    bg = load_cdf(str(bgrates_cdf_path))

    gt_path = find_sibling_cdf(str(bgrates_cdf_path), "goodtimes")
    gt = load_cdf(str(gt_path)) if gt_path else None

    # Auto-discover sibling CSV files from the same output directory
    gt_csv_path = find_sibling_csv(str(csv_path), "imap_lo_goodtimes")
    h_bg_path   = find_sibling_csv(str(csv_path), "imap_lo_H_background")
    o_bg_path   = find_sibling_csv(str(csv_path), "imap_lo_O_background")

    all_ok = True

    def chk(label, csv_val, cdf_val):
        nonlocal all_ok
        ok = check(label, csv_val, cdf_val, rtol, atol)
        all_ok = all_ok and ok

    # ── HO_cnts_expo: goodtime begin / end ─────────────────────────────────
    if gt is not None:
        n_csv, n_cdf = len(df), len(_array(gt["gt_start_met"]))
        print(f"\n── HO_cnts_expo: goodtime intervals (CSV={n_csv}, CDF={n_cdf}) ──")
        if n_csv != n_cdf:
            print("  [MISMATCH] row count differs")
            all_ok = False
        else:
            chk("gt_start_met  (HO_cnts_expo 'begin')", df["begin"].values, gt["gt_start_met"])
            chk("gt_end_met    (HO_cnts_expo 'end')",   df["end"].values,   gt["gt_end_met"])

    # ── HO_cnts_expo: pivot angles ─────────────────────────────────────────
    print("\n── HO_cnts_expo: pivot angles ──")
    chk("pivot    (HO_cnts_expo 'pivot')",    df["pivot"].iloc[-1],    _scalar(gt["pivot"]))
    chk("pivot_de (HO_cnts_expo 'pivot_de')", df["pivot_de"].iloc[-1], _scalar(gt["pivot_de"]))

    # ── HO_cnts_expo: proxy floor counts ───────────────────────────────────
    # sum_bg1_cnts/sum_og1_cnts accumulate across ALL goodtime intervals without
    # resetting.  Only the *last* CSV row equals the CDF grand-total scalar.
    print("\n── HO_cnts_expo: proxy floor counts (last CSV row vs CDF scalar) ──")
    last = df.iloc[-1]
    chk("h_proxy_floor  (HO_cnts_expo 'h_proxy_counts')",
        last["h_proxy_counts"], _scalar(bg["h_proxy_floor"]))
    chk("o_proxy_floor  (HO_cnts_expo 'o_proxy_counts')",
        last["o_proxy_counts"], _scalar(bg["o_proxy_floor"]))

    # ── HO_cnts_expo: proxy exposure ───────────────────────────────────────
    # proxy_exposure (sum_bg1_expo) has no direct CDF variable.  Derive from
    # *_synthetic_floor using:
    #   synthetic_floor = n_good * BG_RATE * N_CYCLE_AVE * HISTOGRAM_CYCLE_EPOCHS * EXPOSURE_FACTOR
    #   proxy_exposure  = n_good * N_CYCLE_SUM * HISTOGRAM_CYCLE_EPOCHS * EXPOSURE_FACTOR
    #                   = synthetic_floor * N_CYCLE_SUM / (BG_RATE * N_CYCLE_AVE)
    print("\n── HO_cnts_expo: proxy exposure (no direct CDF variable) ──")
    for elem in LoConstants.ELEMS:
        elem_syn = _scalar(bg[f"{elem.lower()}_synthetic_floor"])
        derived = elem_syn * LoConstants.N_CYCLE_SUM / (LoConstants.BG_RATES[elem] * LoConstants.N_CYCLE_AVE)
        print(f"  [{elem}] CSV={last['proxy_exposure']:.4f} s  "
              f"derived={derived:.4f} s  "
              f"(synthetic_floor={elem_syn:.6g} × {LoConstants.N_CYCLE_SUM}/{LoConstants.N_CYCLE_AVE} / {LoConstants.BG_RATES[elem]})")
        chk(f"proxy_exposure [{elem}] (derived from {elem.lower()}_synthetic_floor)",
            last["proxy_exposure"], derived)

    # ── Goodtimes CSV ───────────────────────────────────────────────────────
    # imap_lo_goodtimes_*.csv is written by the same print_lines() call as
    # HO_cnts_expo, so begin/end are identical.  This is an independent check.
    if gt_csv_path and gt is not None:
        gt_csv = load_gt_csv(str(gt_csv_path))
        n_gt_csv, n_gt_cdf = len(gt_csv), len(_array(gt["gt_start_met"]))
        print(f"\n── goodtimes CSV: intervals (CSV={n_gt_csv}, CDF={n_gt_cdf}) ──")
        if n_gt_csv != n_gt_cdf:
            print("  [MISMATCH] row count differs")
            all_ok = False
        else:
            chk("gt_start_met  (goodtimes CSV 'begin')", gt_csv["begin"].values, gt["gt_start_met"])
            chk("gt_end_met    (goodtimes CSV 'end')",   gt_csv["end"].values,   gt["gt_end_met"])
    else:
        print(f"\n── goodtimes CSV: {'not found' if gt_csv_path is None else 'goodtimes CDF missing'}, skipping ──")

    # ── H background CSV ────────────────────────────────────────────────────
    # Two rows: rate = sum_bg_cnts/sum_bg_expo ≡ BG_RATES["H"] (constant)
    #           sigma = sqrt(sum_bg_cnts)/sum_bg_expo ≡ h_background_variance
    # begin/end (first_begin-120 / last_end-270) cover the full HK epoch range
    # and have no CDF equivalent.
    if h_bg_path:
        h_bg = load_bg_csv(str(h_bg_path))
        rate_row  = h_bg[h_bg["tag"].str.strip() == "rate"].iloc[0]
        sigma_row = h_bg[h_bg["tag"].str.strip() == "sigma"].iloc[0]
        csv_h_rates  = rate_row[_ESA_COLS].values.astype(float)
        csv_h_sigmas = sigma_row[_ESA_COLS].values.astype(float)
        print(f"\n── H_background CSV vs bgrates CDF ──")
        chk("h_background_rates    (H_background CSV rate row)",  csv_h_rates,  bg["h_background_rates"])
        chk("h_background_variance (H_background CSV sigma row)", csv_h_sigmas, bg["h_background_variance"])
        print(f"  [NOTE] begin/end ({int(rate_row['begin'])} / {int(rate_row['end'])}) "
              f"= first_hk_met-120 / last_hk_met-270 — no CDF equivalent")
    else:
        print(f"\n── H_background CSV: not found, skipping ──")

    # ── O background CSV ────────────────────────────────────────────────────
    if o_bg_path:
        o_bg = load_bg_csv(str(o_bg_path))
        rate_row  = o_bg[o_bg["tag"].str.strip() == "rate"].iloc[0]
        sigma_row = o_bg[o_bg["tag"].str.strip() == "sigma"].iloc[0]
        csv_o_rates  = rate_row[_ESA_COLS].values.astype(float)
        csv_o_sigmas = sigma_row[_ESA_COLS].values.astype(float)
        print(f"\n── O_background CSV vs bgrates CDF ──")
        chk("o_background_rates    (O_background CSV rate row)",  csv_o_rates,  bg["o_background_rates"])
        chk("o_background_variance (O_background CSV sigma row)", csv_o_sigmas, bg["o_background_variance"])
        print(f"  [NOTE] begin/end ({int(rate_row['begin'])} / {int(rate_row['end'])}) "
              f"= first_hk_met-120 / last_hk_met-270 — no CDF equivalent")
    else:
        print(f"\n── O_background CSV: not found, skipping ──")

    print(f"\n{'All checks passed.' if all_ok else 'Some checks FAILED.'}")
    return all_ok


def yyyyddd_to_date(yyyyddd: str):
    from datetime import date, timedelta
    return date(int(yyyyddd[:4]), 1, 1) + timedelta(days=int(yyyyddd[4:]) - 1)


def date_to_yyyyddd(d) -> str:
    return f"{d.year}{d.timetuple().tm_yday:03d}"


def find_bgrates_cdf(cdf_base: Path, d) -> Path | None:
    """Return the highest-version bgrates CDF for a given date, or None."""
    yyyy = f"{d.year}"
    mm   = f"{d.month:02d}"
    yyyymmdd = d.strftime("%Y%m%d")
    cdf_dir = cdf_base / yyyy / mm
    matches = sorted(cdf_dir.glob(f"imap_lo_l1b_bgrates_{yyyymmdd}-repoint*.cdf"))
    return matches[-1] if matches else None


def run_all(start_yyyyddd: str, csv_dir: Path, cdf_base: Path,
            rtol: float = 1e-4, atol: float = 1e-6) -> None:
    from datetime import date, timedelta

    start = yyyyddd_to_date(start_yyyyddd)
    end   = date.today()

    n_compared = n_passed = n_failed = n_skipped = 0

    current = start
    while current <= end:
        yyyyddd  = date_to_yyyyddd(current)
        csv_path = csv_dir / f"imap_lo_HO_cnts_expo_{yyyyddd}.csv"
        cdf_path = find_bgrates_cdf(cdf_base, current)

        if not csv_path.exists() or cdf_path is None:
            n_skipped += 1
            current += timedelta(days=1)
            continue

        print(f"\n{'='*60}")
        print(f"  {yyyyddd}  ({current.isoformat()})")
        print(f"  CSV: {csv_path.name}")
        print(f"  CDF: {cdf_path.name}")
        print(f"{'='*60}")

        n_compared += 1
        ok = compare_data(csv_path, cdf_path, rtol=rtol, atol=atol, current=current)
        if ok:
            n_passed += 1
        else:
            n_failed += 1

        current += timedelta(days=1)

    print(f"\n{'='*60}")
    print(f"  SUMMARY  {start_yyyyddd} → {date_to_yyyyddd(end)}")
    print(f"  compared={n_compared}  passed={n_passed}  failed={n_failed}  skipped={n_skipped}")
    print(f"{'='*60}")


if __name__ == "__main__":
    run_all(
        "2026131",
        csv_dir  = Path("/media/vineetb/delta/projects/imap/lo/quicklook/1S04_l1b_histRates_autogoodtimes/output"),
        cdf_base = Path("/media/vineetb/T7/imap/lo/l1b"),
    )
