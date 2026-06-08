from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Any, Callable


def _find_project_dotenv() -> str | None:
    """Walk upward from this module's directory looking for a .env file.

    We don't rely on python-dotenv's find_dotenv() because it walks from the
    caller's frame, which breaks when invoked via `python -c` or a different
    CWD. Walking from __file__ keeps .env autoload working regardless of
    where the user runs the CLI from.
    """
    import os
    walker = os.path.dirname(os.path.abspath(__file__))
    while walker and walker != os.path.dirname(walker):
        candidate = os.path.join(walker, ".env")
        if os.path.isfile(candidate):
            return candidate
        walker = os.path.dirname(walker)
    return None


try:
    from dotenv import load_dotenv
    # override=False: explicit shell env vars (TOKEN_RESEARCH_OFFLINE=1) beat
    # .env values. Without this an empty `TOKEN_RESEARCH_OFFLINE=` line in
    # .env would silently clobber the flag. CWD-based dotenv discovery also
    # fails when the CLI is invoked from outside the project root, so we walk
    # up from this file to find the project .env.
    _dotenv_path = _find_project_dotenv()
    if _dotenv_path:
        load_dotenv(_dotenv_path, override=False)
except ImportError:
    pass

from token_research.commands import (
    chain_report,
    compare,
    fairlaunch,
    flows,
    holders,
    identity,
    labels,
    liquidity,
    locks,
    momentum_backtest,
    momentum_rank,
    momentum_screen,
    pools,
    portfolio_build,
    portfolio_rebalance,
    price_history,
    prelaunch,
    price,
    relations,
    resolve,
    risk,
    score,
    screen,
    signal,
    staking,
    supply,
    trend,
    unlocks,
    yields,
)
from token_research.commands.deep_report import run as run_deep_report
from token_research.config import AppConfig
from token_research.serialization import deep_report_to_markdown, to_pretty_json


# CPD override flags shared by `risk`, `prelaunch`, `chain-report`.
_CPD_PARAMS: tuple[str, ...] = (
    "trade_size_pct",
    "nesting_depth",
    "primitive_a",
    "primitive_b",
    "cpd_chain_tier",
    "cpd_protocol_tier",
    "cpd_dapp_tier",
    "cpd_stage",
    "cpd_age_band",
    "cpd_code_band",
    "cpd_hacks_band",
)

# Position-sizing flags only the `risk` command consumes today.
_RISK_POSITION_PARAMS: tuple[str, ...] = (
    "fund_aum_usd",
    "max_pos_pct",
    "conviction",
)


def _add_cpd_args(sub: argparse.ArgumentParser, default_trade: float, default_nesting: int) -> None:
    sub.add_argument("--trade-size", type=float, default=default_trade, dest="trade_size_pct",
                     help=f"Trade size as fraction of portfolio (0.01..0.10). Default {default_trade}.")
    sub.add_argument("--nesting", type=int, default=default_nesting, dest="nesting_depth",
                     help=f"Nesting depth (1..10). Default {default_nesting}.")
    sub.add_argument("--primitive-a", type=int, default=None, dest="primitive_a",
                     help="DeFi primitive level (1..10) for axis A. Auto-inferred if omitted.")
    sub.add_argument("--primitive-b", type=int, default=None, dest="primitive_b",
                     help="DeFi primitive level (1..10) for axis B. Defaults to primitive-a.")
    sub.add_argument("--cpd-chain", choices=["I", "II", "III"], default=None, dest="cpd_chain_tier")
    sub.add_argument("--cpd-protocol", choices=["I", "II", "III"], default=None, dest="cpd_protocol_tier")
    sub.add_argument("--cpd-dapp", choices=["I", "II", "III"], default=None, dest="cpd_dapp_tier")
    sub.add_argument("--cpd-stage",
                     choices=["mvp", "alpha", "beta", "release", "audit"],
                     default=None, dest="cpd_stage")
    sub.add_argument("--cpd-age",
                     choices=["0-1", "1-2", "2-3", "3-5", ">5"],
                     default=None, dest="cpd_age_band")
    sub.add_argument("--cpd-code",
                     choices=["proprietary_new", "proprietary_old", "open_source_new", "open_source_old", "oso_audit"],
                     default=None, dest="cpd_code_band")
    sub.add_argument("--cpd-hacks",
                     choices=[">2", "2", "0-1", "0", "0_dev"],
                     default=None, dest="cpd_hacks_band")


def _risk_args(sub: argparse.ArgumentParser) -> None:
    _add_cpd_args(sub, default_trade=0.10, default_nesting=1)
    sub.add_argument("--fund-aum", type=float, default=None, dest="fund_aum_usd",
                     help="Fund AUM in USD. Enables position-sizing output.")
    sub.add_argument("--max-pos-pct", type=float, default=0.05, dest="max_pos_pct",
                     help="Max single-position cap as fraction of AUM. Default 0.05 (5%%).")
    sub.add_argument("--conviction", choices=["low", "medium", "high"], default="medium",
                     dest="conviction", help="Conviction multiplier. low=0.5x, medium=1.0x, high=1.5x. Default medium.")


def _prelaunch_args(sub: argparse.ArgumentParser) -> None:
    _add_cpd_args(sub, default_trade=0.05, default_nesting=2)
    sub.add_argument("--min-tvl", type=float, default=100_000.0, dest="min_tvl_usd",
                     help="Minimum TVL (USD) for a DeFiLlama protocol to be considered a match. Default $100K.")


def _chain_report_args(sub: argparse.ArgumentParser) -> None:
    _add_cpd_args(sub, default_trade=0.10, default_nesting=1)
    sub.add_argument("--min-pool-tvl", type=float, default=100_000.0, dest="min_pool_tvl",
                     help="Minimum TVL (USD) for a yield pool to be listed. Default $100K.")


def _trend_args(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--window", type=int, default=30, dest="window_days",
                     help="Window in days to diff the current snapshot against. Default 30.")
    sub.add_argument("--no-save", action="store_true", dest="no_save",
                     help="Compute deltas without persisting the current snapshot.")


def _signal_args(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--window", type=int, default=30, dest="window_days",
                     help="Window in days for trend-based components. Default 30.")


def _screen_args(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--min-tvl", type=float, default=500_000.0, dest="min_tvl",
                     help="Minimum protocol TVL (USD). Default $500K.")
    sub.add_argument("--max-tvl", type=float, default=500_000_000.0, dest="max_tvl",
                     help="Maximum protocol TVL (USD). Default $500M.")
    sub.add_argument("--category", type=str, default=None, dest="category",
                     help="DeFiLlama category filter (substring match, e.g. 'lending').")
    sub.add_argument("--stage3-top", type=int, default=30, dest="stage3_top",
                     help="How many growth-scored protocols to check on DexScreener. Default 30.")
    sub.add_argument("--stage4-top", type=int, default=15, dest="stage4_top",
                     help="How many market-filtered protocols to check holders. Default 15.")
    sub.add_argument("--top", type=int, default=10, dest="final_top",
                     help="Final result count. Default 10.")
    sub.add_argument("--cex-listed", action="store_true", dest="cex_listed",
                     help="Only include tokens held by known CEX wallets (Binance, Bybit, OKX, Coinbase, Kraken).")
    sub.add_argument("--min-holders", type=int, default=0, dest="min_holders",
                     help="Minimum token holder count (from Blockscout). E.g. 5000 filters abandoned tokens.")
    sub.add_argument("--min-volume", type=float, default=0.0, dest="min_volume",
                     help="Minimum 24h DEX volume (USD). E.g. 10000 filters illiquid/dead tokens.")


def _deep_report_args(sub: argparse.ArgumentParser) -> None:
    """deep-report adds markdown as a format option plus --no-persist."""
    sub.add_argument("--no-persist", action="store_true", help="Do not persist the generated report JSON.")


def _momentum_screen_args(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--min-tvl", type=float, default=1_000_000.0, dest="min_tvl",
                     help="Minimum protocol TVL (USD). Default $1M.")
    sub.add_argument("--max-tvl", type=float, default=10_000_000_000.0, dest="max_tvl",
                     help="Maximum protocol TVL (USD). Default $10B.")
    sub.add_argument("--top", type=int, default=200, dest="top_n",
                     help="Universe cap (top N by mcap). Default 200.")
    sub.add_argument("--min-dex-liq", type=float, default=1_000_000.0, dest="min_dex_liq",
                     help="Per-token DEX liquidity floor (USD). Default $1M.")
    sub.add_argument("--k", type=int, default=5, dest="k",
                     help="Number of longs and shorts each. Default 5.")
    sub.add_argument("--long-z", type=float, default=1.5, dest="long_z",
                     help="Long-leg z-score threshold. Default +1.5.")
    sub.add_argument("--short-z", type=float, default=-1.5, dest="short_z",
                     help="Short-leg z-score threshold. Default -1.5.")
    sub.add_argument("--long-funding-bps", type=float, default=0.0, dest="long_funding_bps",
                     help="Funding cost bps/yr applied to longs. Default 0.")
    sub.add_argument("--short-funding-bps", type=float, default=1500.0, dest="short_funding_bps",
                     help="Funding cost bps/yr applied to shorts. Default 1500 (15%% APR).")
    sub.add_argument("--no-persist", action="store_true", dest="no_persist",
                     help="Do not write the basket JSON to disk.")
    sub.add_argument("--beta-window", type=int, default=60, dest="beta_window",
                     help="Trailing daily-return window for β-vs-BTC. Default 60.")
    sub.add_argument("--beta-min-obs", type=int, default=20, dest="beta_min_obs",
                     help="Minimum overlapping daily returns required for real β. Default 20.")
    sub.add_argument("--btc-reference", type=str, default="btc", dest="btc_reference",
                     help="Reference cache key for BTC series. Default 'btc' (Binance BTC/USDT).")
    sub.add_argument("--beta-default", type=float, default=1.0, dest="beta_default",
                     help="β fallback when cache is missing or short. Default 1.0.")


def _momentum_rank_args(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--basket-file", type=str, default=None, dest="basket_file",
                     help="Path to a saved basket JSON. Defaults to the latest basket.")


def _price_history_args(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--since", type=str, default=None, dest="since",
                     help="Start date (YYYY-MM-DD). Default: 2 years before --end.")
    sub.add_argument("--end", type=str, default=None, dest="end",
                     help="End date (YYYY-MM-DD). Default: today (UTC).")
    sub.add_argument("--timeframe", choices=["1d", "1h", "4h"], default="1d", dest="timeframe",
                     help="OHLCV timeframe. Default 1d.")
    sub.add_argument("--from-basket", type=str, default=None, dest="from_basket",
                     help="Bulk fetch every position in a saved basket: tag, file path, or 'latest'.")
    sub.add_argument("--force", action="store_true", dest="force",
                     help="Refetch from --since even if a cache file already exists.")
    sub.add_argument("--max-parallel", type=int, default=5, dest="max_parallel",
                     help="Concurrent fetch workers (capped at 10). Default 5.")
    sub.add_argument("--exchange", type=str, default=None, dest="only_exchange",
                     help="Force a specific exchange id (binance|okx|bybit|coinbase|kraken).")
    sub.add_argument("--prefer-perp", action="store_true", dest="prefer_perp",
                     help="Skip spot, look for perpetual listing first.")
    sub.add_argument("--reference", type=str, default=None, dest="reference",
                     help="Fetch a reference asset (btc | eth | sol) directly from CEX. "
                          "Writes to prices/_reference/<name>.json.")


def _portfolio_build_args(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--target-beta", type=float, default=0.4, dest="target_beta",
                     help="Net portfolio β target. Default 0.4 (bullish-hedged).")
    sub.add_argument("--beta-window", type=int, default=60, dest="beta_window",
                     help="Daily-return window for β-vs-BTC. Default 60.")
    sub.add_argument("--beta-min-obs", type=int, default=20, dest="beta_min_obs")
    sub.add_argument("--cov-window", type=int, default=90, dest="cov_window",
                     help="Daily-return window for the covariance matrix. Default 90.")
    sub.add_argument("--shrinkage", type=float, default=0.2, dest="shrinkage",
                     help="Linear shrinkage intensity toward mean-var · I. Default 0.2.")
    sub.add_argument("--min-longs", type=int, default=10, dest="min_longs")
    sub.add_argument("--max-longs", type=int, default=15, dest="max_longs")
    sub.add_argument("--max-per-category", type=int, default=4, dest="max_per_category")
    sub.add_argument("--min-categories", type=int, default=3, dest="min_categories")
    sub.add_argument("--max-single-pct", type=float, default=0.15, dest="max_single_pct",
                     help="Cap on any single long position (fraction of gross). Default 0.15.")
    sub.add_argument("--alpha-shorts", type=int, default=0, dest="alpha_shorts",
                     help="Number of alpha shorts to add alongside the BTC hedge. Default 0.")
    sub.add_argument("--drift-threshold", type=float, default=0.05, dest="drift_threshold_pct",
                     help="Per-position drift triggering a rebalance trade. Default 0.05.")
    sub.add_argument("--rebalance-days", type=int, default=7, dest="rebalance_days")
    sub.add_argument("--horizon-months", type=int, default=3, dest="horizon_months")
    sub.add_argument("--btc-reference", type=str, default="btc", dest="btc_reference")
    sub.add_argument("--no-persist", action="store_true", dest="no_persist")
    sub.add_argument("--exclude", type=str, default=None, dest="exclude",
                     help="Comma-separated universe names to skip (e.g. 'FET,UNI').")
    sub.add_argument("--include-only", type=str, default=None, dest="include_only",
                     help="Comma-separated whitelist — restrict universe to these names.")
    sub.add_argument("--long-only", action="store_true", dest="long_only",
                     help="Skip the BTC hedge entirely. Gross = net = 100%% long, "
                          "no beta neutralization. --target-beta and --alpha-shorts ignored.")


def _portfolio_rebalance_args(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--drift-threshold", type=float, default=None, dest="drift_threshold_pct",
                     help="Override the threshold from the saved book. Default: book's value.")


def _momentum_backtest_args(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--start", type=str, default="2026-01-01", dest="start",
                     help="Backtest start (YYYY-MM-DD). Default 2026-01-01.")
    sub.add_argument("--end", type=str, default="2026-04-30", dest="end",
                     help="Backtest end (YYYY-MM-DD). Default 2026-04-30.")
    sub.add_argument("--rebalance", choices=["daily", "weekly", "monthly", "none"],
                     default="weekly", dest="rebalance",
                     help="Rebalance frequency. Default weekly.")
    sub.add_argument("--spot-bps", type=float, default=10.0, dest="spot_bps",
                     help="Spot taker fee, bps. Default 10.")
    sub.add_argument("--perp-bps", type=float, default=5.0, dest="perp_bps",
                     help="Perp taker fee, bps. Default 5.")
    sub.add_argument("--funding-apr", type=float, default=0.15, dest="funding_apr",
                     help="Annualized perp funding rate (decimal). Default 0.15.")
    sub.add_argument("--synthetic", action="store_true", dest="synthetic",
                     help="Use deterministic random-walk price series for missing positions.")
    sub.add_argument("--synthetic-seed", type=int, default=42, dest="synthetic_seed",
                     help="Seed for the synthetic price walk. Default 42.")


@dataclass(frozen=True)
class CommandSpec:
    """Wiring for one subcommand: its run() callable, extra argparse setup, and extra kwargs."""

    handler: Callable[..., Any]
    customize_parser: Callable[[argparse.ArgumentParser], None] | None = None
    extra_kwargs: tuple[str, ...] = ()
    format_choices: tuple[str, ...] = ("json",)
    optional_query: bool = False  # True → `query` becomes nargs="?" (e.g. price-history --reference)


# Single source of truth for all subcommands. `handler=None` means the command
# uses a custom code path (currently only deep-report).
COMMANDS: dict[str, CommandSpec] = {
    "resolve": CommandSpec(resolve.run),
    "identity": CommandSpec(identity.run),
    "price": CommandSpec(price.run),
    "pools": CommandSpec(pools.run),
    "liquidity": CommandSpec(liquidity.run),
    "supply": CommandSpec(supply.run),
    "holders": CommandSpec(holders.run),
    "flows": CommandSpec(flows.run),
    "labels": CommandSpec(labels.run),
    "locks": CommandSpec(locks.run),
    "staking": CommandSpec(staking.run),
    "unlocks": CommandSpec(unlocks.run),
    "relations": CommandSpec(relations.run),
    "yields": CommandSpec(yields.run),
    "compare": CommandSpec(compare.run),
    "risk": CommandSpec(risk.run, _risk_args, _CPD_PARAMS + _RISK_POSITION_PARAMS),
    "prelaunch": CommandSpec(prelaunch.run, _prelaunch_args, _CPD_PARAMS + ("min_tvl_usd",)),
    "chain-report": CommandSpec(chain_report.run, _chain_report_args, _CPD_PARAMS + ("min_pool_tvl",)),
    "trend": CommandSpec(trend.run, _trend_args, ("window_days", "no_save")),
    "signal": CommandSpec(signal.run, _signal_args, ("window_days",)),
    "score": CommandSpec(score.run),
    "fairlaunch": CommandSpec(fairlaunch.run),
    "screen": CommandSpec(
        screen.run,
        _screen_args,
        ("min_tvl", "max_tvl", "category", "stage3_top", "stage4_top", "final_top", "cex_listed", "min_holders", "min_volume"),
    ),
    "momentum-screen": CommandSpec(
        momentum_screen.run,
        _momentum_screen_args,
        (
            "min_tvl", "max_tvl", "top_n", "min_dex_liq", "k",
            "long_z", "short_z", "long_funding_bps", "short_funding_bps", "no_persist",
            "beta_window", "beta_min_obs", "btc_reference", "beta_default",
        ),
    ),
    "momentum-rank": CommandSpec(
        momentum_rank.run,
        _momentum_rank_args,
        ("basket_file",),
    ),
    "price-history": CommandSpec(
        price_history.run,
        _price_history_args,
        (
            "since", "end", "timeframe", "from_basket", "force",
            "max_parallel", "only_exchange", "prefer_perp", "reference",
        ),
        optional_query=True,
    ),
    "portfolio-build": CommandSpec(
        portfolio_build.run,
        _portfolio_build_args,
        (
            "target_beta", "beta_window", "beta_min_obs", "cov_window", "shrinkage",
            "min_longs", "max_longs", "max_per_category", "min_categories",
            "max_single_pct", "alpha_shorts", "drift_threshold_pct",
            "rebalance_days", "horizon_months", "btc_reference", "no_persist",
            "exclude", "include_only", "long_only",
        ),
    ),
    "portfolio-rebalance": CommandSpec(
        portfolio_rebalance.run,
        _portfolio_rebalance_args,
        ("drift_threshold_pct",),
        optional_query=True,  # default to "latest"
    ),
    "momentum-backtest": CommandSpec(
        momentum_backtest.run,
        _momentum_backtest_args,
        (
            "start", "end", "rebalance", "spot_bps", "perp_bps",
            "funding_apr", "synthetic", "synthetic_seed",
        ),
    ),
    # deep-report handled with a sentinel so main() knows to dispatch it via
    # run_deep_report + markdown rendering.
    "deep-report": CommandSpec(
        handler=None,  # type: ignore[arg-type]
        customize_parser=_deep_report_args,
        format_choices=("json", "markdown"),
    ),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="token-research", description="EVM-first crypto token research CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command, spec in COMMANDS.items():
        sub = subparsers.add_parser(command)
        if spec.optional_query:
            sub.add_argument("query", nargs="?", default="_ref",
                             help="Ticker, project name, or token address. Optional in --reference mode.")
        else:
            sub.add_argument("query", help="Ticker, project name, or token address.")
        sub.add_argument("--chain", default=None, help="Target chain, defaults to ethereum.")
        sub.add_argument("--address", default=None, help="Explicit token address override.")
        sub.add_argument("--with-premium", action="store_true", help="Enable premium provider slots in the registry.")
        sub.add_argument(
            "--format",
            choices=list(spec.format_choices),
            default="json",
            help="Output format.",
        )
        if spec.customize_parser is not None:
            spec.customize_parser(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = AppConfig.from_env()
        config.ensure_directories()
    except (ValueError, OSError) as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2

    if args.command == "deep-report":
        payload = run_deep_report(
            query=args.query,
            chain=args.chain,
            address=args.address,
            config=config,
            include_premium=args.with_premium,
            persist=not args.no_persist,
        )
        if args.format == "markdown":
            print(deep_report_to_markdown(payload))
        else:
            print(to_pretty_json(payload))
        return 0

    spec = COMMANDS[args.command]
    kwargs: dict[str, Any] = {
        "query": args.query,
        "chain": args.chain,
        "address": args.address,
        "config": config,
        "include_premium": args.with_premium,
    }
    for param in spec.extra_kwargs:
        if hasattr(args, param):
            kwargs[param] = getattr(args, param)
    payload = spec.handler(**kwargs).to_dict()
    print(to_pretty_json(payload))
    return 0
