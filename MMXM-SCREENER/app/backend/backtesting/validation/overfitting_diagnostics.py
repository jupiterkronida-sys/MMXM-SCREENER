"""Overfitting diagnostics — PBO, DSR, and parameter sensitivity analysis.

Re-exports compute_pbo from cpcv_engine and adds Deflated Sharpe Ratio (DSR)
and parameter sensitivity grid search.
"""
import math
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List

import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "backtest_config.yaml"


def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# Re-export PBO from cpcv_engine (avoid circular imports)
from backtesting.validation.cpcv_engine import compute_pbo


def deflated_sharpe_ratio(
    observed_sharpe: float,
    num_trials: int,
    num_observations: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
    sharpe_variance: float = 1.0,
) -> float:
    """Compute Deflated Sharpe Ratio (Bailey & López de Prado, 2014).

    DSR adjusts the observed Sharpe ratio for multiple testing (data mining).

    Args:
        observed_sharpe: Annualized Sharpe of the strategy.
        num_trials: Number of independent trials/strategies tested.
        num_observations: Number of return observations.
        skewness: Skewness of returns (0 for normal).
        kurtosis: Kurtosis of returns (3 for normal).
        sharpe_variance: Variance of Sharpe estimates.

    Returns:
        DSR probability [0, 1] — likelihood strategy is not overfitted.
        Higher values are better (>0.95 = significant).
    """
    from scipy.stats import norm

    if num_trials < 1 or num_observations < 2:
        return 0.0

    e_max_sr = _expected_max_sharpe(num_trials, num_observations)

    # Variance of Sharpe estimate (accounting for non-normality)
    var_sr = sharpe_variance * (
        1 + (skewness * observed_sharpe) +
        ((kurtosis - 1) / 4) * observed_sharpe ** 2
    )
    var_sr = max(var_sr, 1e-12)

    se_sr = math.sqrt(var_sr / num_observations)

    if se_sr < 1e-12:
        return 0.0

    # DSR = probability that true SR > E[max SR]
    z = (observed_sharpe - e_max_sr) / se_sr
    dsr = norm.cdf(z)

    return dsr


def _expected_max_sharpe(num_trials: int, num_observations: int) -> float:
    """Expected maximum Sharpe ratio under the null (zero true SR).

    Uses approximation from Bailey & López de Prado (2014):
      E[max SR] ~ (1 - gamma) * Phi^{-1}(1 - 1/N) + gamma * Phi^{-1}(1 - 1/(N*e))
    where gamma = Euler-Mascheroni constant (0.5772...)
    """
    from scipy.stats import norm

    if num_trials < 1:
        return 0.0

    gamma = 0.5772156649  # Euler-Mascheroni constant

    # Expected max of N i.i.d. standard normals (approximation)
    z1 = norm.ppf(1.0 - 1.0 / num_trials)
    z2 = norm.ppf(1.0 - 1.0 / (num_trials * math.e))

    e_max = (1 - gamma) * z1 + gamma * z2
    return max(e_max / math.sqrt(num_observations), 0.0)


@dataclass
class SensitivityPoint:
    param_name: str
    param_value: Any
    sharpe: float
    total_return_pct: float
    max_drawdown_pct: float
    num_trades: int


@dataclass
class SensitivityResult:
    """Results from sweeping a single parameter."""
    param_name: str
    base_value: Any
    points: List[SensitivityPoint] = field(default_factory=list)
    sharpe_range: float = 0.0       # max - min Sharpe
    sharpe_stability: float = 0.0    # std of Sharpe across sweep
    instability_flag: bool = False   # True if Sharpe varies wildly


def parameter_sensitivity(
    param_name: str,
    param_values: List[Any],
    base_config: Dict[str, Any],
    run_fn: Callable[[Dict[str, Any]], float],
    stability_threshold: float = 1.0,
) -> SensitivityResult:
    """Run parameter sensitivity analysis by sweeping a single parameter.

    Args:
        param_name: Name of the parameter being swept.
        param_values: Values to test.
        base_config: Base config dict; param_name will be overridden.
        run_fn: Callable(config) -> Sharpe ratio (or other metric).
        stability_threshold: If Sharpe range > threshold, flag as unstable.

    Returns:
        SensitivityResult with all sweep points and instability flag.
    """
    base_value = base_config.get(param_name)
    points: List[SensitivityPoint] = []
    sharpes: List[float] = []

    for val in param_values:
        cfg = dict(base_config)
        cfg[param_name] = val
        try:
            sharpe = run_fn(cfg)
        except Exception as e:
            logger.warning("Sensitivity sweep failed for %s=%s: %s",
                           param_name, val, e)
            sharpe = 0.0

        sharpes.append(sharpe)
        points.append(SensitivityPoint(
            param_name=param_name,
            param_value=val,
            sharpe=sharpe,
            total_return_pct=0.0,   # Not computed in sweep
            max_drawdown_pct=0.0,
            num_trades=0,
        ))

    sharpe_range = max(sharpes) - min(sharpes) if sharpes else 0.0
    sharpe_stability = math.sqrt(
        sum((s - sum(sharpes) / len(sharpes)) ** 2 for s in sharpes) / len(sharpes)
    ) if len(sharpes) > 1 else 0.0

    instability_flag = sharpe_range > stability_threshold

    return SensitivityResult(
        param_name=param_name,
        base_value=base_value,
        points=points,
        sharpe_range=sharpe_range,
        sharpe_stability=sharpe_stability,
        instability_flag=instability_flag,
    )


def numba_trials_estimate(n_parameters: int, n_values_per_param: int = 5) -> int:
    """Estimate effective number of trials for multiple-testing correction.

    Non-independent parameter sweeps inflate the trial count.
    This uses a conservative approximation:
      effective_trials = n_parameters * n_values_per_param * 0.5
    """
    return max(1, int(n_parameters * n_values_per_param * 0.5))
