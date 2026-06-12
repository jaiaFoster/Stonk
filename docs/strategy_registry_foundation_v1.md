# Strategy Registry Foundation v1

Registry gives future strategies one stable contract without external plugin discovery.

Each strategy declares:

- stable ID, label, version, display metadata
- enabled state
- universe
- `StrategyDataRequirement`
- normalized `StrategyResult`

Current adapters wrap existing Calendar, Skew Momentum Vertical, and Stock Momentum services. They do not duplicate or weaken strategy math.

Generic opportunity history stores scanner decisions separately from raw market facts and broker-detected active positions. Forward Factor should join this registry only after source-material formulas are formally specified.
