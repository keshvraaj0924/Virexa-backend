from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

Status = Literal["draft", "active", "paused", "archived"]


def wire_text(value: str, maximum: int) -> str:
    # JavaScript/Zod limits count UTF-16 code units; reject text PostgreSQL cannot
    # store instead of turning valid JSON into an encoding/database exception.
    try:
        units = len(value.encode("utf-16-le")) // 2
    except UnicodeEncodeError:
        raise ValueError("Text must contain valid Unicode.") from None
    if "\x00" in value or units > maximum:
        raise ValueError("Text exceeds the supported field limits.")
    return value


Name = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
    AfterValidator(lambda value: wire_text(value, 120)),
]
Description = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=1000),
    AfterValidator(lambda value: wire_text(value, 1000)),
]


class CreateWorkflow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    description: Description | None = None


class UpdateWorkflow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedVersion: int = Field(strict=True, gt=0)
    name: Name | None = None
    description: Description | None = None
    status: Status | None = None

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set.intersection({"name", "description", "status"}):
            raise ValueError("At least one workflow field must be updated.")
        if any(
            key in self.model_fields_set and getattr(self, key) is None
            for key in ("name", "status")
        ):
            raise ValueError("Name and status cannot be null.")
        return self


class ListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Status | None = None
    limit: int = Field(25, ge=1, le=100)
    cursor: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)]
        | None
    ) = None
