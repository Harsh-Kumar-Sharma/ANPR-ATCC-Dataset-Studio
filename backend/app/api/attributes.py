"""What a box can carry besides its class.

Served rather than duplicated in the canvas. The server checks incoming
attributes against this same list, so a UI built from it cannot offer a
value the save will refuse - which is the failure mode a hard-coded copy
on each side produces the first time the two drift.

Not under a project: unlike classes, these are properties of the domain
rather than of what a particular project counts.
"""

from fastapi import APIRouter

from app.schemas.annotation_attribute import AttributeDefinitionRead
from app.services.annotation_attributes import ATTRIBUTE_DEFINITIONS

attributes_router = APIRouter(prefix="/annotation-attributes", tags=["attributes"])


@attributes_router.get("", response_model=list[AttributeDefinitionRead])
def list_attribute_definitions() -> list[dict]:
    """Every attribute a box can carry, in the order the panel shows them."""
    return ATTRIBUTE_DEFINITIONS
