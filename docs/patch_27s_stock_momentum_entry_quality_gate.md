# Patch 27S - Stock Momentum Entry Quality Gate

Patch 27S separates strong raw momentum from a buyable stock entry.

Stock momentum rows now expose:

- `entry_quality`
- 50-day and 200-day extension
- 30-day realized volatility
- bucket support
- suggested entry type
- initial stop
- take-profit/trailing-exit guidance
- max position-size hint
- `add_allowed_boolean`
- explicit add blockers

Clean `CONSIDER ADDING` language requires complete market data, positive 3M and
6M returns, positive relative strength versus QQQ, price above the 50D and
200D trends, acceptable 50D extension and volatility, bucket support or clear
leadership, available stop/profit guidance, and acceptable current allocation.

Extended names become pullback/watch rows. High-volatility names become starter
only. Leveraged ETFs remain tactical only. Daily Opportunity cannot upgrade a
blocked stock momentum row into a clean add.

## Scope

- Stock momentum and unified stock-add output only.
- No options strategy changes.
- No trade execution.
- Existing strategy score remains visible.
