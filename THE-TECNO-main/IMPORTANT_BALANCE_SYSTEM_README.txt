TecnoGems V23 balance model

IMPORTANT:
- From this version forward, users.balance in data/site.db is stored internally in USD only.
- If pricing mode is SYP, the UI displays balance = USD balance * usd_syp_rate.
- Deposits paid in SYP are converted to USD before adding.
- Deposits paid in USD are added as USD.
- Orders with manual SYP prices are converted to USD before deduction.
- This prevents switching pricing mode from corrupting balances.

If old versions already corrupted a user balance, fix that user's balance from Admin > Users.
Example:
- If a user should have 10,000 SYP and rate is 10,000 SYP/USD, set their internal balance to 1.
- If a user should have $10, set their internal balance to 10.
