"""Option metadata."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from hyprland_schema import HyprOption


@dataclass(frozen=True, slots=True)
class OptionInfo:
    """Metadata about a Hyprland config option, derived from schema."""

    key: str
    type: str  # bool, int, float, string, color, gradient, vec2, choice
    default: Any
    description: str = ""
    min: int | float | None = None
    max: int | float | None = None
    enum_values: tuple[str, ...] | None = None

    @classmethod
    def from_schema(cls, opt: "HyprOption") -> Self:
        """Create from a ``hyprland_schema.HyprOption``."""
        return cls(
            key=opt.key,
            type=opt.type,
            default=opt.default,
            description=opt.description,
            min=opt.min,
            max=opt.max,
            enum_values=opt.enum_values,
        )

    def validate(self, value: Any) -> str | None:
        """Check *value* against schema constraints.

        Returns an error message string if validation fails, or ``None`` if the
        value is acceptable.
        """
        if self.type in ("int", "float", "choice"):
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                return f"expected a numeric value for {self.key!r}, got {value!r}"
            if self.min is not None and numeric < self.min:
                return f"value {value} for {self.key!r} is below minimum {self.min}"
            if self.max is not None and numeric > self.max:
                return f"value {value} for {self.key!r} is above maximum {self.max}"

        if self.enum_values is not None and str(value) not in self.enum_values:
            return f"value {value!r} for {self.key!r} is not one of {self.enum_values}"

        return None
