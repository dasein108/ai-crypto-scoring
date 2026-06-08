"""Revenue-vs-Risk model (CPD 2.0).

Direct translation of ``docs/Revenue_vs_Risk_(CPD 2.0).xlsx``. The workbook
defines three layered scores — project quality (CPD), primitive complexity
(DeFi matrix), and a final "how risky is this specific trade" formula —
which together quantify the risk side of a position so it can be compared
against the expected return (APY, fee yield, etc.) delivered by the
``yields`` command.

No network calls. Pure data + functions.

Glossary (Russian → English):
    Сделка            → Trade size (% of portfolio)
    Вложенность       → Nesting depth (how many composed primitives)
    CPD               → Cardinal Protocol Description — project quality score
    Стадия            → Stage
    Возраст           → Age
    Код               → Code
    Взломы            → Hacks
    Иное              → Other / custom
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from typing import Literal


# ---------------------------------------------------------------------------
# DeFi primitive complexity matrix
# ---------------------------------------------------------------------------

# Ordered lowest risk → highest. Index is 1-based in the spreadsheet but the
# enum value here is (level, name). Use DEFI_PRIMITIVES[level-1] to look up.
DEFI_PRIMITIVES: tuple[tuple[int, str], ...] = (
    (1, "Native coin staking"),
    (2, "Deposits (lending)"),
    (3, "Pools (AMMs, DEXs)"),
    (4, "Vaults"),
    (5, "Yield farming"),
    (6, "Derivatives 1 (perps)"),
    (7, "Derivatives 2 (options)"),
    (8, "Indices (complex derivatives)"),
    (9, "Compound indices (multi-dim derivatives)"),
    (10, "Other / custom"),
)


def defi_matrix(primitive_a: int, primitive_b: int) -> float:
    """Compounded risk multiplier for combining two DeFi primitives.

    From the spreadsheet: ``M[A, B] = (A + B) / 20``. Symmetric, range [0.1, 1.0].
    A larger value means the combined trade is structurally more complex and
    therefore riskier per unit of trade size.
    """
    a = _validate_primitive(primitive_a)
    b = _validate_primitive(primitive_b)
    return round((a + b) / 20.0, 4)


def _validate_primitive(level: int) -> int:
    if not isinstance(level, int) or level < 1 or level > 10:
        raise ValueError(f"primitive level must be int 1..10, got {level!r}")
    return level


# ---------------------------------------------------------------------------
# CPD — Cardinal Protocol Description score
# ---------------------------------------------------------------------------

# Each sub-score is clamped within its stated range. The workbook sums the
# three stack-tier scores (chain/protocol/dapp) with four independent
# categorical sub-scores (stage, age, code, hacks). Total ranges roughly
# from -7 up to +18; tiers quantize that into 5 buckets.

StackTier = Literal["I", "II", "III"]
Stage = Literal["mvp", "alpha", "beta", "release", "audit"]
AgeBand = Literal["0-1", "1-2", "2-3", "3-5", ">5"]
CodeBand = Literal["proprietary_new", "proprietary_old", "open_source_new", "open_source_old", "oso_audit"]
HacksBand = Literal[">2", "2", "0-1", "0", "0_dev"]


CHAIN_TIER_SCORE: dict[StackTier, int] = {"I": 3, "II": 2, "III": 1}
PROTOCOL_TIER_SCORE: dict[StackTier, int] = {"I": 2, "II": 1, "III": 0}

# Canonical chain → stack-tier map used by both `risk` and `prelaunch`.
# Ethereum mainnet is Tier I. Major L2s with own sequencer + fraud proofs
# + large TVL are Tier II. Younger / more experimental chains are Tier III.
CHAIN_STACK_TIER_MAP: dict[str, StackTier] = {
    "ethereum": "I",
    "base": "II",
    "arbitrum": "II",
    "optimism": "II",
    "polygon": "II",
    "bsc": "III",
    "avalanche": "III",
    "monad": "III",
}


def infer_chain_tier(chain: str | None) -> StackTier | None:
    if not chain:
        return None
    return CHAIN_STACK_TIER_MAP.get(chain.lower())
DAPP_TIER_SCORE: dict[StackTier, int] = {"I": 1, "II": 0, "III": -1}

STAGE_SCORE: dict[Stage, int] = {"mvp": -1, "alpha": 0, "beta": 1, "release": 2, "audit": 3}
AGE_SCORE: dict[AgeBand, int] = {"0-1": -1, "1-2": 0, "2-3": 1, "3-5": 2, ">5": 3}
CODE_SCORE: dict[CodeBand, int] = {
    "proprietary_new": -1,
    "proprietary_old": 0,
    "open_source_new": 1,
    "open_source_old": 2,
    "oso_audit": 3,
}
HACKS_SCORE: dict[HacksBand, int] = {
    ">2": -1,
    "2": 0,
    "0-1": 1,
    "0": 2,
    "0_dev": 3,
}

# Maximum total per the spreadsheet: 3 (chain I) + 2 (protocol I) + 1 (dapp I)
# + 3 + 3 + 3 + 3 (stage/age/code/hacks all at their max) = 18.
CPD_MAX_SCORE = 3 + 2 + 1 + 3 + 3 + 3 + 3  # = 18

# Tier thresholds as fractions of CPD_MAX_SCORE.
CPD_TIER_THRESHOLDS: tuple[tuple[str, float], ...] = (
    ("Tier-01", 1.00),
    ("Tier-02", 0.75),
    ("Tier-03", 0.50),
    ("Tier-04", 0.25),
    ("Tier-05", 0.0),   # floor — anything below 25% lands here
)

# Mapping from the quality tier to a "risk input" value used by the final
# risk formula. Best tier → lowest risk input (1); worst tier → highest (10).
CPD_TIER_RISK_INPUT: dict[str, int] = {
    "Tier-01": 1,
    "Tier-02": 3,
    "Tier-03": 6,
    "Tier-04": 8,
    "Tier-05": 10,
}


@dataclass
class CpdInputs:
    """Inputs for a CPD score computation. Missing fields default to 'unknown'
    equivalents, which score at 0 so they neither reward nor punish."""

    chain_tier: StackTier | None = None
    protocol_tier: StackTier | None = None
    dapp_tier: StackTier | None = None
    stage: Stage | None = None
    age_band: AgeBand | None = None
    code_band: CodeBand | None = None
    hacks_band: HacksBand | None = None


@dataclass
class CpdResult:
    inputs: dict[str, str | None]
    sub_scores: dict[str, int]
    total_score: int
    max_score: int
    percent_of_max: float
    tier: str
    risk_input: int
    explanation: list[str] = field(default_factory=list)


def compute_cpd(inputs: CpdInputs) -> CpdResult:
    """Compute the CPD score + quality tier + risk input from structured inputs.

    Any missing sub-score is treated as 0 (neutral). This lets callers hand
    over partial data without the model crashing — a coverage gap upstream
    simply means the project is assumed neutral on that axis.
    """
    explanation: list[str] = []
    sub_scores: dict[str, int] = {}

    def _score(label: str, mapping: dict, key, neutral: int = 0) -> int:
        if key is None:
            explanation.append(f"{label}: unknown → 0 (neutral)")
            return neutral
        if key not in mapping:
            explanation.append(f"{label}: invalid value {key!r} → 0")
            return neutral
        value = mapping[key]
        explanation.append(f"{label}: {key} → {value:+d}")
        return value

    sub_scores["chain"] = _score("chain_tier", CHAIN_TIER_SCORE, inputs.chain_tier)
    sub_scores["protocol"] = _score("protocol_tier", PROTOCOL_TIER_SCORE, inputs.protocol_tier)
    sub_scores["dapp"] = _score("dapp_tier", DAPP_TIER_SCORE, inputs.dapp_tier)
    sub_scores["stage"] = _score("stage", STAGE_SCORE, inputs.stage)
    sub_scores["age"] = _score("age", AGE_SCORE, inputs.age_band)
    sub_scores["code"] = _score("code", CODE_SCORE, inputs.code_band)
    sub_scores["hacks"] = _score("hacks", HACKS_SCORE, inputs.hacks_band)

    total = sum(sub_scores.values())
    percent = total / CPD_MAX_SCORE if CPD_MAX_SCORE else 0.0
    tier = _tier_for_score(total)
    risk_input = CPD_TIER_RISK_INPUT[tier]
    explanation.append(
        f"Total: {total}/{CPD_MAX_SCORE} ({percent * 100:.1f}%) → {tier} → risk input {risk_input}"
    )

    return CpdResult(
        inputs={
            "chain_tier": inputs.chain_tier,
            "protocol_tier": inputs.protocol_tier,
            "dapp_tier": inputs.dapp_tier,
            "stage": inputs.stage,
            "age_band": inputs.age_band,
            "code_band": inputs.code_band,
            "hacks_band": inputs.hacks_band,
        },
        sub_scores=sub_scores,
        total_score=total,
        max_score=CPD_MAX_SCORE,
        percent_of_max=round(percent, 4),
        tier=tier,
        risk_input=risk_input,
        explanation=explanation,
    )


def _tier_for_score(total: int) -> str:
    for tier, fraction in CPD_TIER_THRESHOLDS:
        threshold = CPD_MAX_SCORE * fraction
        # Tier-01 is strict equality to 100% (spreadsheet uses >= 1.0); we
        # keep the ≥ semantics for all but Tier-05 which is the floor.
        if total >= threshold:
            return tier
    return "Tier-05"


# ---------------------------------------------------------------------------
# Risk formula
# ---------------------------------------------------------------------------

@dataclass
class RiskInputs:
    trade_size_pct: float           # 0.01 .. 0.10 (or larger — we clamp-log)
    nesting_depth: int              # 1 .. 10
    cpd_risk_input: int             # 1 .. 10 (from CpdResult.risk_input)
    primitive_a: int                # 1 .. 10 (DeFi matrix row)
    primitive_b: int                # 1 .. 10 (DeFi matrix col)


@dataclass
class RiskResult:
    trade_score: int
    nesting_score: int
    cpd_risk_input: int
    base_risk: int           # ROUNDUP average of three scores
    defi_multiplier: float   # DeFi matrix lookup
    primitive_a_name: str
    primitive_b_name: str
    final_risk: int          # 1..10 — final ROUNDUP
    explanation: list[str] = field(default_factory=list)


def _trade_size_to_score(pct: float) -> int:
    """Map a trade size fraction (0.01 .. 0.10) to a 1..10 integer score.

    From the 'Сделка' lookup table in the Risk sheet. Values outside the
    nominal range clamp to 1/10 so the formula still produces a number.
    """
    if pct is None or pct <= 0:
        return 1
    # Spreadsheet scale: 0.01 → 1, 0.02 → 2, ..., 0.10 → 10.
    score = round(pct * 100)
    return max(1, min(10, score))


def _clamp_int(value: int, low: int = 1, high: int = 10) -> int:
    return max(low, min(high, int(value)))


def compute_risk(inputs: RiskInputs) -> RiskResult:
    """Compute the final risk score from trade size + nesting + CPD + primitives.

    Mirrors the spreadsheet formula:
        base = ceil((trade + nesting + cpd) / 3)
        mult = M[A, B]
        final = ceil(base * (1 + mult))
    """
    trade_score = _trade_size_to_score(inputs.trade_size_pct)
    nesting_score = _clamp_int(inputs.nesting_depth)
    cpd_risk = _clamp_int(inputs.cpd_risk_input)
    a = _validate_primitive(inputs.primitive_a)
    b = _validate_primitive(inputs.primitive_b)

    base = ceil((trade_score + nesting_score + cpd_risk) / 3)
    mult = defi_matrix(a, b)
    final = ceil(base * (1 + mult))
    final = _clamp_int(final)

    explanation = [
        f"trade {inputs.trade_size_pct * 100:.1f}% → {trade_score}",
        f"nesting {inputs.nesting_depth} → {nesting_score}",
        f"CPD risk input → {cpd_risk}",
        f"base = ceil(({trade_score}+{nesting_score}+{cpd_risk})/3) = {base}",
        f"primitives {a} ({DEFI_PRIMITIVES[a - 1][1]}) × {b} ({DEFI_PRIMITIVES[b - 1][1]}) → mult {mult}",
        f"final = ceil({base} × (1 + {mult})) = {final}",
    ]

    return RiskResult(
        trade_score=trade_score,
        nesting_score=nesting_score,
        cpd_risk_input=cpd_risk,
        base_risk=base,
        defi_multiplier=mult,
        primitive_a_name=DEFI_PRIMITIVES[a - 1][1],
        primitive_b_name=DEFI_PRIMITIVES[b - 1][1],
        final_risk=final,
        explanation=explanation,
    )


# ---------------------------------------------------------------------------
# Revenue vs risk
# ---------------------------------------------------------------------------

@dataclass
class RevenueVsRisk:
    apy_percent: float | None           # expected return (observed or implied)
    risk_score: int                     # 1..10 from compute_risk
    risk_adjusted_apy: float | None     # apy × (11 - risk) / 10
    risk_adjusted_apy_conservative: float | None  # apy / risk
    apy_per_unit_risk: float | None     # apy / risk (lower = worse deal)
    verdict: str


def combine_revenue_risk(apy_percent: float | None, risk_score: int) -> RevenueVsRisk:
    """Produce a few views of revenue-vs-risk given an APY and a 1..10 risk score.

    - ``risk_adjusted_apy``: linear weighting. APY × (11 - risk) / 10. Risk 1
      keeps 100% of APY, risk 10 keeps 10%.
    - ``risk_adjusted_apy_conservative``: divide APY by risk. Punishes risky
      trades more aggressively than the linear version.
    - ``apy_per_unit_risk``: same as conservative but named for clarity.
    """
    risk = max(1, min(10, int(risk_score)))

    if apy_percent is None:
        return RevenueVsRisk(
            apy_percent=None,
            risk_score=risk,
            risk_adjusted_apy=None,
            risk_adjusted_apy_conservative=None,
            apy_per_unit_risk=None,
            verdict="no_yield_data",
        )

    linear = apy_percent * (11 - risk) / 10
    conservative = apy_percent / risk

    if apy_percent <= 0:
        verdict = "negative_yield"
    elif risk <= 3 and apy_percent >= 8:
        verdict = "strong_reward_for_risk"
    elif risk >= 8 and apy_percent < 15:
        verdict = "poor_reward_for_risk"
    elif conservative >= 3:
        verdict = "acceptable"
    else:
        verdict = "marginal"

    return RevenueVsRisk(
        apy_percent=apy_percent,
        risk_score=risk,
        risk_adjusted_apy=round(linear, 3),
        risk_adjusted_apy_conservative=round(conservative, 3),
        apy_per_unit_risk=round(conservative, 3),
        verdict=verdict,
    )
