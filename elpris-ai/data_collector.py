import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any

import httpx

logger = logging.getLogger(__name__)


class DataCollector:
    def __init__(self):
        self.eds_url = "https://api.energidataservice.dk/dataset"
        self.yr_url = "https://api.met.no/weatherapi/locationforecast/2.0/compact"

    async def collect_all(self, region: str, forecast_days: int) -> Dict[str, Any]:
        prices = await self._fetch_historical_prices(region, days=7)
        await asyncio.sleep(2)

        weather = await self._fetch_weather_forecast(forecast_days)
        await asyncio.sleep(1)

        co2 = await self._fetch_co2_emissions(region, days=7)
        await asyncio.sleep(1)

        production = await self._fetch_production(region, days=7)

        commodity = self._merge_commodity(co2, production)

        logger.info(
            f"Data collected: {len(prices)} prices, "
            f"{len(weather)} weather forecasts, "
            f"{len(commodity)} commodity records"
        )

        return {
            "historical_prices": prices,
            "weather_forecast": weather,
            "weather_history": [],
            "commodity_prices": commodity,
        }

    async def _fetch_historical_prices(self, region: str, days: int) -> List[Dict]:
        end = datetime.utcnow()
        start = end - timedelta(days=days)

        params = {
            "start": start.strftime("%Y-%m-%dT%H:%M"),
            "end": end.strftime("%Y-%m-%dT%H:%M"),
            "filter": f'{{"PriceArea":"{region}"}}',
            "sort": "HourUTC DESC",
            "limit": days * 24,
        }

        for attempt in range(3):
            async with httpx.AsyncClient() as client:
                try:
                    response = await client.get(
                        f"{self.eds_url}/Elspotprices",
                        params=params,
                        timeout=30.0,
                    )
                    if response.status_code == 429:
                        wait = (attempt + 1) * 5
                        logger.warning(f"Rate limited, waiting {wait}s...")
                        await asyncio.sleep(wait)
                        continue

                    response.raise_for_status()
                    data = response.json()

                    prices = []
                    for record in data.get("records", []):
                        spot = record.get("SpotPriceDKK")
                        if spot is None:
                            continue
                        prices.append({
                            "timestamp": record.get("HourUTC"),
                            "price": float(spot) / 1000.0,
                            "area": record.get("PriceArea", region),
                        })

                    logger.info(f"Fetched {len(prices)} historical prices")
                    return prices

                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429:
                        wait = (attempt + 1) * 5
                        logger.warning(f"Rate limited, waiting {wait}s...")
                        await asyncio.sleep(wait)
                    else:
                        logger.error(f"Error fetching prices: {e}")
                        return []
                except Exception as e:
                    logger.error(f"Error fetching prices: {e}")
                    return []

        logger.error("Failed to fetch prices after 3 attempts")
        return []

    async def _fetch_weather_forecast(self, days: int) -> List[Dict]:
        lat, lon = "56.162", "10.203"

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    self.yr_url,
                    params={"lat": lat, "lon": lon},
                    headers={"User-Agent": "elpris-ai/1.0 github.com/skatter12/elpris-ai-ha"},
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()

                forecasts = []
                timeseries = data.get("properties", {}).get("timeseries", [])

                for entry in timeseries[: days * 4]:
                    instant = entry.get("data", {}).get("instant", {}).get("details", {})
                    ts = entry.get("time")

                    forecasts.append({
                        "timestamp": ts,
                        "temperature": instant.get("air_temperature"),
                        "wind_speed": instant.get("wind_speed"),
                        "cloud_cover": instant.get("cloud_area_fraction"),
                    })

                logger.info(f"Fetched {len(forecasts)} weather forecasts")
                return forecasts

            except Exception as e:
                logger.error(f"Error fetching weather forecast: {e}")
                return []

    async def _fetch_co2_emissions(self, region: str, days: int) -> List[Dict]:
        end = datetime.utcnow()
        start = end - timedelta(days=days)

        params = {
            "start": start.strftime("%Y-%m-%dT%H:%M"),
            "end": end.strftime("%Y-%m-%dT%H:%M"),
            "sort": "Minutes5UTC DESC",
            "limit": days * 288,
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.eds_url}/CO2Emis",
                    params=params,
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()

                records = []
                for record in data.get("records", []):
                    co2 = record.get("CO2PerKWh")
                    records.append({
                        "timestamp": record.get("Minutes5UTC"),
                        "co2_price": float(co2) if co2 else 0,
                        "area": record.get("PriceArea", ""),
                    })

                logger.info(f"Fetched {len(records)} CO2 emission records")
                return records

            except Exception as e:
                logger.error(f"Error fetching CO2 emissions: {e}")
                return []

    async def _fetch_production(self, region: str, days: int) -> List[Dict]:
        end = datetime.utcnow()
        start = end - timedelta(days=days)

        params = {
            "start": start.strftime("%Y-%m-%dT%H:%M"),
            "end": end.strftime("%Y-%m-%dT%H:%M"),
            "sort": "Minutes5UTC DESC",
            "limit": days * 288,
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.eds_url}/ProductionConsolidatedDK",
                    params=params,
                    timeout=30.0,
                )
                if response.status_code == 404:
                    logger.warning("ProductionConsolidatedDK not available, skipping")
                    return []

                response.raise_for_status()
                data = response.json()

                records = []
                for record in data.get("records", []):
                    ts = record.get("Minutes5UTC")
                    if ts:
                        records.append({
                            "timestamp": ts,
                            "production": record.get("ProductionMW", 0) or 0,
                            "consumption": record.get("ConsumptionMW", 0) or 0,
                        })

                logger.info(f"Fetched {len(records)} production records")
                return records

            except Exception as e:
                logger.error(f"Error fetching production data: {e}")
                return []

    def _merge_commodity(
        self, co2_records: List[Dict], prod_records: List[Dict]
    ) -> List[Dict]:
        merged = {}

        for r in co2_records:
            ts = r.get("timestamp")
            if ts:
                merged.setdefault(ts, {"timestamp": ts, "co2_price": 0, "production": 0, "consumption": 0})
                merged[ts]["co2_price"] = r.get("co2_price", 0)

        for r in prod_records:
            ts = r.get("timestamp")
            if ts:
                merged.setdefault(ts, {"timestamp": ts, "co2_price": 0, "production": 0, "consumption": 0})
                merged[ts]["production"] = r.get("production", 0)
                merged[ts]["consumption"] = r.get("consumption", 0)

        return list(merged.values())
