import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

import httpx

logger = logging.getLogger(__name__)


class DataCollector:
    def __init__(self):
        self.elprisenligenu_url = "https://www.elprisenligenu.dk/api/v1/prices"
        self.eds_url = "https://api.energidataservice.dk/dataset"
        self.yr_url = "https://api.met.no/weatherapi/locationforecast/2.0/compact"

    async def collect_all(self, region: str, forecast_days: int) -> Dict[str, Any]:
        prices = await self._fetch_prices_elprisenligenu(region, days=14)
        await asyncio.sleep(1)

        weather = await self._fetch_weather_forecast(forecast_days)
        await asyncio.sleep(1)

        co2 = await self._fetch_co2_emissions(region, days=7)

        commodity = co2

        logger.info(
            f"Data collected: {len(prices)} prices, "
            f"{len(weather)} weather forecasts, "
            f"{len(commodity)} CO2 records"
        )

        return {
            "historical_prices": prices,
            "weather_forecast": weather,
            "weather_history": [],
            "commodity_prices": commodity,
        }

    async def _fetch_prices_elprisenligenu(self, region: str, days: int) -> List[Dict]:
        all_prices = []
        now = datetime.now(timezone.utc)

        for day_offset in range(-days + 1, 2):
            date = now + timedelta(days=day_offset)
            date_str = date.strftime("%Y-%m-%d")
            url = f"{self.elprisenligenu_url}/{date.year}/{date.month:02d}-{date.day:02d}_{region}.json"

            async with httpx.AsyncClient() as client:
                try:
                    response = await client.get(url, timeout=15.0)
                    if response.status_code == 404:
                        continue
                    response.raise_for_status()
                    data = response.json()

                    for entry in data:
                        all_prices.append({
                            "timestamp": entry.get("time_start"),
                            "price": entry.get("DKK_per_kWh", 0),
                            "area": region,
                        })

                except Exception as e:
                    logger.warning(f"Error fetching prices for {date_str}: {e}")
                    continue

            await asyncio.sleep(0.3)

        logger.info(f"Fetched {len(all_prices)} price records from elprisenligenu.dk")
        return all_prices

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

        for attempt in range(3):
            async with httpx.AsyncClient() as client:
                try:
                    response = await client.get(
                        f"{self.eds_url}/CO2Emis",
                        params=params,
                        timeout=30.0,
                    )
                    if response.status_code == 429:
                        wait = (attempt + 1) * 5
                        logger.warning(f"CO2 API rate limited, waiting {wait}s...")
                        await asyncio.sleep(wait)
                        continue

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

                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429:
                        wait = (attempt + 1) * 5
                        logger.warning(f"CO2 API rate limited, waiting {wait}s...")
                        await asyncio.sleep(wait)
                    else:
                        logger.error(f"Error fetching CO2 emissions: {e}")
                        return []
                except Exception as e:
                    logger.error(f"Error fetching CO2 emissions: {e}")
                    return []

        return []
