from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import erf, exp, log, pi, sqrt


class OptionType(StrEnum):
    CALL = "call"
    PUT = "put"


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def _normal_pdf(x: float) -> float:
    return exp(-0.5 * x * x) / sqrt(2.0 * pi)


@dataclass(frozen=True, slots=True)
class OptionAnalytics:
    price: float
    delta: float
    gamma: float
    theta_per_day: float
    vega_per_vol_point: float
    rho_per_rate_point: float


def black_scholes(
    spot: float,
    strike: float,
    time_to_expiry: float,
    risk_free_rate: float,
    volatility: float,
    option_type: OptionType,
    dividend_yield: float = 0.0,
) -> OptionAnalytics:
    if min(spot, strike, time_to_expiry, volatility) <= 0:
        raise ValueError("spot, strike, time and volatility must be positive")
    sqrt_t = sqrt(time_to_expiry)
    d1 = (
        log(spot / strike)
        + (risk_free_rate - dividend_yield + 0.5 * volatility * volatility) * time_to_expiry
    ) / (volatility * sqrt_t)
    d2 = d1 - volatility * sqrt_t
    discounted_spot = spot * exp(-dividend_yield * time_to_expiry)
    discounted_strike = strike * exp(-risk_free_rate * time_to_expiry)
    if option_type == OptionType.CALL:
        price = discounted_spot * _normal_cdf(d1) - discounted_strike * _normal_cdf(d2)
        delta = exp(-dividend_yield * time_to_expiry) * _normal_cdf(d1)
        rho = strike * time_to_expiry * exp(-risk_free_rate * time_to_expiry) * _normal_cdf(d2)
    else:
        price = discounted_strike * _normal_cdf(-d2) - discounted_spot * _normal_cdf(-d1)
        delta = exp(-dividend_yield * time_to_expiry) * (_normal_cdf(d1) - 1)
        rho = -strike * time_to_expiry * exp(-risk_free_rate * time_to_expiry) * _normal_cdf(-d2)
    gamma = exp(-dividend_yield * time_to_expiry) * _normal_pdf(d1) / (
        spot * volatility * sqrt_t
    )
    common_theta = -(discounted_spot * _normal_pdf(d1) * volatility) / (2 * sqrt_t)
    if option_type == OptionType.CALL:
        theta = common_theta - risk_free_rate * discounted_strike * _normal_cdf(d2) + dividend_yield * discounted_spot * _normal_cdf(d1)
    else:
        theta = common_theta + risk_free_rate * discounted_strike * _normal_cdf(-d2) - dividend_yield * discounted_spot * _normal_cdf(-d1)
    vega = discounted_spot * _normal_pdf(d1) * sqrt_t
    return OptionAnalytics(price, delta, gamma, theta / 365, vega / 100, rho / 100)


def implied_volatility(
    market_price: float,
    spot: float,
    strike: float,
    time_to_expiry: float,
    risk_free_rate: float,
    option_type: OptionType,
    dividend_yield: float = 0.0,
    tolerance: float = 1e-7,
) -> float:
    if market_price <= 0:
        raise ValueError("market price must be positive")
    low, high = 1e-6, 5.0
    for _ in range(120):
        mid = (low + high) / 2
        estimate = black_scholes(
            spot, strike, time_to_expiry, risk_free_rate, mid, option_type, dividend_yield
        ).price
        if abs(estimate - market_price) <= tolerance:
            return mid
        if estimate < market_price:
            low = mid
        else:
            high = mid
    raise ValueError("market price is outside the supported implied-volatility range")

