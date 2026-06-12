"""Forward Factor lifecycle remains manual-review-only until source exits are known."""


def forward_factor_lifecycle(row: dict) -> dict:
    return {**row, "lifecycle_action": "MANUAL REVIEW REQUIRED — SOURCE DOES NOT SPECIFY AUTOMATIC EXIT", "automatic_exit_enabled": False}
