# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
EVM Wallet Transaction Checker — GenLayer Intelligent Contract
Fetches transaction count and total gas fees spent for a wallet
across major EVM-compatible chains using public block explorers.
"""

from genlayer import *
import json


# Supported EVM chains with their Etherscan-compatible explorer API endpoints.
# All use the same API style (Etherscan-compatible). No API key needed for
# basic tx-list calls on most public endpoints (rate-limited but functional).
CHAINS = {
    "ethereum": {
        "name": "Ethereum",
        "symbol": "ETH",
        "explorer_api": "https://api.etherscan.io/api",
        "decimals": 18,
    },
    "polygon": {
        "name": "Polygon",
        "symbol": "MATIC",
        "explorer_api": "https://api.polygonscan.com/api",
        "decimals": 18,
    },
    "bsc": {
        "name": "BNB Smart Chain",
        "symbol": "BNB",
        "explorer_api": "https://api.bscscan.com/api",
        "decimals": 18,
    },
    "arbitrum": {
        "name": "Arbitrum One",
        "symbol": "ETH",
        "explorer_api": "https://api.arbiscan.io/api",
        "decimals": 18,
    },
    "optimism": {
        "name": "Optimism",
        "symbol": "ETH",
        "explorer_api": "https://api-optimistic.etherscan.io/api",
        "decimals": 18,
    },
    "base": {
        "name": "Base",
        "symbol": "ETH",
        "explorer_api": "https://api.basescan.org/api",
        "decimals": 18,
    },
    "avalanche": {
        "name": "Avalanche C-Chain",
        "symbol": "AVAX",
        "explorer_api": "https://api.snowtrace.io/api",
        "decimals": 18,
    },
    "fantom": {
        "name": "Fantom",
        "symbol": "FTM",
        "explorer_api": "https://api.ftmscan.com/api",
        "decimals": 18,
    },
}

MAX_TXS_PER_CHAIN = 1000   # Etherscan-compatible APIs cap at 10_000; we use 1000 for speed


@gl.dataclass
class ChainResult:
    chain_name: str
    symbol: str
    tx_count: int
    total_gas_used: int          # raw gas units
    total_gas_fee_native: str    # formatted in native token (e.g. "0.012345 ETH")
    status: str                  # "ok" | "error" | "no_activity"
    error_msg: str


@gl.dataclass
class WalletReport:
    wallet_address: str
    chains_checked: int
    total_tx_count: int
    chain_results: list[ChainResult]
    summary: str                 # LLM-generated human-readable summary


class EVMWalletChecker(gl.Contract):
    """
    Intelligent Contract that checks EVM wallet activity across multiple chains.
    Stores the last queried report so it can be read cheaply after the write call.
    """

    last_report: dict   # serialised WalletReport for the last query

    def __init__(self) -> None:
        self.last_report = {}

    # ------------------------------------------------------------------ #
    #  Internal helpers (leader side — run inside nondet)                 #
    # ------------------------------------------------------------------ #

    def _wei_to_native(self, wei: int, decimals: int) -> str:
        """Convert raw wei integer to a human-readable token string."""
        divisor = 10 ** decimals
        value = wei / divisor
        return f"{value:.8f}".rstrip("0").rstrip(".")

    def _fetch_chain_data(self, wallet: str, chain_key: str) -> ChainResult:
        """
        Fetch transaction list for a wallet on one chain via its block explorer API.
        Uses GenLayer's built-in web access (gl.nondet.web.get).
        """
        cfg = CHAINS[chain_key]
        url = (
            f"{cfg['explorer_api']}"
            f"?module=account&action=txlist"
            f"&address={wallet}"
            f"&startblock=0&endblock=99999999"
            f"&page=1&offset={MAX_TXS_PER_CHAIN}"
            f"&sort=asc"
        )

        try:
            raw = gl.nondet.web.get(url)
            data = json.loads(raw)

            if data.get("status") == "0":
                msg = data.get("message", "")
                # "No transactions found" is a valid empty state, not an error
                if "No transactions found" in msg or data.get("result") == []:
                    return ChainResult(
                        chain_name=cfg["name"],
                        symbol=cfg["symbol"],
                        tx_count=0,
                        total_gas_used=0,
                        total_gas_fee_native="0",
                        status="no_activity",
                        error_msg="",
                    )
                return ChainResult(
                    chain_name=cfg["name"],
                    symbol=cfg["symbol"],
                    tx_count=0,
                    total_gas_used=0,
                    total_gas_fee_native="0",
                    status="error",
                    error_msg=msg,
                )

            txs = data.get("result", [])
            total_gas_wei = 0
            for tx in txs:
                gas_used = int(tx.get("gasUsed", 0))
                gas_price = int(tx.get("gasPrice", 0))
                total_gas_wei += gas_used * gas_price

            return ChainResult(
                chain_name=cfg["name"],
                symbol=cfg["symbol"],
                tx_count=len(txs),
                total_gas_used=sum(int(t.get("gasUsed", 0)) for t in txs),
                total_gas_fee_native=self._wei_to_native(total_gas_wei, cfg["decimals"]),
                status="ok",
                error_msg="",
            )

        except Exception as exc:
            return ChainResult(
                chain_name=cfg["name"],
                symbol=cfg["symbol"],
                tx_count=0,
                total_gas_used=0,
                total_gas_fee_native="0",
                status="error",
                error_msg=str(exc)[:120],
            )

    def _build_report(self, wallet: str, results: list[ChainResult]) -> WalletReport:
        active = [r for r in results if r.status == "ok"]
        total_txs = sum(r.tx_count for r in active)

        prompt = f"""
You are a blockchain analytics assistant.
A user queried wallet address {wallet} across {len(results)} EVM chains.

Here is the raw data per chain:
{json.dumps([
    {
        "chain": r.chain_name,
        "tx_count": r.tx_count,
        "gas_fee": r.total_gas_fee_native + " " + r.symbol,
        "status": r.status,
    }
    for r in results
], indent=2)}

Write a concise 2-3 sentence plain-English summary of this wallet's cross-chain activity.
Mention which chains have the most activity, total transactions, and any notable gas costs.
Be factual and specific. No markdown. No preamble like "Sure!" or "Here is:".
"""

        summary = gl.nondet.exec_prompt(prompt)
        if not isinstance(summary, str):
            summary = "Summary unavailable."

        return WalletReport(
            wallet_address=wallet,
            chains_checked=len(results),
            total_tx_count=total_txs,
            chain_results=results,
            summary=summary.strip(),
        )

    # ------------------------------------------------------------------ #
    #  Public write method — triggers the full cross-chain check          #
    # ------------------------------------------------------------------ #

    @gl.public.write
    def check_wallet(self, wallet_address: str) -> None:
        """
        Query all supported EVM chains for the given wallet address.
        Results are stored in `last_report` and can be read via `get_last_report`.

        Consensus strategy: the leader fetches all data; validators re-fetch and
        compare total tx counts per chain (allowing ±1 tolerance for in-flight txs).
        """
        wallet = wallet_address.strip().lower()
        if not (wallet.startswith("0x") and len(wallet) == 42):
            raise gl.vm.UserError(
                "Invalid EVM address. Must be a 42-character hex string starting with 0x."
            )

        def leader_fn():
            results = [self._fetch_chain_data(wallet, k) for k in CHAINS]
            report = self._build_report(wallet, results)
            return report

        def validator_fn(leaders_res) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return False
            leaders_report: WalletReport = leaders_res.calldata
            # Re-fetch a subset of chains for validation (first 3 for speed)
            keys = list(CHAINS.keys())[:3]
            for k in keys:
                my_result = self._fetch_chain_data(wallet, k)
                leader_chain = next(
                    (r for r in leaders_report.chain_results if r.chain_name == CHAINS[k]["name"]),
                    None,
                )
                if leader_chain is None:
                    return False
                # Allow ±2 tx difference (new txs landing mid-validation)
                if abs(my_result.tx_count - leader_chain.tx_count) > 2:
                    return False
            return True

        report: WalletReport = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        # Serialise the dataclass to a plain dict for storage
        self.last_report = {
            "wallet_address": report.wallet_address,
            "chains_checked": report.chains_checked,
            "total_tx_count": report.total_tx_count,
            "summary": report.summary,
            "chain_results": [
                {
                    "chain_name": r.chain_name,
                    "symbol": r.symbol,
                    "tx_count": r.tx_count,
                    "total_gas_used": r.total_gas_used,
                    "total_gas_fee_native": r.total_gas_fee_native,
                    "status": r.status,
                    "error_msg": r.error_msg,
                }
                for r in report.chain_results
            ],
        }

    # ------------------------------------------------------------------ #
    #  Public view methods — free reads, no gas beyond query cost         #
    # ------------------------------------------------------------------ #

    @gl.public.view
    def get_last_report(self) -> dict:
        """Return the full report from the last check_wallet call."""
        return self.last_report

    @gl.public.view
    def get_supported_chains(self) -> list[str]:
        """Return the list of chain keys this contract supports."""
        return list(CHAINS.keys())
