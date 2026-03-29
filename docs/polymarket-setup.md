# Polymarket Trading API Setup

## 1. Get Your Keys from Polymarket

Polymarket uses **proxy wallets** for accounts created via email/social login. All keys come from Polymarket's settings, not MetaMask.

### Find your proxy wallet address
1. Go to [polymarket.com](https://polymarket.com) and log in
2. Open Settings → your **deposit address** is your proxy wallet address

### Create API keys
1. Go to Polymarket Settings → **API Keys** section
2. Create new API relayer keys
3. You'll get:
   - **API Key** — your relayer API key
   - **API Secret** — for HMAC signing
   - **Passphrase** — API passphrase
   - **Secret** (private key) — the signer private key (starts with `0x`)

Save all of these — you'll need them for the `.env` file.

## 2. Add Keys to `.env`

Add these to your `.env` file in the repo root:

```env
# Polymarket execution (all from Polymarket Settings → API Keys)
POLYMARKET_PRIVATE_KEY=0xYOUR_SIGNER_SECRET_KEY
POLYMARKET_WALLET_ADDRESS=0xYOUR_PROXY_DEPOSIT_ADDRESS
POLYMARKET_API_KEY=your-api-key-here
POLYMARKET_API_SECRET=your-api-secret-here
POLYMARKET_API_PASSPHRASE=your-passphrase-here
```

| Variable | Where to find it |
|----------|-----------------|
| `POLYMARKET_PRIVATE_KEY` | API Keys → **Secret** (the signer private key) |
| `POLYMARKET_WALLET_ADDRESS` | Settings → **Deposit address** (proxy wallet) |
| `POLYMARKET_API_KEY` | API Keys → **API Key** |
| `POLYMARKET_API_SECRET` | API Keys → **API Secret** |
| `POLYMARKET_API_PASSPHRASE` | API Keys → **Passphrase** |

## 3. Install Dependencies

```bash
cd /home/dastiger/prediciton
python3 -m pip install -r requirements.txt --break-system-packages
```

## 4. Verify Setup

```bash
python3 -c "from clients.executor import polymarket_auth_available; print('PM auth:', polymarket_auth_available())"
```

Should print `PM auth: True`.

## 5. Test with a Small Order

```
BTC                          # Start the BTC watcher
BUY PM UP 1 0.50             # Buy 1 Up contract at $0.50
                              # Confirmation: "BUY 1 PM UP @ $0.50 ($0.50 total) — Type Y to confirm, N to cancel"
Y                             # Confirm execution
```

## Terminal Commands

| Command | Description |
|---------|-------------|
| `BUY PM UP 10 0.50` | Buy 10 Up contracts at $0.50 each |
| `BUY PM DOWN 5 0.40` | Buy 5 Down contracts at $0.40 each |
| `BUY PM UP 10 MKT` | Market buy 10 Up contracts |
| `SELL PM UP 10 0.55` | Sell 10 Up contracts at $0.55 |
| `SELL PM DOWN 5` | Sell 5 Down contracts |
| `POS` | Show current positions |
| `FUND PM 60` | Set Polymarket available cash to $60 |
| `FUND PCT 0.6` | Use 60% of funds for contract calculations |

## Security Notes

- **Never commit `.env` to git** (already in `.gitignore`)
- Use a **dedicated trading wallet** with only the funds you need
- All keys are only used locally — they never leave your machine
- All orders require Y/N confirmation before execution
