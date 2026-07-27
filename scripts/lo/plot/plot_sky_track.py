"""Plot where the instrument boresights sweep on the sky as IMAP spins.

The maths all lives in imap_processing -- the spin-table reader, the frame
transforms, the MET/ET conversions -- so this script only furnishes kernels,
picks times, and draws.

The top panel is the sky track. A boresight mounted at angle A from the spin
axis traces a small circle of radius A on the sky once per rotation, so each
instrument draws a ring; the ring drifts over hours as the spin axis follows the
Sun, and jumps at a repoint.

The bottom panel zooms in on a couple of rotations and marks the spin starts
read from the spin table, showing that one cycle of that sky motion is exactly
one row of the CSV.

Both panels come from the attitude CK. The spin table is what says where one
rotation ends and the next begins.

Runs in the imap_processing environment, which has spiceypy and matplotlib:
    uv run --project ../imap_processing python scripts/plot_sky_track.py \
        2026-01-08T16:38:38 --minutes 30

IMAP-Lo is the one instrument whose boresight is not fixed in the spacecraft: a
pivot mechanism rotates it about the instrument X axis, so its ring radius is
whatever the pivot angle is set to. Give that angle with --lo-pivot; the nominal
90 degrees puts the boresight in the spin plane, tracing the same great circle
as a 90-degree ENA imager.
"""

import argparse
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
import numpy as np
import spiceypy

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from imap_processing.spice.geometry import (
    SpiceFrame,
    frame_transform,
    instrument_pointing,
    lo_instrument_pointing,
)
from imap_processing.spice.spin import (
    get_spacecraft_spin_phase,
    get_spin_angle,
    get_spin_data,
    set_global_spin_table_paths,
)
from imap_processing.spice.time import et_to_met, str_to_et
from matplotlib.lines import Line2D

INSTRUMENTS = {
    "hi45": SpiceFrame.IMAP_HI_45,
    "hi90": SpiceFrame.IMAP_HI_90,
    "ultra45": SpiceFrame.IMAP_ULTRA_45,
    "ultra90": SpiceFrame.IMAP_ULTRA_90,
    "lo": SpiceFrame.IMAP_LO,
}

SPICE_DIR = Path(os.environ.get("IMAP_DATA_DIR") + "/imap/spice")

# imap[_dps]_<year>_<doy>_<year>_<doy>_<version>.ah.bc
CK_PATTERN = re.compile(r".*_(\d{4})_(\d{3})_(\d{4})_(\d{3})_(\d+)\.ah\.bc$")
# imap_<year>_<doy>_<year>_<doy>_<version>.spin
SPIN_PATTERN = re.compile(r"imap_(\d{4})_(\d{3})_(\d{4})_(\d{3})_(\d+)\.spin$")
# imap_<kind>[_odNNN]_<yyyymmdd>_<yyyymmdd>_v<version>.bsp
SPK_PATTERN = re.compile(r"imap_(\w+?)_(?:od\d+_)?(\d{8})_(\d{8})_v(\d+)\.bsp$")


def year_doy(year, doy):
    """Datetime of the start of a day given as a year and day of year."""
    return datetime.strptime(f"{year}_{doy}", "%Y_%j")


def files_covering(paths, pattern, start, end, to_dates):
    """Files whose coverage overlaps [start, end], lowest version first."""
    covering = []
    for path in paths:
        match = pattern.match(path.name)
        if match is None:
            continue
        file_start, file_end = to_dates(match)
        if file_start <= end and file_end >= start:
            covering.append((int(match.group(match.re.groups)), path))
    return [path for _, path in sorted(covering)]


def furnish_kernels(spice_dir, start, end):
    """Load the kernels needed to orient the spacecraft over the window.

    Higher-versioned CKs are loaded last so that they take priority, and the
    ephemerides are loaded reconstructed-last for the same reason.
    """
    for pattern in ("lsk/*.tls", "sclk/*.tsc", "fk/*.tf"):
        for path in sorted(spice_dir.glob(pattern)):
            spiceypy.furnsh(str(path))

    cks = files_covering(
        sorted(spice_dir.glob("ck/*.ah.bc")),
        CK_PATTERN,
        start,
        end,
        lambda m: (year_doy(m[1], m[2]), year_doy(m[3], m[4]) + timedelta(days=1)),
    )
    if not cks:
        raise SystemExit(f"No attitude CK in {spice_dir / 'ck'} covers {start}-{end}")
    for path in cks:
        spiceypy.furnsh(str(path))

    # For the Sun marker only, so a missing ephemeris is not fatal
    spks = files_covering(
        sorted(spice_dir.glob("spk/imap_*.bsp")),
        SPK_PATTERN,
        start,
        end,
        lambda m: (
            datetime.strptime(m[2], "%Y%m%d"),
            datetime.strptime(m[3], "%Y%m%d"),
        ),
    )
    priority = {"nom": 0, "pred": 1, "recon": 2}
    for path in sorted(
        spks, key=lambda p: priority.get(SPK_PATTERN.match(p.name)[1], 0)
    ):
        spiceypy.furnsh(str(path))
    if (spice_dir / "spk/de440.bsp").exists():
        spiceypy.furnsh(str(spice_dir / "spk/de440.bsp"))

    return cks


def load_spin_tables(spice_dir, start, end):
    """Hand imap_processing the spin tables covering the window."""
    spins = files_covering(
        sorted(spice_dir.glob("spin/*.spin")),
        SPIN_PATTERN,
        start,
        end,
        lambda m: (year_doy(m[1], m[2]), year_doy(m[3], m[4]) + timedelta(days=1)),
    )
    if not spins:
        raise SystemExit(f"No spin table in {spice_dir / 'spin'} covers {start}-{end}")
    set_global_spin_table_paths(spins)
    return get_spin_data()


def spacecraft_axis_track(et, frame, vector):
    """Longitude and latitude of a spacecraft-fixed direction, in degrees."""
    rotated = np.atleast_2d(
        frame_transform(et, vector, SpiceFrame.IMAP_SPACECRAFT, frame)
    )
    lon_lat = np.rad2deg([spiceypy.reclat(row)[1:] for row in rotated])
    return lon_lat[:, 0] % 360, lon_lat[:, 1]


def boresight_track(et, name, frame, lo_pivot):
    """Longitude and latitude of an instrument boresight, in degrees.

    IMAP-Lo is the one instrument whose boresight is not fixed in the
    spacecraft: its pivot mechanism rotates it about the instrument X axis, so
    it needs the pivot angle rather than the frame's nominal boresight.
    """
    if name == "lo":
        lon_lat = np.atleast_2d(lo_instrument_pointing(et, lo_pivot, frame))
    else:
        lon_lat = np.atleast_2d(instrument_pointing(et, INSTRUMENTS[name], frame))
    return lon_lat[:, 0] % 360, lon_lat[:, 1]


def label_for(name, lo_pivot):
    """Legend label, noting the pivot angle for IMAP-Lo."""
    return f"lo (pivot {lo_pivot:g}°)" if name == "lo" else name


def mollweide(longitude, latitude):
    """Degrees to the radian coordinates matplotlib's Mollweide axes want."""
    return np.deg2rad((longitude + 180) % 360 - 180), np.deg2rad(latitude)


def plot_sky(axis, et, args, frame):
    """Draw the boresight tracks, the spin axis and the Sun."""
    handles = []
    for name, color in zip(args.instrument, plt.cm.tab10.colors):
        longitude, latitude = boresight_track(et, name, frame, args.lo_pivot)
        axis.scatter(*mollweide(longitude, latitude), s=1, alpha=0.6, color=color)
        handles.append(
            Line2D(
                [],
                [],
                marker="o",
                linestyle="",
                color=color,
                label=label_for(name, args.lo_pivot),
            )
        )

    longitude, latitude = spacecraft_axis_track(et, frame, np.array([0.0, 0.0, 1.0]))
    axis.scatter(*mollweide(longitude, latitude), s=25, marker="x", color="black")
    handles.append(
        Line2D([], [], marker="x", linestyle="", color="black", label="spin axis")
    )

    try:
        sun, _ = spiceypy.spkpos("SUN", et[0], frame.name, "LT+S", "IMAP")
    except spiceypy.utils.exceptions.SpiceyError:
        print("No ephemeris coverage for the Sun; skipping that marker")
    else:
        _, sun_longitude, sun_latitude = spiceypy.reclat(sun)
        axis.scatter(
            *mollweide(np.rad2deg(sun_longitude), np.rad2deg(sun_latitude)),
            s=180,
            marker="*",
            color="orange",
            edgecolor="black",
            zorder=5,
        )
        handles.append(
            Line2D(
                [],
                [],
                marker="*",
                linestyle="",
                color="orange",
                markeredgecolor="black",
                markersize=12,
                label="Sun",
            )
        )

    # The projection runs -180 to +180; label it in the 0-360 convention
    axis.set_xticklabels(
        [f"{tick % 360}°" for tick in range(-150, 151, 30)], fontsize=7
    )
    axis.grid(alpha=0.3)
    axis.legend(
        handles=handles,
        loc="lower right",
        bbox_to_anchor=(1.12, -0.1),
        fontsize="small",
    )


def plot_zoom(axis, spin_data, args, frame):
    """Draw a few rotations of one boresight against the spin starts."""
    name = args.instrument[0]
    start_met = spin_data["spin_start_met"].iloc[0]
    period = spin_data["actual_spin_period"].iloc[0]

    spins = spin_data[
        (spin_data["spin_start_met"] >= start_met)
        & (spin_data["spin_start_met"] < start_met + args.zoom_spins * period)
    ]
    et = str_to_et(spins["spin_start_utc"].iloc[0].replace(" ", "T")) + np.arange(
        0, args.zoom_spins * period, 0.05
    )
    _, latitude = boresight_track(et, name, frame, args.lo_pivot)
    seconds = et_to_met(et) - et_to_met(et[0])

    axis.plot(seconds, latitude, linewidth=1.2, color="tab:blue", label="sky latitude")

    # The same rotation as the spin table reports it, from the other product
    phase_axis = axis.twinx()
    phase = get_spacecraft_spin_phase(et_to_met(et))
    phase_axis.plot(
        seconds,
        get_spin_angle(phase, degrees=True),
        linewidth=1,
        color="tab:green",
        alpha=0.7,
        label="spin phase",
    )
    phase_axis.set_ylabel("spin phase (deg)", color="tab:green")
    phase_axis.tick_params(axis="y", colors="tab:green")

    for _, spin in spins.iterrows():
        offset = spin["spin_start_met"] - spins["spin_start_met"].iloc[0]
        axis.axvline(offset, color="tab:red", linestyle="--", linewidth=1)
        axis.annotate(
            f" spin {spin['spin_number']}\n {spin['actual_spin_period']:.4f} s",
            (offset, axis.get_ylim()[0]),
            fontsize="x-small",
            color="tab:red",
            va="bottom",
            ha="left",
        )

    axis.set(
        xlabel=f"seconds from spin {spins['spin_number'].iloc[0]}",
        ylabel=f"{label_for(name, args.lo_pivot)} latitude (deg)",
        title="Sky motion from the attitude CK against spin phase from the spin table",
        xlim=(0, seconds[-1]),
    )
    axis.grid(alpha=0.3)


def main():
    """Plot the sky track and the per-rotation zoom."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("start", help="Start of the window, e.g. 2026-01-08T16:38:38")
    parser.add_argument(
        "--minutes", type=float, default=30, help="Length of the window (default: 30)"
    )
    parser.add_argument(
        "--step", type=float, default=1.0, help="Sample spacing in seconds (default: 1)"
    )
    parser.add_argument(
        "--instrument",
        nargs="+",
        choices=list(INSTRUMENTS),
        default=["hi90", "hi45"],
        help="Boresights to trace; the first is used for the zoom panel",
    )
    parser.add_argument(
        "--frame",
        choices=["ECLIPJ2000", "J2000"],
        default="ECLIPJ2000",
        help="Frame the sky coordinates are expressed in (default: ECLIPJ2000)",
    )
    parser.add_argument(
        "--lo-pivot",
        type=float,
        default=90.0,
        metavar="DEGREES",
        help="IMAP-Lo pivot angle, only used with --instrument lo (default: 90, "
        "the nominal value, which puts the boresight in the spin plane)",
    )
    parser.add_argument(
        "--zoom-spins", type=int, default=3, help="Rotations in the lower panel"
    )
    parser.add_argument(
        "--output", type=Path, default=Path("sky_track.png"), help="Image to write"
    )
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start)
    end = start + timedelta(minutes=args.minutes)
    frame = SpiceFrame[args.frame]

    cks = furnish_kernels(SPICE_DIR, start, end)
    print(f"Attitude from {', '.join(path.name for path in cks)}")
    spin_data = load_spin_tables(SPICE_DIR, start, end)

    et = str_to_et(start.isoformat()) + np.arange(0, args.minutes * 60, args.step)
    met = et_to_met(et)
    spin_data = spin_data[
        spin_data["spin_start_met"].between(met[0], met[-1], inclusive="left")
    ]
    print(f"{len(et)} samples, {len(spin_data)} spins in the window")

    figure = plt.figure(figsize=(11, 10))
    sky_axis = figure.add_subplot(2, 1, 1, projection="mollweide")
    zoom_axis = figure.add_subplot(2, 1, 2)
    plot_sky(sky_axis, et, args, frame)
    sky_axis.set_title(
        f"IMAP boresight sky tracks, {start:%Y-%m-%d %H:%M:%S} + {args.minutes:g} min"
    )
    plot_zoom(zoom_axis, spin_data, args, frame)

    figure.tight_layout()
    figure.savefig(args.output, dpi=150)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
