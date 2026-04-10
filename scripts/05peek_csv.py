import csv

from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency import (
    DataSource,
    DataType,
    Relationship,
)

with open(
    "../sds_data_manager/lambda_code/SDSCode/pipeline_lambdas/dependency_config.csv"
) as f:
    reader = csv.DictReader(
        filter(lambda row: row[0] != "#", f),
        fieldnames=[
            "from_src",
            "from_dtype",
            "from_desc",
            "to_src",
            "to_dtype",
            "to_desc",
            "edge",
            "edge_direction",
        ],
    )

    from_src = set()
    to_src = set()
    from_dtype = set()
    to_dtype = set()
    from_desc = set()
    to_desc = set()
    edge = set()

    for row in reader:
        from_src.add(row["from_src"].strip())
        to_src.add(row["to_src"].strip())
        from_dtype.add(row["from_dtype"].strip())
        to_dtype.add(row["to_dtype"].strip())
        from_desc.add(row["from_desc"].strip())
        to_desc.add(row["to_desc"].strip())
        edge.add(row["edge"].strip())

        assert row["edge_direction"].strip() == "DOWNSTREAM"


assert all(_from_src in DataSource().valid_source for _from_src in from_src)
assert all(_to_src in DataSource().valid_source for _to_src in to_src)

assert all(_from_dtype in DataType().valid_type for _from_dtype in from_dtype)
assert all(_to_dtype in DataType().valid_type for _to_dtype in to_dtype)

assert all(_edge in Relationship().valid_relationship for _edge in edge)

n_from_in_to = set()
n_from_not_in_to = set()
for _from_desc in from_desc:
    if _from_desc in to_desc:
        n_from_in_to.add(_from_desc)
    else:
        n_from_not_in_to.add(_from_desc)

