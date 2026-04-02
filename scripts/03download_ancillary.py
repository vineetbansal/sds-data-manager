from pprint import pprint
import imap_data_access


instrument_ancillaries =   {
      "codice": [
          "l2-lo-efficiency",
          "l2-lo-gfactor",
          "l2-lo-onboard-energy-bins",
          "l2-lo-onboard-energy-table",
          "l2-lo-onboard-mpq-cal",
          "lo-mpq-cal",
          "l2-hi-energy-table",
          "l2-hi-ialirt-efficiency",
          "l2-hi-omni-efficiency",
          "l2-hi-sectored-efficiency",
          "l2-hi-tof-table",
      ],
      "glows": [
          "exclusions-by-instr-team",
          "map-of-excluded-regions",   # .dat
          "map-of-uv-sources",         # .dat
          "suspected-transients",
      ],
      "hi": [
          "45sensor-cal-prod",
          "90sensor-cal-prod",
          "90sensor-esa-energies",
          "90sensor-esa-eta-fit-factors",
          "calibration-prod-config",
      ],
      "hit": [
          "sectored-dt0-factors",
          "sectored-dt1-factors",
          "sectored-dt2-factors",
          "sectored-dt3-factors",
          "standard-dt0-factors",
          "standard-dt1-factors",
          "standard-dt2-factors",
          "standard-dt3-factors",
          "summed-dt0-factors",
          "summed-dt1-factors",
          "summed-dt2-factors",
          "summed-dt3-factors",
      ],
      "idex": [
          "l2a-calibration-curve-t-rise",
          "l2a-calibration-curve-yield-params",
      ],
      "lo": [
          "bad-times",
          "efficiency-factor",
          "esa-eta-fit-factors",
          "good-times",
          "hydrogen-background",
          "oxygen-background",
          "pointing-file",
          "sweep-table",
      ],
      "mag": [
          "ialirt-calibration",
          "l1b-cal",
          "l1b-calibration",
          "l1b-lut",
          "l1d-calibration",
          "l2-calibration",
          "l2-calibration-matrices",
          "l2-norm-offsets",
      ],
      "swapi": [
          "esa-unit-conversion",
          "lut-notes",
      ],
      "swe": [
          "esa-lut",
          "eu-conversion",
          "l1b-in-flight-cal",
      ],
      "ultra": [
          "l1b-45sensor-back-pos-lookup",
          "l1b-45sensor-imgparams-lookup",
          "l1b-45sensor-leftslit-lookup",
          "l1b-45sensor-logistic-interpolation",
          "l1b-45sensor-rightslit-lookup",
          "l1b-45sensor-spbtphcorr",
          "l1b-45sensor-sptpphcorr",
          "l1b-45sensor-tdc-norm-lookup",
          "l1b-90sensor-imgparams-lookup",
          "l1b-90sensor-scattering-calibration-data",
          "l1b-90sensor-spbtphcorr",
          "l1b-90sensor-sptpphcorr",
          "l1b-egynorm-lookup",
          "l1b-scattering-thresholds-per-energy",
          "l1b-sensor-gf-blades",
          "l1b-sensor-gf-noblades",
          "l1b-yadjust-lookup",
          "l1c-45sensor-nominal-for-lookup",
          "l1c-45sensor-static-dead-times",
          "l1c-90sensor-dps-exposure",
          "l1c-90sensor-efficiencies",
          "l1c-90sensor-gf",
          "l1c-90sensor-sc-pointing-bsf",
          "l1c-90sensor-sc-pointing-bsf-test",
          "l1c-90sensor-sc-pointing-index",
          "l1c-90sensor-sc-pointing-index-test",
          "l1c-90sensor-sc-pointing-phi",
          "l1c-90sensor-sc-pointing-phi-test",
          "l1c-90sensor-sc-pointing-theta",
          "l1c-90sensor-sc-pointing-theta-test",
          "l1c-90sensor-static-dead-times",
          "l2-energy-bin-group-sizes",
      ],
  }

for instrument, ancillaries in instrument_ancillaries.items():
    for ancillary in ancillaries:
        files = imap_data_access.query(
            table="ancillary",
            instrument=instrument,
            descriptor=ancillary,
            version="latest",
        )
        if len(files) == 0:
            print(f"No files found for {instrument} {ancillary}")
        else:
            file = sorted(
                files, key=lambda x: (x["start_date"], x["version"]), reverse=True
            )[0]

            download_path = imap_data_access.download(file["file_path"])
            print(download_path)