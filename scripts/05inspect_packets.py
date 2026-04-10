import glob
import imap_data_access
from imap_processing import imap_module_directory
from imap_processing.utils import packet_file_to_datasets
from imap_processing.swe.utils.swe_utils import SWEAPID
from imap_processing.cli import Swe


xtce_document = (
    f"{imap_module_directory}/swe/packet_definitions/swe_packet_definition.xml"
)

packet_files = glob.glob(str(imap_data_access.config["DATA_DIR"]) + "/imap/swe/l0/*/*/*.pkts")

for packet_file in packet_files:
    packet_filename = packet_file.split("/")[-1]
    dependency_str = '[{"type": "spice", "files": ["naif0012.tls", "imap_sclk_0145.tsc"]}, {"type": "science", "files": ["' + packet_filename + '"]}]'
    swe = Swe("l1a", "sci", dependency_str, "20251007", None, "v001", False)

    # This steps loads the SPICE kernels etc so that `packet_file_to_datasets` succeeds.
    dependencies = swe.pre_processing()

    datasets_by_apid = packet_file_to_datasets(
        packet_file, xtce_document, use_derived_value=False
    )
    if SWEAPID.SWE_SCIENCE in datasets_by_apid:
        print(packet_file)

    # Just like we did `pre_processing`, we can do
    # processed_data = swe.do_processing(dependencies)
    # swe.post_processing(processed_data, dependencies)