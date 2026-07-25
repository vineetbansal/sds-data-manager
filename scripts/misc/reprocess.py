"""
Script provided by Jon Niehof to reprocess certain dates for a product.
"""

import datetime
import logging
import time

import imap_data_access
import spacepy.time

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s:%(name)s:%(message)s")


now = datetime.datetime.now(tz=datetime.UTC).strftime('%Y%m%d')
instrument="lo"
data_level="l1b"
descriptor="de"
reprocess_from = "20260531"
reprocess_to = "20260531"
# reprocess_to = now
ndays = 1

print()
print(descriptor)
for st in spacepy.time.tickrange(reprocess_from, reprocess_to, deltadays=ndays).UTC:
	start_date = st.strftime('%Y%m%d')
	end_date = min((st + datetime.timedelta(days = (ndays-1))).strftime('%Y%m%d'), reprocess_to)
	print(f"Starting  {start_date} - {end_date}")
	imap_data_access.reprocess(
	    start_date=start_date,
	    end_date=end_date,
	    instrument=instrument,
	    data_level=data_level,
	    descriptor=descriptor,
	)
	print(f"Requested {start_date} - {end_date}")
	time.sleep(1)
