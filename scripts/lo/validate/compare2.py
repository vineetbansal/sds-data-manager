"""
Compare CSV outputs of hist_auto_gt_V5.py with the l1c_pset CDFs.

Files compared
--------------
imap_lo_HO_cnts_expo_*.csv  (csv_path, required)
  pivot          -> pivot_angle              [pset CDF]

imap_lo_H_background_*.csv  (auto-discovered)
  rate row  (cols 7-13) -> h_background_rates             [pset CDF, shape (1,7,3600,40)]
  sigma row (cols 7-13) -> h_background_rates_stat_uncert [pset CDF, shape (1,7,3600,40)]
  begin/end timing has no CDF equivalent

imap_lo_O_background_*.csv  (auto-discovered)
  rate row  (cols 7-13) -> o_background_rates             [pset CDF, shape (1,7,3600,40)]
  sigma row (cols 7-13) -> o_background_rates_stat_uncert [pset CDF, shape (1,7,3600,40)]
  begin/end timing has no CDF equivalent

proxy_exposure (HO_cnts_expo)  -> sum(pset exposure_time) × EXPOSURE_FACTOR
  proxy_exposure ≈ sum(pset["exposure_time"]) * EXPOSURE_FACTOR (0.5)
  ~0.5% residual from actual vs nominal spin period; rtol=2% tolerance used.

Fields with no L1C pset equivalent (informational only)
---------------------------------------------------------
  begin / end (goodtime intervals)    — pset stores only pointing_start/end_met
  h_proxy_counts / o_proxy_counts     — not in L1C pset
  pivot_de                            — not in L1C pset

3S2 quickmaps vs pset exposure_time
  Both pipelines use actual spin durations, so agreement is much tighter than
  the ~2% proxy check.  Residual discrepancy arises when a histrates epoch
  falls within the goodtime boundary ambiguity caused by the hardcoded +9s MET
  correction in hist_auto_gt_V5.py vs the SPICE SCLK kernel.

  Expected per-bin absolute tolerance (seconds):
    atol(t) = N × ceil(|delta(t)| / T_epoch) × EXPO_PER_EPOCH_PER_BIN
  where
    N        = number of goodtime intervals (rows in the HO CSV)
    delta(t) = DRIFT_SLOPE × (t − DRIFT_ZERO).days     [scripts/sclk_drift.py]
    T_epoch  ≈ 423 s   (histrates epoch spacing)
    EXPO_PER_EPOCH_PER_BIN ≈ 1.1 s  (empirical; observed range 0.99–1.02 s)

  imap_processing sets its goodtime start to the first epoch boundary, so the
  first epoch of each interval always falls in the (0, |delta|) window and is
  excluded by the 3S2 pipeline.  With N intervals and ceil = 1 (since
  |delta| << T_epoch for current mission data), the maximum per-bin diff is
  N × ~1.0 s.

Tolerance note
--------------
Background rates in the pset CDF are stored as float16, so rtol=1e-3 is the
default (matching float16 relative precision of ~9.8e-4).
"""


import math
import re
from datetime import date, datetime
from pathlib import Path

import cdflib
import numpy as np
import pandas as pd

from imap_processing.lo.constants import LoConstants


HO_COLS = [
    "date", "begin", "end", "bin_lo", "bin_hi", "instrument",
    "h_proxy_counts", "o_proxy_counts", "proxy_exposure", "pivot", "pivot_de",
]

BG_COLS = [
    "date", "begin", "end", "bin_lo", "bin_hi", "instrument",
    "esa1", "esa2", "esa3", "esa4", "esa5", "esa6", "esa7", "tag",
]

_ESA_COLS = ["esa1", "esa2", "esa3", "esa4", "esa5", "esa6", "esa7"]


def load_csv(path: str) -> pd.DataFrame:
    return pd.read_csv(path, header=None, names=HO_COLS)


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


def find_sibling_csv(ho_cnts_path: str, prefix: str) -> Path | None:
    """Find a sibling CSV in the same directory sharing the YYYYDDD date string."""
    p = Path(ho_cnts_path)
    m = re.search(r"(\d{7})", p.name)
    if m:
        candidate = p.parent / f"{prefix}_{m.group(1)}.csv"
        return candidate if candidate.exists() else None
    return None


_PIVOT_FOLDERS = [75, 90, 105]


def _snap_pivot(pivot: float) -> int:
    """Snap a pivot angle to the nearest available quickmaps folder."""
    return min(_PIVOT_FOLDERS, key=lambda c: abs(c - pivot))


def find_quickmap_csvs(
    quickmaps_base: Path, yyyyddd: str, pivot: float
) -> dict[int, Path]:
    """Return {esa_step: path} for 3S2 quickmap daily CSVs that exist."""
    folder = quickmaps_base / f"pivot_{_snap_pivot(pivot)}" / "daily"
    return {
        esa: p
        for esa in range(1, 8)
        if (p := folder / f"data_YD_{yyyyddd}_esa{esa}.csv").exists()
    }


# ── SCLK drift model (derived from scripts/sclk_drift.py) ──────────────────
# The quicklook goodtime generator (hist_auto_gt_V5.py) computes MET as
#   met_ql = (utc − 2010-01-01).total_seconds() + 9
# calibrated against a pre-2012 SPICE kernel.  The true offset diverges from
# +9 as the spacecraft clock drifts:
#   delta(t) = met_ql(t) − met_spice(t)  [seconds]
# Linear fit over 2025-11-08 → 2026-05-16 (residual std = 0.027 s):
_DRIFT_ZERO  = date(2026, 1, 26)   # date when delta(t) ≈ 0
_DRIFT_SLOPE = -0.07612            # s/day  (run sclk_drift.py to refresh)

# L1B histrates epoch spacing: 7 ESA steps × ~60 s each ≈ 423 s
_HISTRATES_EPOCH_SPACING_S = 423.0

# Per-bin exposure contributed by one histrates epoch (one ESA step):
#   ~spin_period/60 × spins_per_ESA_step ≈ 15 s / 60 × 4 spins ≈ 1.0 s/bin
# Empirically observed across all dates: mean=0.993 s, max=1.018 s.
# The 1.1 s value adds ~8% headroom for spin-phase non-uniformity.
_EXPO_PER_EPOCH_PER_BIN_S = 1.1

# Dates whose pset CDF values were manually overridden after initial production.
# The 3S2 quickmap was not regenerated, so the exposure comparison is not valid.
_EXPO_SKIP_YYYYDDD = frozenset({"2026062", "2026064", "2026065", "2026091"})


def _goodtime_drift_s(repoint_date: date) -> float:
    """Expected met_quicklook − met_spice in seconds at a given repoint date."""
    return _DRIFT_SLOPE * (repoint_date - _DRIFT_ZERO).days


def _expo_atol(repoint_date: date, n_goodtime_intervals: int) -> float:
    """
    Per-bin exposure absolute tolerance from the goodtime SCLK drift model.

    imap_processing sets its goodtime start to the first epoch boundary, so the
    first epoch of every goodtime interval falls in the (0, |delta(t)|) window
    and is excluded by the 3S2 pipeline.  With N intervals and at most
    ceil(|delta| / T_epoch) epochs affected per boundary edge:

        atol = N × ceil(|delta(t)| / T_epoch) × EXPO_PER_EPOCH_PER_BIN

    For current mission data |delta| < T_epoch always, so ceil = 1.

    Parameters
    ----------
    repoint_date : date
        Calendar date of the repoint being compared.
    n_goodtime_intervals : int
        Number of rows in the HO CSV (= number of goodtime intervals).

    Returns
    -------
    float
        Maximum expected |csv_expo[bin] − cdf_expo[bin]| in seconds.
    """
    drift_s = abs(_goodtime_drift_s(repoint_date))
    n_per_boundary = max(1, math.ceil(drift_s / _HISTRATES_EPOCH_SPACING_S))
    return n_goodtime_intervals * n_per_boundary * _EXPO_PER_EPOCH_PER_BIN_S


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


def compare_data(
    csv_path: Path,
    pset_cdf_path: Path,
    rtol: float = 1e-3,
    atol: float = 1e-6,
    current: datetime | None = None,
    quickmaps_base: Path | None = None,
) -> bool:
    df = load_csv(str(csv_path))
    if df.empty:
        print("\n── No goodtimes, failing  ──")
        return False

    pset = load_cdf(str(pset_cdf_path))

    h_bg_path = find_sibling_csv(str(csv_path), "imap_lo_H_background")
    o_bg_path = find_sibling_csv(str(csv_path), "imap_lo_O_background")

    all_ok = True

    def chk(label, csv_val, cdf_val):
        nonlocal all_ok
        ok = check(label, csv_val, cdf_val, rtol, atol)
        all_ok = all_ok and ok

    # ── Pivot angle ─────────────────────────────────────────────────────────────
    # pset["pivot_angle"] originates from get_pivot_angle_from_nhk() in lo_l1b,
    # which is the same HK-median calculation as hist_auto_gt_V5.py's `pivot`.
    print("\n── HO_cnts_expo: pivot angle ──")
    last = df.iloc[-1]
    chk("pivot_angle (HO_cnts_expo 'pivot')", last["pivot"], _scalar(pset["pivot_angle"]))
    print("  [NOTE] pivot has no equivalent in L1C pset CDF")

    # ── Fields with no L1C pset equivalent ──────────────────────────────────────
    print("\n── Fields with no L1C pset equivalent (skipped) ──")
    print("  [NOTE] goodtime intervals (gt_start_met / gt_end_met)"
          " — pset stores only pointing_start/end_met")
    print("  [NOTE] h_proxy_counts / o_proxy_counts (proxy floor counts)"
          " — not in L1C pset")
    print("  [NOTE] pivot_de — not in L1C pset")

    # ── proxy_exposure vs pset exposure_time ─────────────────────────────────
    # proxy_exposure (quicklook) = n_good * HISTOGRAM_CYCLE_EPOCHS * EXPOSURE_FACTOR
    #   using nominal spin period and the EXPOSURE_FACTOR=0.5 convention.
    # pset exposure_time total   = sum of actual spin-duration-based per-bin exposure
    #   = n_good * actual_spin_duration * N_ESA_LEVELS * N_SPINS_PER_ESA_LEVEL
    #   ≈ n_good * HISTOGRAM_CYCLE_EPOCHS  (no EXPOSURE_FACTOR applied)
    # So: proxy_exposure ≈ sum(pset["exposure_time"]) * EXPOSURE_FACTOR
    # Residual ~0.5% discrepancy is expected from actual vs nominal spin period.
    print("\n── HO_cnts_expo: proxy_exposure vs pset exposure_time ──")
    if "exposure_time" in pset:
        pset_expo_total = float(np.asarray(pset["exposure_time"]).sum())
        derived = pset_expo_total * LoConstants.EXPOSURE_FACTOR
        csv_proxy = float(df.iloc[-1]["proxy_exposure"])
        print(f"  CSV proxy_exposure={csv_proxy:.2f} s  "
              f"derived (pset sum × {LoConstants.EXPOSURE_FACTOR})={derived:.2f} s")
        ok = check(
            "proxy_exposure (CSV) vs sum(pset exposure_time) × EXPOSURE_FACTOR",
            csv_proxy, derived, rtol=0.02, atol=0.0,
        )
        all_ok = all_ok and ok
    else:
        print("  [NOTE] exposure_time not found in pset CDF — skipping")

    # ── 3S2 quickmap expo vs pset exposure_time ──────────────────────────────────
    # The 3S2 pipeline uses actual spin durations (same as imap_processing), so
    # agreement should be much tighter than the ~0.5% from the proxy check above.
    # pset exposure_time shape (1, 7, 3600, 40) encodes the original 6°-bin totals
    # as: value[esa, spin6_bin] / 60 (repeated 60×) / 40 (broadcast).
    # Reconstruct: sum over 40 off-angle bins then group-sum 60 consecutive 0.1°
    # bins → per-6°-bin exposure that matches the 3S2 `expo` column.
    if quickmaps_base is not None and "exposure_time" in pset:
        m = re.search(r"(\d{7})", csv_path.name)
        yyyyddd = m.group(1) if m else None
        pivot_val = float(np.atleast_1d(pset["pivot_angle"]).ravel()[0])
        qm_csvs = find_quickmap_csvs(quickmaps_base, yyyyddd, pivot_val) if yyyyddd else {}
        print(f"\n── 3S2 quickmap expo vs pset exposure_time (pivot snapped to {_snap_pivot(pivot_val)}°) ──")
        if yyyyddd in _EXPO_SKIP_YYYYDDD:
            print(f"  [SKIP] {yyyyddd} has manually overridden pset values — quickmap comparison not valid")
        elif not qm_csvs:
            print("  [NOTE] No 3S2 quickmap daily CSVs found for this date — skipping")
        else:
            # Reconstruct per-6°-bin exposure from pset: (7, 3600, 40) → (7, 60)
            pset_et = np.asarray(pset["exposure_time"], dtype=float)[0]  # (7, 3600, 40)
            pset_et_6deg = pset_et.sum(axis=-1).reshape(7, 60, 60).sum(axis=-1)  # (7, 60)

            repoint_date = current.date() if isinstance(current, datetime) else current
            use_drift_atol = repoint_date is not None
            if use_drift_atol:
                n_intervals = len(df)
                drift_s = _goodtime_drift_s(repoint_date)
                expo_atol = _expo_atol(repoint_date, n_intervals)
                print(
                    f"  [drift] delta(t) = {drift_s:+.2f} s, "
                    f"N_intervals = {n_intervals} → "
                    f"atol = {expo_atol:.2f} s/bin"
                )

            for esa, path in sorted(qm_csvs.items()):
                qm_df = pd.read_csv(path)
                csv_expo = qm_df["expo"].values.astype(float)  # shape (60,)
                cdf_expo = pset_et_6deg[esa - 1]               # shape (60,)
                if use_drift_atol:
                    ok = check(
                        f"exposure_time esa{esa} (3S2 vs pset)",
                        csv_expo, cdf_expo, rtol=0.0, atol=expo_atol,
                    )
                    all_ok = all_ok and ok
                else:
                    chk(f"exposure_time esa{esa} (3S2 expo vs pset)", csv_expo, cdf_expo)
    elif quickmaps_base is None:
        pass  # caller did not provide quickmaps_base; skip silently
    else:
        print("\n── 3S2 quickmap expo: exposure_time not in pset CDF — skipping ──")

    # ── H background CSV vs pset CDF ────────────────────────────────────────────
    # h_background_rates in pset has shape (1, 7, 3600, 40): the 7 per-ESA values
    # are broadcast uniformly across all spin/off-angle bins.  Extract [0, :, 0, 0].
    # Values are stored as float16; rtol=1e-3 is sufficient.
    if h_bg_path:
        h_bg = load_bg_csv(str(h_bg_path))
        rate_row  = h_bg[h_bg["tag"].str.strip() == "rate"].iloc[0]
        sigma_row = h_bg[h_bg["tag"].str.strip() == "sigma"].iloc[0]
        csv_h_rates  = rate_row[_ESA_COLS].values.astype(float)
        csv_h_sigmas = sigma_row[_ESA_COLS].values.astype(float)

        bg_arr  = np.asarray(pset["h_background_rates"],             dtype=float)
        sig_arr = np.asarray(pset["h_background_rates_stat_uncert"], dtype=float)
        # shape is (1, 7, 3600, 40); take first spin/off-angle bin of each ESA step
        cdf_h_rates  = bg_arr.reshape(1, 7, -1)[0, :, 0]
        cdf_h_sigmas = sig_arr.reshape(1, 7, -1)[0, :, 0]

        print("\n── H_background CSV vs pset CDF ──")
        chk("h_background_rates             (H_background CSV rate  row)", csv_h_rates,  cdf_h_rates)
        chk("h_background_rates_stat_uncert (H_background CSV sigma row)", csv_h_sigmas, cdf_h_sigmas)
        print(f"  [NOTE] begin/end ({int(rate_row['begin'])} / {int(rate_row['end'])}) "
              f"= first_hk_met-120 / last_hk_met-270 — no CDF equivalent")
    else:
        print("\n── H_background CSV: not found, skipping ──")

    # ── O background CSV vs pset CDF ────────────────────────────────────────────
    if o_bg_path:
        o_bg = load_bg_csv(str(o_bg_path))
        rate_row  = o_bg[o_bg["tag"].str.strip() == "rate"].iloc[0]
        sigma_row = o_bg[o_bg["tag"].str.strip() == "sigma"].iloc[0]
        csv_o_rates  = rate_row[_ESA_COLS].values.astype(float)
        csv_o_sigmas = sigma_row[_ESA_COLS].values.astype(float)

        bg_arr  = np.asarray(pset["o_background_rates"],             dtype=float)
        sig_arr = np.asarray(pset["o_background_rates_stat_uncert"], dtype=float)
        cdf_o_rates  = bg_arr.reshape(1, 7, -1)[0, :, 0]
        cdf_o_sigmas = sig_arr.reshape(1, 7, -1)[0, :, 0]

        print("\n── O_background CSV vs pset CDF ──")
        chk("o_background_rates             (O_background CSV rate  row)", csv_o_rates,  cdf_o_rates)
        chk("o_background_rates_stat_uncert (O_background CSV sigma row)", csv_o_sigmas, cdf_o_sigmas)
        print(f"  [NOTE] begin/end ({int(rate_row['begin'])} / {int(rate_row['end'])}) "
              f"= first_hk_met-120 / last_hk_met-270 — no CDF equivalent")
    else:
        print("\n── O_background CSV: not found, skipping ──")

    print(f"\n{'All checks passed.' if all_ok else 'Some checks FAILED.'}")
    return all_ok


def yyyyddd_to_date(yyyyddd: str):
    from datetime import date, timedelta
    return date(int(yyyyddd[:4]), 1, 1) + timedelta(days=int(yyyyddd[4:]) - 1)


def date_to_yyyyddd(d) -> str:
    return f"{d.year}{d.timetuple().tm_yday:03d}"


def find_pset_cdf(cdf_base: Path, d) -> Path | None:
    """Return the highest-version pset CDF for a given date, or None."""
    yyyy     = f"{d.year}"
    mm       = f"{d.month:02d}"
    yyyymmdd = d.strftime("%Y%m%d")
    cdf_dir  = cdf_base / yyyy / mm
    matches  = sorted(cdf_dir.glob(f"imap_lo_l1c_pset_{yyyymmdd}-repoint*.cdf"))
    return matches[-1] if matches else None


def run_all(
    start_yyyyddd: str,
    csv_dir: Path,
    cdf_base: Path,
    rtol: float = 1e-3,
    atol: float = 1e-6,
    quickmaps_base: Path | None = None,
) -> None:
    from datetime import date, timedelta

    start = yyyyddd_to_date(start_yyyyddd)
    end   = date.today()

    n_compared = n_passed = n_failed = n_skipped = 0

    current = start
    while current <= end:
        yyyyddd  = date_to_yyyyddd(current)
        csv_path = csv_dir / f"imap_lo_HO_cnts_expo_{yyyyddd}.csv"
        cdf_path = find_pset_cdf(cdf_base, current)

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
        ok = compare_data(
            csv_path, cdf_path, rtol=rtol, atol=atol, current=current,
            quickmaps_base=quickmaps_base,
        )
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
        "2025310",
        csv_dir       = Path("/media/vineetb/delta/projects/imap/lo/quicklook/1S04_l1b_histRates_autogoodtimes/output"),
        cdf_base      = Path("/media/vineetb/T7/imap/lo/l1c"),
        quickmaps_base= Path("/media/vineetb/delta/projects/imap/lo/quicklook/3S2_l1b_quickmaps/outdir"),
    )
