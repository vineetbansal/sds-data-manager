import re
from pathlib import Path
import cdflib
import numpy as np
import pandas as pd
from scipy.interpolate import interp1d

from imap_processing.lo.constants import LoConstants
from imap_processing.lo.l2.lo_l2 import reduce_geometric_factor_dataset


DATA_ROOT = Path("/home/vineetb/Downloads/IMAP-LO_All_L3_maps_and_inputs_20260522/imap")
LO_L1C_DIR = DATA_ROOT / "lo/l1c"
LO_L1B_DIR = DATA_ROOT / "lo/l1b"
GLOWS_L3E_DIR = DATA_ROOT / "glows/l3e"

# Sky map grid: 6-degree bins
NLAT = 30    # -90 to +90
NLON = 60    # 0 to 360
DEG_BIN = 6.0
NESA = 7


def find_matching_triplets(lo_dir: Path, glows_dir: Path, l1b_dir: Path, date: str):
    l1c_matches = sorted(lo_dir.rglob(f"*pset_{date}-repoint*.cdf"))
    triplets = []
    for lo_file in l1c_matches:
        m = re.search(r"repoint(\d+)", lo_file.name)
        if not m:
            continue
        repoint = m.group(1)
        glows_matches = sorted(glows_dir.rglob(f"*{date}-repoint{repoint}*.cdf"))
        l1b_matches = sorted(l1b_dir.rglob(f"*bgrates*{date}-repoint{repoint}*.cdf"))
        if glows_matches and l1b_matches:
            triplets.append((l1b_matches[0], l1c_matches[0], glows_matches[0]))
    return triplets


def interpolate_surv_prob(
    surv_prob: np.ndarray,        # (24, 360)
    glows_energies: np.ndarray,   # (24,) keV
    lo_energies: np.ndarray,      # (7,)  keV
) -> np.ndarray:                  # (7, 360)
    """Interpolate surv_prob from the GLOWS 24-point log energy grid to Lo's 7 ESA energies."""
    f = interp1d(
        np.log(glows_energies), surv_prob,
        axis=0, kind="linear", bounds_error=False, fill_value="extrapolate",
    )
    sp = f(np.log(lo_energies))
    return np.clip(sp, 1e-6, 1.0)


def process(date: str, lo_dir: Path, glows_dir: Path, l1b_dir: Path) -> np.ndarray | None:
    """Process a single date, returning sp_interp_weighted summed over repoints, shape (7, 60)."""

    triplets = find_matching_triplets(lo_dir, glows_dir, l1b_dir, date)
    if not triplets:
        print(f"No matching lo l1c + glows l3e + lo l1b histrates files found for {date}")
        return None
    l1b_file, l1c_file, glows_file = triplets[0]

    # Load Lo H HiRes energies and geometric factors from the `imap_processing`

    """
    gf["Cntr_E"] — The geometric factor file is indexed by ESA step number (1–7), but
    the flux formula needs physical energy in keV
      (flux = counts / (exposure × GF × energy)).
    Cntr_E is the central energy of each ESA step in keV, so it converts the step index
    into a usable physical quantity. It's also what normalize_pset_coordinates in
    lo_l2.py uses to assign real energy coordinates to the pset dimensions.

    gf["GF_Trpl_H"] — The h_counts variable in the l1c pset contains hydrogen triple
    coincidence events specifically. There are several geometric factor columns
    available (GF_Dbl_H, GF_Trpl_H, GF_Trpl_all, etc.) and we need to match the right
    one to the count type. Taken from populate_geometric_factors in lo_l2.py at
    line 819:

      gf_vars = {
          "geometric_factor": f"GF_Trpl_{species.upper()}",
          ...
      }

    So for species "h" that resolves to GF_Trpl_H. Using the doubles geometric factor
    (GF_Dbl_H) or the species-agnostic triple factor (GF_Trpl_all) would give wrong flux
    values because those correspond to different detector coincidence requirements with
    different effective apertures.
    """
    _gf = reduce_geometric_factor_dataset("h", esa_mode=0)
    lo_energies, geo_factor = _gf["Cntr_E"].values, _gf["GF_Trpl_H"].values

    glows_cdf = cdflib.cdfread.CDF(str(glows_file))
    surv_prob = np.array(glows_cdf.varget("surv_prob"))[0]       # (24, 360)
    glows_energies = np.array(glows_cdf.varget("energy_grid"))   # (24,) keV

    l1c_cdf = cdflib.cdfread.CDF(str(l1c_file))
    h_counts = np.array(l1c_cdf.varget("h_counts"))[0].astype(float)      # (7, 3600, 40)

    l1b_cdf = cdflib.cdfread.CDF(str(l1b_file))
    h_synthetic_floor = float(np.atleast_1d(l1b_cdf.varget("h_synthetic_floor")).ravel()[0])
    # Derive total good-time exposure from the synthetic floor.
    # synthetic_floor = n_good × BG_RATE × N_CYCLE_AVE × HISTOGRAM_CYCLE_EPOCHS × EXPOSURE_FACTOR
    # exposure_sum    = n_good × N_CYCLE_SUM × HISTOGRAM_CYCLE_EPOCHS × EXPOSURE_FACTOR
    # exposure_sum    = synthetic_floor × N_CYCLE_SUM / (BG_RATE × N_CYCLE_AVE)
    exposure_total = h_synthetic_floor * LoConstants.N_CYCLE_SUM / (
        LoConstants.BG_RATES["H"] * LoConstants.N_CYCLE_AVE
    )
    exposure_correct = np.full(
        (7, 3600, 40),
        exposure_total / (LoConstants.EXPOSURE_FACTOR * NESA * 3600 * 40),
    )

    exposure_incorrect = np.array(l1c_cdf.varget("exposure_time"))[0].astype(
        float)  # (7, 3600, 40)

    sp_interp = interpolate_surv_prob(surv_prob, glows_energies, lo_energies)  # (7, 360)
    sp_interp = sp_interp.reshape(7, 60, 6).sum(axis=-1)  # (7, 60)

    exposure_incorrect = (
        exposure_incorrect.sum(axis=-1)  # (7, 3600)
        .reshape(7, 60, 60)  # split 3600 into 60 chunks of size 60
        .sum(axis=-1)  # sum within each chunk
    )  # (7, 60)

    sp_interp_weighted = sp_interp * exposure_incorrect

    return sp_interp_weighted  # (7, 60)


if __name__ == "__main__":
    from datetime import date, timedelta

    out_dir = Path("out")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 60 spin-angle bin centers at 6-degree resolution (3, 9, ..., 357 degrees)
    spin_bin_centers = np.arange(60) * 6.0 + 3.0

    start = date(2025, 11, 7)
    end = date.today()
    current = start
    results: dict[str, np.ndarray] = {}  # date_str -> (7, 60)
    while current <= end:
        date_str = current.strftime("%Y%m%d")
        result = process(
            date=date_str,
            lo_dir=LO_L1C_DIR,
            glows_dir=GLOWS_L3E_DIR,
            l1b_dir=LO_L1B_DIR,
        )
        if result is not None:
            results[date_str] = result
        current += timedelta(days=1)

    if results:
        dates_list = sorted(results.keys())
        for esa in range(NESA):
            data = np.column_stack([results[d][esa] for d in dates_list])  # (60, n_dates)
            df = pd.DataFrame(data, index=spin_bin_centers, columns=dates_list)
            df.index.name = "spin_angle_deg"
            df.to_csv(out_dir / f"sp_corrected_esa{esa + 1}.csv")
        print(f"Wrote {NESA} CSVs to {out_dir} with {len(dates_list)} date columns.")
