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
        tasks = [
            self._fetch_historical_prices(region, days=30),
            self._fetch_weather_forecast(forecast_days),
            self._fetch_co2_emissions(region, days=30),
            self._fetch_production(region, days=30),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        data = {
            "historical_prices": results[0] if not isinstance(results[0], Exception) else [],
            "weather_forecast": results[1] if not isinstance(results[1], Exception) else [],
            "weather_history": [],
            "commodity_prices": [],
        }

        co2 = results[2] if not isinstance(results[2], Exception) else []
        production = results[3] if not isinstance(results[3], Exception) else []

        data["commodity_prices"] = self._merge_commodity(co2, production)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Error in task {i}: {result}")

        logger.info(
            f"Data collected: {len(data['historical_prices'])} prices, "
            f"{len(data['weather_forecast'])} weather forecasts, "
            f"{len(data['commodity_prices'])} commodity records"
        )
        return data

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

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.eds_url}/Elspotprices",
                    params=params,
                    timeout=30.0,
                )
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

            except Exception as e:
                logger.error(f"Error fetching historical prices: {e}")
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
                timeseries = (
                    data.get("properties", {}).get("timeseries", [])
                )

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
                    if co2 is None:
                        co2 = record.get("PriceArea") and record.get("CO2PerKWh")
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

        dataset = "ProductionPrognosisDK1" if region == "DK1" else "ProductionPrognosisDK2"

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.eds_url}/{dataset}",
                    params=params,
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()

                records = []
                for record in data.get("records", []):
                    records.append({
                        "timestamp": record.get("Minutes5UTC"),
                        "production": record.get("ProductionStartIntervalKW", 0) or 0,
                        "consumption": record.get("ConsumptionEndIntervalKW", 0) or 0,
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
