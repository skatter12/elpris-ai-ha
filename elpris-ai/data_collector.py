import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

LATITUDE = "56.162"
LONGITUDE = "10.203"

CACHE_DIR = Path(os.environ.get("CONFIG_DIR", "/config")) / "elpris_ai"
CACHE_FILE = CACHE_DIR / "data_cache.json"


def _should_fetch_tomorrow() -> bool:
    now_cet = datetime.now(timezone(timedelta(hours=1)))
    if now_cet.hour > 13 or (now_cet.hour == 13 and now_cet.minute >= 30):
        return True
    return False


class DataCollector:
    def __init__(self):
        self.elprisenligenu_url = "https://www.elprisenligenu.dk/api/v1/prices"
        self.eds_url = "https://api.energidataservice.dk/dataset"
        self.yr_url = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
        self.openmeteo_url = "https://archive-api.open-meteo.com/v1/archive"

    def _load_cache(self) -> Optional[Dict[str, Any]]:
        """Load cached data from disk."""
        try:
            if CACHE_FILE.exists():
                with open(CACHE_FILE, "r") as f:
                    data = json.load(f)
                logger.info(
                    f"Loaded cache: {len(data.get('historical_prices', []))} prices, "
                    f"{len(data.get('weather_history', []))} weather history, "
                    f"{len(data.get('commodity_prices', []))} CO2 records"
                )
                return data
        except Exception as e:
            logger.warning(f"Could not load cache: {e}")
        return None

    def _save_cache(self, data: Dict[str, Any]) -> None:
        """Save data to disk cache."""
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_data = {
                "historical_prices": data.get("historical_prices", []),
                "weather_history": data.get("weather_history", []),
                "weather_forecast": data.get("weather_forecast", []),
                "commodity_prices": data.get("commodity_prices", []),
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }
            with open(CACHE_FILE, "w") as f:
                json.dump(cache_data, f)
            logger.info(
                f"Saved cache: {len(cache_data['historical_prices'])} prices, "
                f"{len(cache_data['weather_history'])} weather history, "
                f"{len(cache_data['commodity_prices'])} CO2 records"
            )
        except Exception as e:
            logger.error(f"Error saving cache: {e}")

    def _find_missing_dates(
        self, existing_timestamps: List[str], days_back: int
    ) -> List[datetime]:
        """Find dates that are missing from the existing data."""
        now = datetime.now(timezone.utc)
        existing_dates = set()

        for ts in existing_timestamps:
            try:
                if isinstance(ts, str):
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    existing_dates.add(dt.date())
            except Exception:
                continue

        all_dates = set()
        for i in range(days_back):
            dt = now - timedelta(days=i)
            all_dates.add(dt.date())

        missing = sorted(all_dates - existing_dates)
        return [datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc) for d in missing]

    def _find_missing_weather_dates(
        self, existing_timestamps: List[str], days_back: int
    ) -> List[datetime]:
        """Find dates missing from weather data."""
        now = datetime.now(timezone.utc)
        existing_dates = set()

        for ts in existing_timestamps:
            try:
                if isinstance(ts, str):
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    existing_dates.add(dt.date())
            except Exception:
                continue

        all_dates = set()
        for i in range(days_back):
            dt = now - timedelta(days=i)
            all_dates.add(dt.date())

        missing = sorted(all_dates - existing_dates)
        return [datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc) for d in missing]

    async def collect_all(self, region: str, forecast_days: int) -> Dict[str, Any]:
        historical_days = 365

        cache = self._load_cache()

        if cache and cache.get("historical_prices"):
            logger.info("Cache found, filling gaps instead of full fetch...")
            data = await self._fill_gaps(cache, region, historical_days, forecast_days)
        else:
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

            data = {
                "historical_prices": prices,
                "weather_forecast": weather_forecast,
                "weather_history": weather_history,
                "commodity_prices": co2,
            }

        self._save_cache(data)

        logger.info(
            f"Data collected: {len(data['historical_prices'])} prices, "
            f"{len(data['weather_history'])} weather history, "
            f"{len(data['weather_forecast'])} weather forecast, "
            f"{len(data['commodity_prices'])} CO2 records"
        )

        return data

    async def _fill_gaps(
        self,
        cache: Dict[str, Any],
        region: str,
        historical_days: int,
        forecast_days: int,
    ) -> Dict[str, Any]:
        """Only fetch data that's missing from the cache."""

        existing_prices = cache.get("historical_prices", [])
        existing_weather = cache.get("weather_history", [])
        existing_co2 = cache.get("commodity_prices", [])

        price_timestamps = [p.get("timestamp", "") for p in existing_prices]
        weather_timestamps = [w.get("timestamp", "") for w in existing_weather]
        co2_timestamps = [c.get("timestamp", "") for c in existing_co2]

        missing_price_dates = self._find_missing_dates(price_timestamps, historical_days)
        missing_weather_dates = self._find_missing_weather_dates(
            weather_timestamps, historical_days
        )
        missing_co2_dates = self._find_missing_dates(co2_timestamps, historical_days)

        new_prices = []
        if missing_price_dates:
            logger.info(
                f"Fetching {len(missing_price_dates)} missing price dates..."
            )
            for dt in missing_price_dates:
                url = f"{self.elprisenligenu_url}/{dt.year}/{dt.month:02d}-{dt.day:02d}_{region}.json"
                async with httpx.AsyncClient() as client:
                    try:
                        response = await client.get(url, timeout=15.0)
                        if response.status_code == 404:
                            continue
                        response.raise_for_status()
                        data = response.json()
                        for entry in data:
                            new_prices.append(
                                {
                                    "timestamp": entry.get("time_start"),
                                    "price": entry.get("DKK_per_kWh", 0),
                                    "area": region,
                                }
                            )
                        logger.info(f"Fetched prices for {dt.date()}")
                    except Exception as e:
                        logger.warning(f"Error fetching prices for {dt.date()}: {e}")
                await asyncio.sleep(0.3)

        new_weather = []
        if missing_weather_dates:
            logger.info(
                f"Fetching {len(missing_weather_dates)} missing weather dates..."
            )
            batch_size = 90
            i = 0
            while i < len(missing_weather_dates):
                batch_start = missing_weather_dates[i]
                batch_end_dt = min(
                    batch_start + timedelta(days=batch_size - 1),
                    missing_weather_dates[-1],
                    datetime.now(timezone.utc),
                )
                async with httpx.AsyncClient() as client:
                    try:
                        response = await client.get(
                            self.openmeteo_url,
                            params={
                                "latitude": LATITUDE,
                                "longitude": LONGITUDE,
                                "start_date": batch_start.date().isoformat(),
                                "end_date": batch_end_dt.date().isoformat(),
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

                        for j, ts in enumerate(timestamps):
                            new_weather.append(
                                {
                                    "timestamp": ts,
                                    "temperature": temps[j] if j < len(temps) else 0,
                                    "wind_speed": winds[j] if j < len(winds) else 0,
                                    "cloud_cover": clouds[j] if j < len(clouds) else 0,
                                }
                            )
                        logger.info(
                            f"Fetched weather for {batch_start.date()} to {batch_end_dt.date()}: {len(timestamps)} records"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Error fetching weather for {batch_start.date()} to {batch_end_dt.date()}: {e}"
                        )
                i += batch_size
                await asyncio.sleep(0.5)

        new_co2 = []
        if missing_co2_dates:
            logger.info(f"Fetching {len(missing_co2_dates)} missing CO2 dates...")
            batch_size = 30
            i = 0
            while i < len(missing_co2_dates):
                batch_start = missing_co2_dates[i]
                batch_end_dt = min(
                    batch_start + timedelta(days=batch_size - 1),
                    missing_co2_dates[-1],
                    datetime.now(timezone.utc),
                )
                params = {
                    "start": batch_start.strftime("%Y-%m-%dT%H:%M"),
                    "end": batch_end_dt.strftime("%Y-%m-%dT%H:%M"),
                    "sort": "Minutes5UTC DESC",
                    "limit": batch_size * 288,
                }
                async with httpx.AsyncClient() as client:
                    try:
                        response = await client.get(
                            f"{self.eds_url}/CO2Emis",
                            params=params,
                            timeout=30.0,
                        )
                        if response.status_code == 429:
                            logger.warning("CO2 API rate limited, waiting 30s...")
                            await asyncio.sleep(30)
                            continue

                        response.raise_for_status()
                        data = response.json()
                        for record in data.get("records", []):
                            co2 = record.get("CO2PerKWh")
                            new_co2.append(
                                {
                                    "timestamp": record.get("Minutes5UTC"),
                                    "co2_price": float(co2) if co2 else 0,
                                    "area": record.get("PriceArea", ""),
                                }
                            )
                        logger.info(
                            f"Fetched CO2 for {batch_start.date()} to {batch_end_dt.date()}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Error fetching CO2 for {batch_start.date()} to {batch_end_dt.date()}: {e}"
                        )
                i += batch_size
                await asyncio.sleep(3)

        fetch_tomorrow = _should_fetch_tomorrow()
        future_offsets = [0, -1] if fetch_tomorrow else [0]
        new_future_prices = []
        for future_offset in future_offsets:
            dt = datetime.now(timezone.utc) + timedelta(days=future_offset)
            url = f"{self.elprisenligenu_url}/{dt.year}/{dt.month:02d}-{dt.day:02d}_{region}.json"
            async with httpx.AsyncClient() as client:
                try:
                    response = await client.get(url, timeout=15.0)
                    if response.status_code == 404:
                        continue
                    response.raise_for_status()
                    data = response.json()
                    for entry in data:
                        new_future_prices.append(
                            {
                                "timestamp": entry.get("time_start"),
                                "price": entry.get("DKK_per_kWh", 0),
                                "area": region,
                            }
                        )
                    logger.info(f"Fetched future prices for {dt.date()}")
                except Exception as e:
                    logger.warning(
                        f"Error fetching future prices for {dt.date()}: {e}"
                    )
            await asyncio.sleep(0.3)

        weather_forecast = await self._fetch_weather_forecast(forecast_days)

        existing_price_map = {p["timestamp"]: p for p in existing_prices}
        for p in new_prices + new_future_prices:
            existing_price_map[p["timestamp"]] = p
        merged_prices = list(existing_price_map.values())

        existing_weather_map = {w["timestamp"]: w for w in existing_weather}
        for w in new_weather:
            existing_weather_map[w["timestamp"]] = w
        merged_weather = list(existing_weather_map.values())

        existing_co2_map = {c["timestamp"]: c for c in existing_co2}
        for c in new_co2:
            existing_co2_map[c["timestamp"]] = c
        merged_co2 = list(existing_co2_map.values())

        logger.info(
            f"Gap fill complete: {len(new_prices)} new prices, "
            f"{len(new_weather)} new weather, "
            f"{len(new_co2)} new CO2 records"
        )

        return {
            "historical_prices": merged_prices,
            "weather_forecast": weather_forecast,
            "weather_history": merged_weather,
            "commodity_prices": merged_co2,
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

        fetch_tomorrow = _should_fetch_tomorrow()
        future_offsets = [0, -1] if fetch_tomorrow else [0]
        for future_offset in future_offsets:
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

        fetch_tomorrow = _should_fetch_tomorrow()
        future_offsets = [0, -1] if fetch_tomorrow else [0]
        for future_offset in future_offsets:
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
