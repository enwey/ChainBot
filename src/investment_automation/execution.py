from __future__ import annotations

import ast
import base64
from dataclasses import dataclass
from typing import Optional

import requests

from .market_data import MarketDataClient
from .settings import Settings

WSOL_MINT = "So11111111111111111111111111111111111111112"


@dataclass
class ExecutionResult:
    executed: bool
    mode: str
    tx_hash: Optional[str] = None
    reason: Optional[str] = None


class TradeExecutor:
    def __init__(self, settings: Settings, market_data: MarketDataClient) -> None:
        self.settings = settings
        self.market_data = market_data
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "investment-automation/0.1"})

    def close(self) -> None:
        self.session.close()

    def runtime_status(self) -> dict[str, object]:
        return {
            "execution_mode": self.settings.execution_mode,
            "live_enabled": self.settings.is_live_mode,
            "buy_slippage_bps": self.settings.buy_slippage_bps,
            "sell_slippage_bps": self.settings.sell_slippage_bps,
        }

    def buy(self, token_mint: str, amount_usd: float) -> ExecutionResult:
        return self._execute("buy", token_mint, amount_usd)

    def sell(self, token_mint: str, amount_usd: float) -> ExecutionResult:
        return self._execute("sell", token_mint, amount_usd)

    def _execute(self, side: str, token_mint: str, amount_usd: float) -> ExecutionResult:
        if not self.settings.is_live_mode:
            return ExecutionResult(executed=True, mode="paper", reason=f"paper {side}")

        if not self.market_data.rugcheck_is_safe(token_mint):
            return ExecutionResult(executed=False, mode="live", reason="rugcheck rejected token")

        keypair = self._load_keypair()
        if keypair is None:
            return ExecutionResult(executed=False, mode="live", reason="missing or invalid SOLANA_PRIVATE_KEY")

        input_mint = WSOL_MINT if side == "buy" else token_mint
        output_mint = token_mint if side == "buy" else WSOL_MINT
        slippage = self.settings.buy_slippage_bps if side == "buy" else self.settings.sell_slippage_bps

        lamports = self._usd_to_lamports(amount_usd)
        try:
            quote = self.session.get(
                self.settings.jupiter_quote_url,
                params={
                    "inputMint": input_mint,
                    "outputMint": output_mint,
                    "amount": lamports,
                    "slippageBps": slippage,
                },
                timeout=15,
            )
            quote.raise_for_status()
            quote_payload = quote.json()

            swap = self.session.post(
                self.settings.jupiter_swap_url,
                json={
                    "quoteResponse": quote_payload,
                    "userPublicKey": str(keypair.pubkey()),
                    "wrapAndUnwrapSol": True,
                },
                timeout=20,
            )
            swap.raise_for_status()
            swap_payload = swap.json()

            tx_hash = self._sign_and_submit_swap(keypair, swap_payload["swapTransaction"])
            return ExecutionResult(executed=True, mode="live", tx_hash=tx_hash)
        except Exception as exc:
            return ExecutionResult(executed=False, mode="live", reason=str(exc))

    def _load_keypair(self):
        private_key = self.settings.solana_private_key
        if not private_key:
            return None
        try:
            from solders.keypair import Keypair

            private_key = private_key.strip()
            if private_key.startswith("["):
                return Keypair.from_bytes(bytes(ast.literal_eval(private_key)))
            return Keypair.from_base58_string(private_key)
        except Exception:
            return None

    def _sign_and_submit_swap(self, keypair, swap_transaction: str) -> str:
        from solana.rpc.api import Client
        from solders.transaction import VersionedTransaction

        raw_tx = base64.b64decode(swap_transaction)
        tx = VersionedTransaction.from_bytes(raw_tx)
        signature = keypair.sign_message(tx.message.to_bytes_versioned())
        signed_tx = VersionedTransaction.populate(tx.message, [signature])
        client = Client(self.settings.rpc_url)
        response = client.send_raw_transaction(bytes(signed_tx), opts={"skip_preflight": True})
        return str(response.value)

    def _usd_to_lamports(self, amount_usd: float) -> int:
        sol_amount = amount_usd / max(self.settings.sol_price_usd, 0.01)
        return int(sol_amount * 1_000_000_000)
