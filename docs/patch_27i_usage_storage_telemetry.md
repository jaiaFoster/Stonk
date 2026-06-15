# Patch 27I - Usage and Storage Telemetry

Patch 27I measures actual read-only product usage and stored-section sizes
before future pruning decisions.

## Captured

- Cached dashboard shell/full loads
- Developer snapshot modes
- On-demand detail-section requests
- Dashboard section open/close events
- Copy/download export actions
- Snapshot size profiles and payload section byte counts

## Privacy Boundary

Telemetry stores only event names, section names, small allowlisted metadata,
timestamps, run IDs, and integer byte counts. It does not store holdings,
account numbers, order data, raw provider payloads, auth state, tokens, or
credentials.

## Reliability Boundary

Telemetry is optional and fail-safe. Recording errors are logged as warnings
and never block dashboard loads, runs, snapshots, or reports. Telemetry
diagnostics are read-only and never trigger provider calls.

## Diagnostics

`/api/dev/usage-telemetry` reports:

- Most and least requested detail sections
- Counts by event type
- Latest snapshot size profile
- Recent hot/full/raw and section-size trends

The endpoint uses existing developer-diagnostics token protection.
