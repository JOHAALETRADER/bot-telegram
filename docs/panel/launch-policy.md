# Panel launch policy — 11 November 2026 (Colombia)

The first successful startup snapshots existing Premium/Prestige members from persisted global/broker levels. The snapshot and activation time are stored once; subsequent restarts never expand this cohort. Previously delivered panel packages retain access; existing portal licence dates are never modified.

- Existing cohort: a new validated deposit >= USD100 in an ID-validated account.
- New members / Basic upgrades: >= USD230 as a single deposit, or eligible accumulation reaching USD230 according to the existing preview/window rules. Lifetime totals and cross-broker totals do not qualify.
- A qualifying deposit creates a durable pending panel review in the same transaction as the deposit, using a savepoint. The owner separately confirms from the proof that the deposit was performed on/after 11 November 2026 (Colombia), after ID validation, and was reported by the member. Validation time is never treated as deposit time.
- Approval stores both attestations and the reviewer, then enables package delivery. Rejection preserves the validated deposit and VIP benefits. Callback replay does not duplicate deposits or automatic packages.
- A changed or pending account ID blocks panel approval/delivery. No panel callback grants a portal licence, resets a trial or changes channels.

Owner operation: validate the broker deposit using the existing flow, then use the additional panel date/ID confirmation card. `/panelpendientes` restores pending cards after message loss or restart. Users can retry package delivery from My JT Space after a Telegram delivery error.

Rollback scope: source changes only; never delete snapshot/review tables or alter pre-existing VIP data. The previous source revision can be restored, but its historical USD230 delivery gate does not implement the launch policy.

Delivery audit: successful member package sends trigger a separate owner confirmation. An owner-notice failure never repeats the member package or changes the deposit; the notice can retry on the next delivery check. The receipt means Telegram accepted the package, not that the member downloaded or installed it.

Public panel entry: https://t.me/JOHAALETRADER_bot?start=panel (Spanish default; preserves an existing language), and https://t.me/JOHAALETRADER_bot?start=panel_en (English). These entries display public information and a registration button, never grant access or reset registration.
