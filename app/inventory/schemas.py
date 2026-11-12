from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

Quantity = Annotated[StrictInt, Field(gt=0, le=1_000_000)]
Identifier = Annotated[StrictInt, Field(gt=0)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def reject_control_characters(cls, value):
        if isinstance(value, str) and any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Уберите управляющие символы")
        return value


class WarehouseInput(Input):
    name: str = Field(min_length=1, max_length=80)


class MovementInput(Input):
    kind: Literal["receipt", "transfer", "fbs", "fbo"]
    product_id: Identifier
    quantity: Quantity
    source_id: Identifier | None = None
    destination_id: Identifier | None = None
    destination: str | None = Field(default=None, min_length=1, max_length=120)
    reference: str = Field(default="", max_length=80)

    @model_validator(mode="after")
    def validate_route(self) -> Self:
        if self.kind == "receipt":
            valid = self.source_id is None and self.destination_id and self.destination is None
        elif self.kind == "transfer":
            valid = (
                self.source_id
                and self.destination_id
                and self.source_id != self.destination_id
                and self.destination is None
            )
        else:
            valid = self.source_id and self.destination_id is None and self.destination
        if not valid:
            raise ValueError("Проверьте склад отправления и получателя")
        return self


class ActionInput(Input):
    action: Literal["ship", "receive", "cancel", "deliver"]
