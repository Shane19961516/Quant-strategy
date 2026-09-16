from universal_quant.features.bar_structure import add_bar_structure
from universal_quant.features.breakout import add_breakout
from universal_quant.features.momentum import add_momentum
from universal_quant.features.trend import add_trend
from universal_quant.features.volatility import add_volatility
from universal_quant.features.volume import add_volume


def add_all_features(df):
    out = add_trend(df)
    out = add_momentum(out)
    out = add_volatility(out)
    out = add_breakout(out)
    out = add_volume(out)
    out = add_bar_structure(out)
    return out
