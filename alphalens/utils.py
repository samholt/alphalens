#
# Copyright 2017 Quantopian, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import pandas as pd
import numpy as np
from IPython.display import display


class NonMatchingTimezoneError(Exception):
    pass


def non_unique_bin_edges_error(func):
    """
    Give user a more informative error in case it is not possible
    to properly calculate quantiles on the input dataframe (factor)
    """
    def dec(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ValueError as e:
            if 'Bin edges must be unique' in str(e):
                print("""
    It's NOT possible to compute the selected quantiles for the input provided.
    This usually happens when the input contains too many identical
    values and they span more than one quantile. The quantiles are choosen
    to have the same number of records each, but the same value cannot span
    multiple quantiles. Possible workarounds are:
    1 - Decrease the number of quantiles
    2 - Specify a custom quantiles range, e.g. [0, .50, .75, 1.] to get unequal
        number of records per quantile
    3 - Use 'bins' option instead of 'quantiles', 'bins' chooses the
        buckets to be evenly spaced according to the values themselves, while
        'quantiles' forces the buckets to have the same number of records.
    4 - for factors with discrete values use the 'bins' option with custom
        ranges and create a range for each discrete value
    Please see utils.get_clean_factor_and_forward_returns documentation for
    full documentation of 'bins' and 'quantiles' options.
                      """)
            raise
    return dec


@non_unique_bin_edges_error
def quantize_factor(factor_data, quantiles=5, bins=None, by_group=False):
    """
    Computes period wise factor quantiles.

    Parameters
    ----------
    factor_data : pd.DataFrame - MultiIndex
        A MultiIndex DataFrame indexed by date (level 0) and asset (level 1),
        containing the values for a single alpha factor, forward returns for
        each period, the factor quantile/bin that factor value belongs too, and
        (optionally) the group the asset belongs to.
    quantiles : int or sequence[float]
        Number of equal-sized quantile buckets to use in factor bucketing.
        Alternately sequence of quantiles, allowing non-equal-sized buckets
        e.g. [0, .10, .5, .90, 1.] or [.05, .5, .95]
        Only one of 'quantiles' or 'bins' can be not-None
    bins : int or sequence[float]
        Number of equal-width (valuewise) bins to use in factor bucketing.
        Alternately sequence of bin edges allowing for non-uniform bin width
        e.g. [-4, -2, -0.5, 0, 10]
        Only one of 'quantiles' or 'bins' can be not-None
    by_group : bool
        If True, compute quantile buckets separately for each group.

    Returns
    -------
    factor_quantile : pd.Series
        Factor quantiles indexed by date and asset.
    """

    def quantile_calc(x, _quantiles, _bins):
        if _quantiles is not None and _bins is None:
            return pd.qcut(x, _quantiles, labels=False) + 1
        elif _bins is not None and _quantiles is None:
            return pd.cut(x, _bins, labels=False) + 1
        raise ValueError('Either quantiles or bins should be provided')

    grouper = [factor_data.index.get_level_values('date')]
    if by_group:
        grouper.append('group')

    factor_quantile = factor_data.groupby(grouper)['factor'] \
        .apply(quantile_calc, quantiles, bins)
    factor_quantile.name = 'factor_quantile'

    return factor_quantile.dropna()


def compute_forward_returns(prices, periods=(1, 5, 10), filter_zscore=None):
    """
    Finds the N period forward returns (as percent change) for each asset
    provided.

    Parameters
    ----------
    prices : pd.DataFrame
        Pricing data to use in forward price calculation.
        Assets as columns, dates as index. Pricing data must
        span the factor analysis time period plus an additional buffer window
        that is greater than the maximum number of expected periods
        in the forward returns calculations.
    periods : sequence[int]
        periods to compute forward returns on.
    filter_zscore : int or float
        Sets forward returns greater than X standard deviations
        from the the mean to nan.
        Caution: this outlier filtering incorporates lookahead bias.

    Returns
    -------
    forward_returns : pd.DataFrame - MultiIndex
        Forward returns in indexed by date and asset.
        Separate column for each forward return window.
    """

    forward_returns = pd.DataFrame(index=pd.MultiIndex.from_product(
        [prices.index, prices.columns], names=['date', 'asset']))

    for period in periods:
        delta = prices.pct_change(period).shift(-period)

        if filter_zscore is not None:
            mask = abs(delta - delta.mean()) > (filter_zscore * delta.std())
            delta[mask] = np.nan

        forward_returns[period] = delta.stack()

    forward_returns.index = forward_returns.index.rename(['date', 'asset'])

    return forward_returns


def demean_forward_returns(factor_data, grouper=None):
    """
    Convert forward returns to returns relative to mean
    period wise all-universe or group returns.
    group-wise normalization incorporates the assumption of a
    group neutral portfolio constraint and thus allows allows the
    factor to be evaluated across groups.

    For example, if AAPL 5 period return is 0.1% and mean 5 period
    return for the Technology stocks in our universe was 0.5% in the
    same period, the group adjusted 5 period return for AAPL in this
    period is -0.4%.

    Parameters
    ----------
    factor_data : pd.DataFrame - MultiIndex
        Forward returns in indexed by date and asset.
        Separate column for each forward return window.
    grouper : list
        If True, demean according to group.

    Returns
    -------
    adjusted_forward_returns : pd.DataFrame - MultiIndex
        DataFrame of the same format as the input, but with each
        security's returns normalized by group.
    """

    factor_data = factor_data.copy()

    if not grouper:
        grouper = factor_data.index.get_level_values('date')

    cols = get_forward_returns_columns(factor_data.columns)
    factor_data[cols] = factor_data.groupby(grouper)[cols] \
        .transform(lambda x: x - x.mean())

    return factor_data


def print_table(table, name=None, fmt=None):
    """
    Pretty print a pandas DataFrame.

    Uses HTML output if running inside Jupyter Notebook, otherwise
    formatted text output.

    Parameters
    ----------
    table : pd.Series or pd.DataFrame
        Table to pretty-print.
    name : str, optional
        Table name to display in upper left corner.
    fmt : str, optional
        Formatter to use for displaying table elements.
        E.g. '{0:.2f}%' for displaying 100 as '100.00%'.
        Restores original setting after displaying.
    """
    if isinstance(table, pd.Series):
        table = pd.DataFrame(table)

    if isinstance(table, pd.DataFrame):
        table.columns.name = name

    prev_option = pd.get_option('display.float_format')
    if fmt is not None:
        pd.set_option('display.float_format', lambda x: fmt.format(x))

    display(table)

    if fmt is not None:
        pd.set_option('display.float_format', prev_option)


def get_clean_factor_and_forward_returns(factor,
                                         prices,
                                         groupby=None,
                                         by_group=False,
                                         quantiles=5,
                                         bins=None,
                                         periods=(1, 5, 10),
                                         filter_zscore=20,
                                         groupby_labels=None):
    """
    Formats the factor data, pricing data, and group mappings
    into a DataFrame that contains aligned MultiIndex
    indices of date and asset.

    Parameters
    ----------
    factor : pd.Series - MultiIndex
        A MultiIndex Series indexed by date (level 0) and asset (level 1),
        containing the values for a single alpha factor.
        ::
            -----------------------------------
                date    |    asset   |
            -----------------------------------
                        |   AAPL     |   0.5
                        -----------------------
                        |   BA       |  -1.1
                        -----------------------
            2014-01-01  |   CMG      |   1.7
                        -----------------------
                        |   DAL      |  -0.1
                        -----------------------
                        |   LULU     |   2.7
                        -----------------------

    prices : pd.DataFrame
        A wide form Pandas DataFrame indexed by date with assets
        in the columns. It is important to pass the
        correct pricing data in depending on what time of period your
        signal was generated so to avoid lookahead bias, or
        delayed calculations. Pricing data must span the factor
        analysis time period plus an additional buffer window
        that is greater than the maximum number of expected periods
        in the forward returns calculations.
        'Prices' must contain at least an entry for each date/asset
        combination in 'factor'. This entry must be the asset price
        at the time the asset factor value is computed and it will be
        considered the buy price for that asset at that date.
        'Prices' must also contain entries for dates following each
        date/asset combination in 'factor', as many more dates as the
        maximum value in 'periods'. The asset price after 'period'
        dates will be considered the sell price for that asset when
        computing 'period' forward returns.
        ::
            ----------------------------------------------------
                        | AAPL |  BA  |  CMG  |  DAL  |  LULU  |
            ----------------------------------------------------
               Date     |      |      |       |       |        |
            ----------------------------------------------------
            2014-01-01  |605.12| 24.58|  11.72| 54.43 |  37.14 |
            ----------------------------------------------------
            2014-01-02  |604.35| 22.23|  12.21| 52.78 |  33.63 |
            ----------------------------------------------------
            2014-01-03  |607.94| 21.68|  14.36| 53.94 |  29.37 |
            ----------------------------------------------------

    groupby : pd.Series - MultiIndex or dict
        Either A MultiIndex Series indexed by date and asset,
        containing the period wise group codes for each asset, or
        a dict of asset to group mappings. If a dict is passed,
        it is assumed that group mappings are unchanged for the
        entire time period of the passed factor data.
    by_group : bool
        If True, compute statistics separately for each group.
    quantiles : int or sequence[float]
        Number of equal-sized quantile buckets to use in factor bucketing.
        Alternately sequence of quantiles, allowing non-equal-sized buckets
        e.g. [0, .10, .5, .90, 1.] or [.05, .5, .95]
        Only one of 'quantiles' or 'bins' can be not-None
    bins : int or sequence[float]
        Number of equal-width (valuewise) bins to use in factor bucketing.
        Alternately sequence of bin edges allowing for non-uniform bin width
        e.g. [-4, -2, -0.5, 0, 10]
        Chooses the buckets to be evenly spaced according to the values
        themselves. Useful when the factor contains discrete values.
        Only one of 'quantiles' or 'bins' can be not-None
    periods : sequence[int]
        periods to compute forward returns on.
    filter_zscore : int or float
        Sets forward returns greater than X standard deviations
        from the the mean to nan.
        Caution: this outlier filtering incorporates lookahead bias.
    groupby_labels : dict
        A dictionary keyed by group code with values corresponding
        to the display name for each group.

    Returns
    -------
    merged_data : pd.DataFrame - MultiIndex
        A MultiIndex Series indexed by date (level 0) and asset (level 1),
        containing the values for a single alpha factor, forward returns for
        each period, the factor quantile/bin that factor value belongs to, and
        (optionally) the group the asset belongs to.
        ::
           -------------------------------------------------------------------
                      |       |  1  |  5  |  10  |factor|group|factor_quantile
           -------------------------------------------------------------------
               date   | asset |     |     |      |      |     |
           -------------------------------------------------------------------
                      | AAPL  | 0.09|-0.01|-0.079|  0.5 |  G1 |      3
                      --------------------------------------------------------
                      | BA    | 0.02| 0.06| 0.020| -1.1 |  G2 |      5
                      --------------------------------------------------------
           2014-01-01 | CMG   | 0.03| 0.09| 0.036|  1.7 |  G2 |      1
                      --------------------------------------------------------
                      | DAL   |-0.02|-0.06|-0.029| -0.1 |  G3 |      5
                      --------------------------------------------------------
                      | LULU  |-0.03| 0.05|-0.009|  2.7 |  G1 |      2
                      --------------------------------------------------------
    """

    if factor.index.levels[0].tz != prices.index.tz:
        raise NonMatchingTimezoneError("The timezone of 'factor' is not the "
                                       "same as the timezone of 'prices'. See "
                                       "the pandas methods tz_localize and "
                                       "tz_convert.")

    merged_data = compute_forward_returns(prices, periods, filter_zscore)

    factor = factor.copy()
    factor.index = factor.index.rename(['date', 'asset'])
    merged_data['factor'] = factor

    if groupby is not None:
        if isinstance(groupby, dict):
            diff = set(factor.index.get_level_values(
                'asset')) - set(groupby.keys())
            if len(diff) > 0:
                raise KeyError(
                    "Assets {} not in group mapping".format(
                        list(diff)))

            ss = pd.Series(groupby)
            groupby = pd.Series(index=factor.index,
                                data=ss[factor.index.get_level_values(
                                    'asset')].values)

        if groupby_labels is not None:
            diff = set(groupby.values) - set(groupby_labels.keys())
            if len(diff) > 0:
                raise KeyError(
                    "groups {} not in passed group names".format(
                        list(diff)))

            sn = pd.Series(groupby_labels)
            groupby = pd.Series(index=factor.index,
                                data=sn[groupby.values].values)

        merged_data['group'] = groupby.astype('category')

    merged_data = merged_data.dropna()

    merged_data['factor_quantile'] = quantize_factor(merged_data,
                                                     quantiles,
                                                     bins,
                                                     by_group)

    merged_data = merged_data.dropna()

    return merged_data


def common_start_returns(factor,
                         prices,
                         before,
                         after,
                         cumulative=False,
                         mean_by_date=False,
                         demean=None):
    """
    A date and equity pair is extracted from each index row in the factor
    dataframe and for each of these pairs a return series is built starting
    from 'before' the date and ending 'after' the date specified in the pair.
    All those returns series are then aligned to a common index (-before to
    after) and returned as a single DataFrame

    Parameters
    ----------
    factor : pd.DataFrame
        DataFrame with at least date and equity as index, the columns are
        irrelevant
    prices : pd.DataFrame
        A wide form Pandas DataFrame indexed by date with assets
        in the columns. Pricing data should span the factor
        analysis time period plus/minus an additional buffer window
        corresponding to after/before period parameters.
    before:
        How many returns to load before factor date
    after:
        How many returns to load after factor date
    cumulative: bool, optional
        Return cumulative returns
    mean_by_date: bool, optional
        If True, compute mean returns for each date and return that
        instead of a return series for each asset
    demean: pd.DataFrame, optional
        DataFrame with at least date and equity as index, the columns are
        irrelevant. For each date a list of equities is extracted from 'demean'
        index and used as universe to compute demeaned mean returns (long short
        portfolio)

    Returns
    -------
    aligned_returns : pd.DataFrame
        Dataframe containing returns series for each factor aligned to the same
        index: -before to after
    """

    if cumulative:
        returns = prices
    else:
        returns = prices.pct_change(axis=0)

    all_returns = []

    for timestamp, df in factor.groupby(level='date'):

        equities = df.index.get_level_values('asset')

        try:
            day_zero_index = returns.index.get_loc(timestamp)
        except KeyError:
            continue

        starting_index = max(day_zero_index - before, 0)
        ending_index = min(day_zero_index + after + 1,
                           len(returns.index))

        equities_slice = set(equities)
        if demean is not None:
            demean_equities = demean.loc[timestamp] \
                .index.get_level_values('asset')
            equities_slice |= set(demean_equities)

        series = returns.loc[returns.index[starting_index:ending_index],
                             equities_slice]
        series.index = range(starting_index - day_zero_index,
                             ending_index - day_zero_index)

        if cumulative:
            series = (series / series.loc[0, :]) - 1

        if demean is not None:
            mean = series.loc[:, demean_equities].mean(axis=1)
            series = series.loc[:, equities]
            series = series.sub(mean, axis=0)

        if mean_by_date:
            series = series.mean(axis=1)

        all_returns.append(series)

    return pd.concat(all_returns, axis=1)


def cumulative_returns(returns, period):
    """
    Builds cumulative returns from N-periods returns.

    When 'period' N is greater than 1 the cumulative returns plot is computed
    building and averaging the cumulative returns of N interleaved portfolios
    (started at subsequent periods 1,2,3,...,N) each one rebalancing every N
    periods.

    Parameters
    ----------
    returns: pd.Series
        pd.Series containing N-periods returns
    period: integer
        Period for which the returns are computed
    Returns
    -------
    pd.Series
        Cumulative returns series
    """

    returns = returns.fillna(0)

    if period == 1:
        return returns.add(1).cumprod()

    # build N portfolios from the single returns Series

    def split_portfolio(ret, period): return pd.DataFrame(np.diag(ret))

    sub_portfolios = returns.groupby(np.arange(len(returns.index)) // period,
                                     axis=0).apply(split_portfolio, period)
    sub_portfolios.index = returns.index

    # compute 1 period returns so that we can average the N portfolios

    def rate_of_returns(ret, period): return (
        (np.nansum(ret) + 1)**(1. / period)) - 1

    sub_portfolios = pd.rolling_apply(sub_portfolios,
                                      period,
                                      rate_of_returns,
                                      min_periods=1,
                                      args=(period,))

    sub_portfolios = sub_portfolios.add(1).cumprod()

    return sub_portfolios.mean(axis=1)


def rate_of_return(period_ret):
    """
    1-period Growth Rate: the average rate of 1-period returns
    """
    return period_ret.add(1).pow(1. / period_ret.name).sub(1)


def std_conversion(period_std):
    """
    1-period standard deviation (or standard error) approximation

    Parameters
    ----------
    period_std: pd.DataFrame
        DataFrame containing standard deviation or standard error values
        with column headings representing the return period.

    Returns
    -------
    pd.DataFrame
        DataFrame in same format as input but with one-period
        standard deviation/error values.
    """
    period_len = period_std.name
    return period_std / np.sqrt(period_len)


def get_forward_returns_columns(columns):
    return columns[columns.astype('str').str.isdigit()]
