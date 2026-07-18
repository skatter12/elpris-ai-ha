import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

import httpx

logger = logging.getLogger(__name__)

LATITUDE = "56.162"
LONGITUDE = "10.203"


class DataCollector:
    def __init__(self):
        self.elprisenligenu_url = "https://www.elprisenligenu.dk/api/v1/prices"
        self.eds_url = "https://api.energidataservice.dk/dataset"
        self.yr_url = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
        self.openmeteo_url = "https://archive-api.open-meteo.com/v1/archive"

    async def collect_all(self, region: str, forecast_days: int) -> Dict[str, Any]:
        historical_days = 365

        logger.info(f"Fetching {historical_days} days of historical prices...")
        prices = await self._fetch_prices_historical(region, historical_days)
        await asyncio.sleep(1)

        logger.info("Fetching historical weather from Open-Meteo...")
        weather_history = await self._fetch_weather_historical(historical_days)
        await asyncio.sleep(1)

        logger.info("Fetching weather forecast from YR...")
        weather_forecast = await self._fetch_weather_forecast(forecast_days)
        await asyncio.sleep(1)

        logger.info(f"Fetching {historical_days} days of CO2 emissions...")
        co2 = await self._fetch_co2_emissions(region, historical_days)

        commodity = co2

        logger.info(
            f"Data collected: {len(prices)} prices, "
            f"{len(weather_history)} weather history, "
            f"{len(weather_forecast)} weather forecast, "
            f"{len(commodity)} CO2 records"
        )

        return {
            "historical_prices": prices,
            "weather_forecast": weather_forecast,
            "weather_history": weather_history,
            "commodity_prices": commodity,
        }

    async def refresh_prices(self, region: str, forecast_days: int) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        all_prices = []

        for day_offset in range(0, 3):
            dt = now - timedelta(days=day_offset)
            url = f"{self.elprisenligenu_url}/{dt.year}/{dt.month:02d}-{dt.day:02d}_{region}.json"
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
                    logger.warning(f"Error refreshing prices for {dt.date()}: {e}")
            await asyncio.sleep(0.3)

        for future_offset in [0, -1]:
            dt = now + timedelta(days=future_offset)
            url = f"{self.elprisenligenu_url}/{dt.year}/{dt.month:02d}-{dt.day:02d}_{region}.json"
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
                    logger.warning(f"Error refreshing prices for {dt.date()}: {e}")
            await asyncio.sleep(0.3)

        weather_forecast = await self._fetch_weather_forecast(forecast_days)

        logger.info(f"Refreshed: {len(all_prices)} prices, {len(weather_forecast)} weather forecast")
        return {
            "prices": all_prices,
            "weather_forecast": weather_forecast,
        }

    async def _fetch_prices_historical(self, region: str, days: int) -> List[Dict]:
        all_prices = []
        now = datetime.now(timezone.utc)
        batch_size = 30

        for batch_start in range(0, days, batch_size):
            batch_end = min(batch_start + batch_size, days)
            batch_prices = []

            for day_offset in range(batch_start, batch_end):
                dt = now - timedelta(days=day_offset)
                url = f"{self.elprisenligenu_url}/{dt.year}/{dt.month:02d}-{dt.day:02d}_{region}.json"

                async with httpx.AsyncClient() as client:
                    try:
                        response = await client.get(url, timeout=15.0)
                        if response.status_code == 404:
                            continue
                        response.raise_for_status()
                        data = response.json()

                        for entry in data:
                            batch_prices.append({
                                "timestamp": entry.get("time_start"),
                                "price": entry.get("DKK_per_kWh", 0),
                                "area": region,
                            })
                    except Exception as e:
                        logger.warning(f"Error fetching prices for {dt.date()}: {e}")
                        continue

                await asyncio.sleep(0.3)

            all_prices.extend(batch_prices)
            logger.info(f"Prices batch {batch_start}-{batch_end}: {len(batch_prices)} records (total: {len(all_prices)})")

            if batch_end < days:
                await asyncio.sleep(1)

        for future_offset in [0, -1]:
            dt = now + timedelta(days=future_offset)
            url = f"{self.elprisenligenu_url}/{dt.year}/{dt.month:02d}-{dt.day:02d}_{region}.json"
            async with httpx.AsyncClient() as client:
                try:
                    response = await client.get(url, timeout=15.0)
                    if response.status_code == 404:
                        logger.info(f"Prices for {dt.date()} not yet available")
                        continue
                    response.raise_for_status()
                    data = response.json()
                    for entry in data:
                        all_prices.append({
                            "timestamp": entry.get("time_start"),
                            "price": entry.get("DKK_per_kWh", 0),
                            "area": region,
                        })
                    logger.info(f"Fetched prices for {dt.date()} (today/tomorrow)")
                except Exception as e:
                    logger.warning(f"Error fetching future prices for {dt.date()}: {e}")
            await asyncio.sleep(0.3)

        logger.info(f"Fetched {len(all_prices)} price records from elprisenligenu.dk")
        return all_prices

    async def _fetch_weather_historical(self, days: int) -> List[Dict]:
        end_date = datetime.utcnow().date()
        start_date = end_date - timedelta(days=days)
        batch_days = 90
        all_forecasts = []

        current_start = start_date
        while current_start < end_date:
            current_end = min(current_start + timedelta(days=batch_days), end_date)

            async with httpx.AsyncClient() as client:
                try:
                    response = await client.get(
                        self.openmeteo_url,
                        params={
                            "latitude": LATITUDE,
                            "longitude": LONGITUDE,
                            "start_date": current_start.isoformat(),
                            "end_date": current_end.isoformat(),
                            "hourly": "temperature_2m,wind_speed_10m,cloud_cover",
                            "timezone": "Europe/Copenhagen",
                        },
                        timeout=30.0,
                    )
                    response.raise_for_status()
                    data = response.json()

                    hourly = data.get("hourly", {})
                    timestamps = hourly.get("time", [])
                    temps = hourly.get("temperature_2m", [])
                    winds = hourly.get("wind_speed_10m", [])
                    clouds = hourly.get("cloud_cover", [])

                    for i, ts in enumerate(timestamps):
                        all_forecasts.append({
                            "timestamp": ts,
                            "temperature": temps[i] if i < len(temps) else 0,
                            "wind_speed": winds[i] if i < len(winds) else 0,
                            "cloud_cover": clouds[i] if i < len(clouds) else 0,
                        })

                    logger.info(f"Weather batch {current_start} to {current_end}: {len(timestamps)} records")

                except Exception as e:
                    logger.error(f"Error fetching weather history: {e}")

            current_start = current_end + timedelta(days=1)
            await asyncio.sleep(0.5)

        logger.info(f"Fetched {len(all_forecasts)} historical weather records")
        return all_forecasts

    async def _fetch_weather_forecast(self, days: int) -> List[Dict]:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    self.yr_url,
                    params={"lat": LATITUDE, "lon": LONGITUDE},
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
        all_records = []
        batch_days = 30
        now = datetime.utcnow()

        for batch_start in range(0, days, batch_days):
            batch_end = min(batch_start + batch_days, days)
            end = now - timedelta(days=batch_start)
            start = now - timedelta(days=batch_end)

            params = {
                "start": start.strftime("%Y-%m-%dT%H:%M"),
                "end": end.strftime("%Y-%m-%dT%H:%M"),
                "sort": "Minutes5UTC DESC",
                "limit": batch_days * 288,
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
                            wait = (attempt + 1) * 10
                            logger.warning(f"CO2 API rate limited, waiting {wait}s...")
                            await asyncio.sleep(wait)
                            continue

                        response.raise_for_status()
                        data = response.json()

                        batch_records = []
                        for record in data.get("records", []):
                            co2 = record.get("CO2PerKWh")
                            batch_records.append({
                                "timestamp": record.get("Minutes5UTC"),
                                "co2_price": float(co2) if co2 else 0,
                                "area": record.get("PriceArea", ""),
                            })

                        all_records.extend(batch_records)
                        logger.info(f"CO2 batch {batch_start}-{batch_end}: {len(batch_records)} records (total: {len(all_records)})")
                        break

                    except httpx.HTTPStatusError as e:
                        if e.response.status_code == 429:
                            wait = (attempt + 1) * 10
                            logger.warning(f"CO2 API rate limited, waiting {wait}s...")
                            await asyncio.sleep(wait)
                        else:
                            logger.error(f"Error fetching CO2 emissions: {e}")
                            break
                    except Exception as e:
                        logger.error(f"Error fetching CO2 emissions: {e}")
                        break

            await asyncio.sleep(3)

        logger.info(f"Fetched {len(all_records)} CO2 emission records")
        return all_records
