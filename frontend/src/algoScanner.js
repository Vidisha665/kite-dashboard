export const ALGO_PARAMETERS = Object.freeze({
  volumeLookback: 20,
  volumeMultiplier: 3,
  priceLookback: 20,
  maximumBreakoutAge: 20,
  midpointTolerance: 1,
});

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : Number.NaN;
}

export function normalizeCandles(rawCandles = []) {
  return rawCandles
    .map((candle) => ({
      date: String(candle.date ?? candle.timestamp ?? "").slice(0, 10),
      open: finiteNumber(candle.open),
      high: finiteNumber(candle.high),
      low: finiteNumber(candle.low),
      close: finiteNumber(candle.close),
      volume: finiteNumber(candle.volume),
    }))
    .sort((left, right) => left.date.localeCompare(right.date));
}

function readSnapshotPrice(stock) {
  const price =
    stock.ohlc?.last_price ??
    stock.ohlcSnapshot?.last_price ??
    stock.ohlc_snapshot?.last_price ??
    stock.snapshotLtp ??
    stock.last_price;
  const number = Number(price);
  return Number.isFinite(number) && number > 0 ? number : null;
}

export function scanStocks(stocks = []) {
  let scanned = 0;
  const results = [];

  for (const stock of stocks) {
    scanned += 1;
    const candles = normalizeCandles(stock.candles ?? stock.dailyCandles ?? []);
    if (candles.length < 25) continue;

    const latestIndex = candles.length - 1;
    const firstEligibleIndex = Math.max(
      ALGO_PARAMETERS.priceLookback,
      latestIndex - ALGO_PARAMETERS.maximumBreakoutAge,
    );
    // Scanner setup math uses the latest candle close. LTP snapshots and live
    // websocket ticks are used only for the table's Current Price column.
    const currentPrice = candles[latestIndex].close;

    for (let bIndex = latestIndex - 1; bIndex >= firstEligibleIndex; bIndex -= 1) {
      const breakout = candles[bIndex];
      const previousCandles = candles.slice(
        bIndex - ALGO_PARAMETERS.volumeLookback,
        bIndex,
      );
      if (previousCandles.length !== ALGO_PARAMETERS.volumeLookback) continue;

      const previousVolumes = previousCandles.map((candle) => candle.volume);
      const previousHighs = previousCandles.map((candle) => candle.high);
      if (
        !Number.isFinite(breakout.open) ||
        !Number.isFinite(breakout.high) ||
        !Number.isFinite(breakout.low) ||
        !Number.isFinite(breakout.close) ||
        breakout.close <= 0 ||
        !Number.isFinite(breakout.volume) ||
        !previousVolumes.every(Number.isFinite) ||
        !previousHighs.every(Number.isFinite)
      ) continue;

      const averageVolume =
        previousVolumes.reduce((total, volume) => total + volume, 0) /
        ALGO_PARAMETERS.volumeLookback;
      const prevHigh = Math.max(...previousHighs);
      if (
        !Number.isFinite(averageVolume) ||
        averageVolume <= 0 ||
        breakout.close <= breakout.open ||
        breakout.volume < ALGO_PARAMETERS.volumeMultiplier * averageVolume ||
        breakout.close <= prevHigh
      ) continue;

      const afterCandles = candles.slice(bIndex + 1);
      if (!afterCandles.length || !afterCandles.every((candle) => Number.isFinite(candle.high))) continue;
      const maxPriceAfter = Math.max(...afterCandles.map((candle) => candle.high));
      if (maxPriceAfter <= breakout.high) continue;

      const midpoint = (breakout.high + breakout.low) / 2;
      const daysSince = latestIndex - bIndex;
      if (!Number.isFinite(currentPrice) || midpoint <= 0) continue;
      const distanceFromMidpoint = ((currentPrice - midpoint) / midpoint) * 100;
      if (
        Math.abs(distanceFromMidpoint) > ALGO_PARAMETERS.midpointTolerance ||
        daysSince > ALGO_PARAMETERS.maximumBreakoutAge
      ) continue;

      results.push({
        symbol: stock.symbol ?? stock.ticker ?? "",
        companyName: stock.companyName ?? stock.company_name ?? stock.company ?? "",
        currentPrice,
        breakoutDate: breakout.date,
        bIndex,
        daysSince,
        breakoutLow: breakout.low,
        breakoutHigh: breakout.high,
        midpoint,
        distanceFromMidpoint,
        volMultiple: breakout.volume / averageVolume,
        prevHigh,
        maxPriceAfter,
        returnFromCloseToSubHigh: ((maxPriceAfter - breakout.close) / breakout.close) * 100,
        retracementHigh: ((maxPriceAfter - currentPrice) / maxPriceAfter) * 100,
        candles,
        instrument_token: stock.instrument_token ?? stock.instrumentToken ?? null,
        snapshotLtp: readSnapshotPrice(stock),
        isNifty50: Boolean(stock.isNifty50 ?? stock.nifty50 ?? stock.nifty_50),
        isNifty100: Boolean(stock.isNifty100 ?? stock.nifty100 ?? stock.nifty_100),
      });
      break;
    }
  }

  results.sort(
    (left, right) =>
      left.daysSince - right.daysSince ||
      Math.abs(left.distanceFromMidpoint) - Math.abs(right.distanceFromMidpoint),
  );
  return { results, scanned };
}
