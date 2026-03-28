# Polymarket Trading API Setup

## 1. Create/Use an Ethereum Wallet

Polymarket uses **proxy wallets** for accounts created via email/social login. Your trading funds live in this proxy wallet, not your MetaMask EOA.

To find your proxy wallet address:
1. Go to [polymarket.com](https://polymarket.com) and log in
2. Open Settings/Profile — your **deposit address** is your proxy wallet address

You'll need the **private key** associated with the account that controls this proxy wallet. If you signed up via MetaMask, export it from MetaMask: Settings > Security > Reveal Private Key.

## 2. Fund Your Wallet on Polygon

- **USDC.e on Polygon** — the trading currency on Polymarket
  - Bridge from Ethereum via [Polygon Bridge](https://wallet.polygon.technology/bridge) or buy directly on Polygon via an exchange
- **Small amount of POL** (Polygon's gas token) — needed for the one-time token allowance approval (~0.01 POL)

## 3. Approve Token Allowances (One-Time)

Before your first API trade, you must approve Polymarket's exchange contracts to spend your USDC and Conditional Tokens:

1. Go to [polymarket.com](https://polymarket.com) and connect your wallet
2. Place any small trade via the web UI — this triggers the approval prompts
3. Once approved, your API trades will work

## 4. Add Keys to `.env`

```env
# Polymarket execution
POLYMARKET_PRIVATE_KEY=0xYOUR_PRIVATE_KEY_HERE
POLYMARKET_WALLET_ADDRESS=0xYOUR_PROXY_WALLET_ADDRESS_HERE
```

- **`POLYMARKET_PRIVATE_KEY`** — the raw hex private key (with `0x` prefix) from the wallet you used to sign up on Polymarket
- **`POLYMARKET_WALLET_ADDRESS`** — your Polymarket **proxy/deposit address** (found in Polymarket settings), NOT your MetaMask address

## 5. Install Dependencies

```bash
cd /home/dastiger/prediciton
python3 -m pip install -r requirements.txt --break-system-packages
```

This installs `py-clob-client`, the official Polymarket CLOB client library.

## 6. First Run — API Credential Derivation

On first use, `py-clob-client` automatically derives your CLOB API credentials (key, secret, passphrase) from your private key via an EIP-712 signature. This happens once and is cached in memory for the session.

## 7. Test with a Small Order

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
- The private key is only used locally — it never leaves your machine
- All orders require Y/N confirmation before execution
