import pytest

from pydantic_curator.utils import extract_criterion_schema_classes


def test_extract_criterion_schema_classes():
    schema_code = extract_criterion_schema_classes({'PriorTreatment'})
    print(schema_code)
