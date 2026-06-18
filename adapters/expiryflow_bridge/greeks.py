from datetime import date
import opengreeks.black76 as b76

RISK_FREE_RATE = 0.065  # RBI repo rate ~6.5%


def _time_to_expiry(ts_date: date, expiry: date) -> float:
    days = (expiry - ts_date).days
    return max(days / 365.0, 1 / 365.0)


def compute_greeks(spot: float, strike: float, iv: float,
                   ts_date: date, expiry: date, option_type: str) -> dict | None:
    """
    Compute Black-76 Greeks for an option.

    iv   : implied volatility as PERCENTAGE (e.g. 12.58 = 12.58% annual vol)
           Divided by 100 internally before passing to Black-76.
    spot : underlying spot price (used as forward proxy)
    """
    if not spot or not strike or not iv or iv <= 0:
        return None
    try:
        flag  = "c" if option_type == "CE" else "p"
        T     = _time_to_expiry(ts_date, expiry)
        r     = RISK_FREE_RATE
        sigma = iv / 100.0   # Dhan stores IV as percent; Black-76 needs decimal

        return {
            "delta": b76.delta(flag, spot, strike, T, r, sigma),
            "gamma": b76.gamma(flag, spot, strike, T, r, sigma),
            "theta": b76.theta(flag, spot, strike, T, r, sigma),
            "vega":  b76.vega( flag, spot, strike, T, r, sigma),
            "rho":   b76.rho(  flag, spot, strike, T, r, sigma),
            "iv":    iv,
            "spot":  spot,
        }
    except Exception:
        return None
