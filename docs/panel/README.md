# Panel onboarding · USD230

The registration bot exposes `MI PANEL DE SEÑALES` / `MY SIGNALS PANEL` only after an independently validated broker ID and at least USD230 of validated deposits in that same broker account, with its broker level Premium or Prestige. A pending replacement ID, pending receipt, legacy global amount, or deposits split across brokers never qualify.

After successful normal administrator deposit confirmation, the new isolated helper delivers a panel reference image, the welcome text and three links: the authenticated download portal, the Telegraph installation guide in the user's language, and the account page. An additive `panel_onboarding_state` receipt prevents repeated automatic welcome messages after later validated deposits. Users may reopen the package intentionally from their member menu. The callback rechecks eligibility.

This delivery does not create/approve a portal account, start or reset a trial, grant any desktop broker, register a payment, or modify VIP channel entitlement. Portal approval remains with Johanna. The package tells the user to create their account and send its email for linking and approval; never its password. Download authorization remains on the existing authenticated portal. Monthly license expiry remains independent of Premium membership.

Photo errors fall back to the same three buttons in a text message. Delivery failures are logged with PANEL_ONBOARDING_FAILED and never undo a validated VIP deposit or grant a portal license; the package can be retried from the member menu. Logs never include credentials.

Public guides:
- Spanish: https://telegra.ph/JOHAALETRADER--Guía-de-instalación-y-acceso-al-panel-11-11
- English: https://telegra.ph/JOHAALETRADER--Panel-installation-and-access-guide-11-11

The guides use the real 0.76.18 reference screenshot; current installer 0.76.19 remains published separately in the owner's portal. No installer binary or credentials are published here. Telegraph ownership credentials are retained privately, outside this public repository.

Validation: `python -m unittest discover -s tests -v` with repository requirements installed. Offline SQLAlchemy state and mocked Telegram only. The optional baseline AST comparison additionally requires `main.original.py` and verifies the earlier promotion, deposit, VIP and referral code, allowing only the isolated post-deposit notification call. No live user sends are used as tests.
