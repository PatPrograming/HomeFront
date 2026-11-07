"""Base model for data that crosses a boundary (DynamoDB items, API events).

Python attributes are snake_case (PEP 8); the JSON on the wire is camelCase
(brief §6.3, §6.4), the same spelling the TypeScript types use. Models accept
either spelling and always serialize camelCase.
"""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class WireModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, serialize_by_alias=True)
