import re
from pathlib import Path

import cdflib
import numpy as np
import pandas as pd
from scipy.interpolate import interp1d

from imap_processing.lo.l2.lo_l2 import reduce_geometric_factor_dataset
from imap_processing.spice.geometry import SpiceFrame, frame_transform_az_el
from imap_processing.spice.time import ttj2000ns_to_et


DATA_ROOT = Path("/home/vineetb/Downloads/IMAP-LO_All_L3_maps_and_inputs_20260522/imap")
LO_L1C_DIR = DATA_ROOT / "lo/l1c"
GLOWS_L3E_DIR = DATA_ROOT / "glows/l3e"
SPICE_DIR = DATA_ROOT / "spice"

# Sky map grid: 6-degree bins
NLAT = 30    # -90 to +90
NLON = 60    # 0 to 360
DEG_BIN = 6.0
NESA = 7


def find_matching_pairs(lo_dir: Path, glows_dir: Path, date: str):
    """Return list of (lo_l1c_path, glows_l3e_path) matched by date+repoint."""
    lo_files = sorted(lo_dir.rglob(f"*pset_{date}-repoint*.cdf"))
    pairs = []
    for lo_file in lo_files:
        m = re.search(r"repoint(\d+)", lo_file.name)
        if not m:
            continue
        repoint = m.group(1)
        glows_matches = sorted(glows_dir.rglob(f"*{date}-repoint{repoint}*.cdf"))
        if glows_matches:
            pairs.append((lo_file, glows_matches[0]))
    return pairs


# Spin/off angle bin centers matching the l1c pset convention (from lo_l1c.py)
_SPIN_CENTERS = (np.linspace(0, 360, 3601)[:-1] + np.linspace(0, 360, 3601)[1:]) / 2  # (3600,)
_OFF_CENTERS = (np.linspace(-2, 2, 41)[:-1] + np.linspace(-2, 2, 41)[1:]) / 2          # (40,)


def load_spice_kernels(spice_dir: Path) -> None:
    import spiceypy

    kernel_patterns = ["lsk/*.tls", "fk/*.tf", "sclk/*.tsc", "spk/*.bsp", "ck/*.bc"]
    loaded = 0
    for pattern in kernel_patterns:
        for kernel in sorted(spice_dir.glob(pattern)):
            spiceypy.furnsh(str(kernel))
            loaded += 1
    print(f"  Loaded {loaded} SPICE kernels from {spice_dir}")


def compute_pointing_from_spice(epoch_ttj2000ns: float, pivot_angle: float) -> tuple[np.ndarray, np.ndarray]:
    et = ttj2000ns_to_et(epoch_ttj2000ns)

    spin, off = np.meshgrid(_SPIN_CENTERS, _OFF_CENTERS, indexing="ij")  # (3600, 40) each
    off = off + (90.0 - pivot_angle)   # adjust off-angle relative to pivot, matching l1c
    dps_az_el = np.stack([spin, off], axis=-1)  # (3600, 40, 2)

    hae_az_el = frame_transform_az_el(
        et, dps_az_el, SpiceFrame.IMAP_DPS, SpiceFrame.IMAP_HAE, degrees=True
    )  # (3600, 40, 2)

    return hae_az_el[:, :, 0], hae_az_el[:, :, 1]  # lon, lat


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


def apply_survival_correction(
    h_counts: np.ndarray,         # (7, 3600, 40)
    sp_interp: np.ndarray,        # (7, 360)
) -> np.ndarray:                  # (7, 3600, 40)
    """Divide h_counts by survival probability, mapping 0.1-deg lo bins to 1-deg glows bins."""
    # Lo spin_angle centers: 0.05, 0.15, ..., 359.95 (3600 bins)
    # GLOWS spin_angle centers: 1, 2, ..., 360 (360 bins, 0-based index 0..359)
    # Every 10 consecutive lo bins share one glows bin: lo index i -> glows index i // 10
    sp_spin_idx = np.arange(3600) // 10                 # (3600,)
    sp_2d = sp_interp[:, sp_spin_idx]                   # (7, 3600)
    sp_3d = sp_2d[:, :, np.newaxis]                     # (7, 3600, 1) - broadcast over off_angle
    return h_counts / sp_3d


def project_to_map(
    corrected_counts: np.ndarray,   # (7, 3600, 40)
    exposure: np.ndarray,           # (7, 3600, 40)
    hae_lon: np.ndarray,            # (3600, 40) degrees
    hae_lat: np.ndarray,            # (3600, 40) degrees
) -> tuple[np.ndarray, np.ndarray]:

    counts_map = np.zeros((NESA, NLAT, NLON))
    exposure_map = np.zeros((NESA, NLAT, NLON))

    lat_idx = np.clip(np.floor((hae_lat + 90.0) / DEG_BIN).astype(int), 0, NLAT - 1)
    lon_idx = np.floor(hae_lon / DEG_BIN).astype(int) % NLON
    geo_valid = np.isfinite(hae_lat) & np.isfinite(hae_lon)

    for esa in range(NESA):
        cnt = corrected_counts[esa]    # (3600, 40)
        exp = exposure[esa]            # (3600, 40)
        mask = geo_valid & np.isfinite(cnt) & (exp > 0)
        np.add.at(counts_map[esa], (lat_idx[mask], lon_idx[mask]), cnt[mask])
        np.add.at(exposure_map[esa], (lat_idx[mask], lon_idx[mask]), exp[mask])

    return counts_map, exposure_map


def compute_flux(
    counts_map: np.ndarray,     # (7, 30, 60)
    exposure_map: np.ndarray,   # (7, 30, 60)
    geo_factor: np.ndarray,     # (7,) cm² sr keV
    lo_energies: np.ndarray,    # (7,) keV
) -> np.ndarray:                # (7, 30, 60)
    """Flux = corrected_counts / (exposure * geometric_factor * energy)."""
    flux = np.full_like(counts_map, np.nan, dtype=float)
    for esa in range(NESA):
        denom = exposure_map[esa] * geo_factor[esa] * lo_energies[esa]
        valid = denom > 0
        flux[esa][valid] = counts_map[esa][valid] / denom[valid]
    return flux


def _lat_lon_centers():
    lats = -90.0 + DEG_BIN * (np.arange(NLAT) + 0.5)
    lons = DEG_BIN * (np.arange(NLON) + 0.5)
    return lats, lons


def write_map_csv(data: np.ndarray, path: Path, lat_centers, lon_centers):
    """Write a (30, 60) 2D array as CSV with lat/lon bin-center labels."""
    df = pd.DataFrame(data, index=np.round(lat_centers, 1), columns=np.round(lon_centers, 1))
    df.index.name = "lat_deg"
    df.to_csv(path)


def process(date: str, lo_dir: Path, glows_dir: Path, out_dir: Path, use_spice: bool = False, spice_dir: Path | None = None):

    out_dir.mkdir(parents=True, exist_ok=True)

    pairs = find_matching_pairs(lo_dir, glows_dir, date)
    if not pairs:
        print(f"No matching lo l1c + glows l3e files found for {date}")
        return
    print(f"Found {len(pairs)} repoint(s) for {date}")

    if use_spice:
        print("Using SPICE for HAE pointing computation")
        load_spice_kernels(Path(spice_dir))

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

    acc_counts = np.zeros((NESA, NLAT, NLON))
    acc_exposure = np.zeros((NESA, NLAT, NLON))

    for lo_file, glows_file in pairs:
        glows_cdf = cdflib.cdfread.CDF(str(glows_file))
        surv_prob = np.array(glows_cdf.varget("surv_prob"))[0]       # (24, 360)
        glows_energies = np.array(glows_cdf.varget("energy_grid"))   # (24,) keV

        lo_cdf = cdflib.cdfread.CDF(str(lo_file))
        h_counts = np.array(lo_cdf.varget("h_counts"))[0].astype(float)      # (7, 3600, 40)
        exposure = np.array(lo_cdf.varget("exposure_time"))[0].astype(float)  # (7, 3600, 40)

        if use_spice:
            epoch = float(np.array(lo_cdf.varget("epoch"))[0])
            pointing_start = float(np.array(lo_cdf.varget("pointing_start_met"))[0])
            pointing_end = float(np.array(lo_cdf.varget("pointing_end_met"))[0])
            pivot_angle = float(np.array(lo_cdf.varget("pivot_angle"))[0])
            # Use pointing midpoint, matching the l1c convention
            from imap_processing.spice.time import met_to_ttj2000ns
            midpoint_ttj2000ns = met_to_ttj2000ns((pointing_start + pointing_end) / 2)
            hae_lon, hae_lat = compute_pointing_from_spice(midpoint_ttj2000ns, pivot_angle)
        else:
            hae_lon = np.array(lo_cdf.varget("hae_longitude"))[0]   # (3600, 40)
            hae_lat = np.array(lo_cdf.varget("hae_latitude"))[0]    # (3600, 40)

        sp_interp = interpolate_surv_prob(surv_prob, glows_energies, lo_energies)  # (7, 360)
        corrected_counts = apply_survival_correction(h_counts, sp_interp)

        cnts, expo = project_to_map(corrected_counts, exposure, hae_lon, hae_lat)  # (NESA, NLAT, NLON) each
        acc_counts += np.nan_to_num(cnts)
        acc_exposure += np.nan_to_num(expo)

    flux_map = compute_flux(acc_counts, acc_exposure, geo_factor, lo_energies)
    lat_centers, lon_centers = _lat_lon_centers()

    for esa in range(NESA):
        for name, data in [("flux", flux_map[esa]), ("cnts", acc_counts[esa]), ("expo", acc_exposure[esa])]:
            path = out_dir / f"map_sp_{name}_{date}_esa{esa + 1}.csv"
            write_map_csv(data, path, lat_centers, lon_centers)

    print(f"Written {3 * NESA} CSV files to {out_dir}/")


if __name__ == "__main__":
    # process a single date - loop here if needed
    process(
        date="20260510",
        lo_dir=LO_L1C_DIR,
        glows_dir=GLOWS_L3E_DIR,
        out_dir=Path("out")
    )
