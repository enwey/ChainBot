from __future__ import annotations

import ast
import base64
import time
from dataclasses import dataclass
from typing import Optional

import requests

from .market_data import MarketDataClient
from .runtime_safety import DependencyFailureRecord, RuntimeSafetyManager
from .settings import Settings

WSOL_MINT = "So11111111111111111111111111111111111111112"


@dataclass
class ExecutionResult:
    executed: bool
    mode: str
    tx_hash: Optional[str] = None
    reason: Optional[str] = None
    idempotency_key: Optional[str] = None


class TradeExecutor:
    def __init__(
        self,
        settings: Settings,
        market_data: MarketDataClient,
        *,
        safety_manager: RuntimeSafetyManager | None = None,
    ) -> None:
        self.settings = settings
        self.market_data = market_data
        self.safety_manager = safety_manager
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

    def buy(
        self, token_mint: str, amount_usd: float, *, idempotency_key: str | None = None
    ) -> ExecutionResult:
        return self._execute("buy", token_mint, amount_usd, idempotency_key=idempotency_key)

    def sell(
        self, token_mint: str, amount_usd: float, *, idempotency_key: str | None = None
    ) -> ExecutionResult:
        return self._execute("sell", token_mint, amount_usd, idempotency_key=idempotency_key)

    def _execute(
        self,
        side: str,
        token_mint: str,
        amount_usd: float,
        *,
        idempotency_key: str | None = None,
    ) -> ExecutionResult:
        if not self.settings.is_live_mode:
            return ExecutionResult(
                executed=True,
                mode="paper",
                reason=f"paper {side}",
                idempotency_key=idempotency_key,
            )

        if not self.market_data.rugcheck_is_safe(token_mint):
            return ExecutionResult(
                executed=False,
                mode="live",
                reason="rugcheck rejected token",
                idempotency_key=idempotency_key,
            )

        keypair = self._load_keypair()
        if keypair is None:
            return ExecutionResult(
                executed=False,
                mode="live",
                reason="missing or invalid SOLANA_PRIVATE_KEY",
                idempotency_key=idempotency_key,
            )

        input_mint = WSOL_MINT if side == "buy" else token_mint
        output_mint = token_mint if side == "buy" else WSOL_MINT
        slippage = (
            self.settings.buy_slippage_bps if side == "buy" else self.settings.sell_slippage_bps
        )

        lamports = self._usd_to_lamports(amount_usd)
        try:
            quote = self._request(
                "jupiter",
                "quote",
                self.session.get,
                self.settings.jupiter_quote_url,
                params={
                    "inputMint": input_mint,
                    "outputMint": output_mint,
                    "amount": lamports,
                    "slippageBps": slippage,
                },
                timeout=15,
            )
            quote_payload = quote.json()

            swap = self._request(
                "jupiter",
                "swap",
                self.session.post,
                self.settings.jupiter_swap_url,
                json={
                    "quoteResponse": quote_payload,
                    "userPublicKey": str(keypair.pubkey()),
                    "wrapAndUnwrapSol": True,
                },
                timeout=20,
            )
            swap_payload = swap.json()

            tx_hash = self._sign_and_submit_swap(keypair, swap_payload["swapTransaction"])
            return ExecutionResult(
                executed=True, mode="live", tx_hash=tx_hash, idempotency_key=idempotency_key
            )
        except Exception as exc:
            return ExecutionResult(
                executed=False,
                mode="live",
                reason=str(exc),
                idempotency_key=idempotency_key,
            )

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
        try:
            response = client.send_raw_transaction(bytes(signed_tx), opts={"skip_preflight": True})
        except Exception as exc:
            self._record_dependency_failure("solana_rpc", "submit_swap", exc)
            raise
        self._record_dependency_success("solana_rpc", "submit_swap")
        return str(response.value)

    def _usd_to_lamports(self, amount_usd: float) -> int:
        sol_amount = amount_usd / max(self.settings.sol_price_usd, 0.01)
        return int(sol_amount * 1_000_000_000)

    def _request(self, dependency: str, operation: str, method, url: str, **kwargs):
        try:
            response = method(url, **kwargs)
            response.raise_for_status()
        except Exception as exc:
            self._record_dependency_failure(dependency, operation, exc)
            raise
        self._record_dependency_success(dependency, operation)
        return response

    def _record_dependency_failure(self, dependency: str, operation: str, exc: Exception) -> None:
        if self.safety_manager is None:
            return
        self.safety_manager.record_dependency_failure(
            DependencyFailureRecord(
                component="execution",
                dependency=dependency,
                operation=operation,
                message=str(exc),
                occurred_ts=int(time.time()),
                failure_class=exc.__class__.__name__,
                metadata={},
            )
        )

    def _record_dependency_success(self, dependency: str, operation: str) -> None:
        if self.safety_manager is None:
            return
        self.safety_manager.record_dependency_success("execution", dependency, operation)
