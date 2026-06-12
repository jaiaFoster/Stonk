"""Central provider-call budget shared by all strategies in one run."""

from dataclasses import dataclass, field


@dataclass
class ProviderBudget:
    max_requests: int
    used: int = 0
    by_type: dict[str, int] = field(default_factory=dict)

    def consume(self, data_type: str, amount: int = 1) -> bool:
        if self.used + amount > self.max_requests:
            return False
        self.used += amount
        self.by_type[data_type] = self.by_type.get(data_type, 0) + amount
        return True

    @property
    def remaining(self) -> int:
        return max(0, self.max_requests - self.used)
