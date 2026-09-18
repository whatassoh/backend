from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class AIUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None


@dataclass(frozen=True, kw_only=True)
class PromptReference:
    name: str
    version: str

    @property
    def identifier(self) -> str:
        return f"{self.name}:{self.version}"
