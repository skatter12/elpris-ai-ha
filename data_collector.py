import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

class DataCollector:
    def __init__(self):
        self.energi_data_url = "https://api.energidataservice.dk/dataset"
        self.dmi_url = "https://www.dmi.dk/NinJo2DmiDk/ninjo2dmidk"
        self.nordpool_url = "https://dataportal-api.nordpoolgroup.com/api/DayAheadPrices"

    async def collect_all(self, region: str, forecast_days: int) -> Dict[str, Any]:
        tasks = [
            self._fetch_historical_prices(region, days=30),
            self._fetch_weather_forecast(forecast_days),
            self._fetch_weather_history(days=30),
            self._fetch_commodity_prices(days=30),
            self._fetch_nordpool_prices(region)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        data = {
            "historical_prices": results[0] if not isinstance(results[0], Exception) else [],
            "weather_forecast": results[1] if not isinstance(results[1], Exception) else [],
            "weather_history": results[2] if not isinstance(results[2], Exception) else [],
            "commodity_prices": results[3] if not isinstance(results[3], Exception) else [],
            "current_prices": results[4] if not isinstance(results[4], Exception) else []
        }

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Error fetching data source {i}: {result}")

        return data

    async def _fetch_historical_prices(self, region: str, days: int) -> List[Dict]:
        end = datetime.utcnow()
        start = end - timedelta(days=days)

        params = {
            "start": start.strftime("%Y-%m-%dT%H:%M"),
            "end": end.strftime("%Y-%m-%dT%H:%M"),
            "filter": f'{{"PriceArea":"{region}"}}',
            "sort": "HourUTC DESC",
            "limit": days * 24
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.energi_data_url}/Elspotprices",
                    params=params,
                    timeout=30.0
                )
                response.raise_for_status()
                data = response.json()

                prices = []
                for record in data.get("records", []):
                    prices.append({
                        "timestamp": record.get("HourUTC"),
                        "price": record.get("SpotPriceDKK", 0) / 1000,
                        "area": record.get("PriceArea", region)
                    })

                logger.info(f"Fetched {len(prices)} historical prices")
                return prices

            except Exception as e:
                logger.error(f"Error fetching historical prices: {e}")
                return []

    async def _fetch_weather_forecast(self, days: int) -> List[Dict]:
        params = {
            "cmd": "llj",
            "lat": "56.1682",
            "lon": "10.1695"
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    self.dmi_url,
                    params=params,
                    timeout=30.0
                )
                response.raise_for_status()
                data = response.json()

                forecasts = []
                ts_list = data.get("timeSeries", [])

                for ts in ts_list[:days * 24]:
                    timestamp = ts.get("validTime", "")
                    temp = None
                    wind_speed = None
                    cloud_cover = None
                    precipitation = None

                    for param in ts.get("parameters", []):
                        if param["name"] == "temperature":
                            temp = param["values"][0]
                        elif param["name"] == "windSpeed":
                            wind_speed = param["values"][0]
                        elif param["name"] == "cloudCover":
                            cloud_cover = param["values"][0]
                        elif param["name"] == "precipitation":
                            precipitation = param["values"][0]

                    forecasts.append({
                        "timestamp": timestamp,
                        "temperature": temp,
                        "wind_speed": wind_speed,
                        "cloud_cover": cloud_cover,
                        "precipitation": precipitation
                    })

                logger.info(f"Fetched {len(forecasts)} weather forecasts")
                return forecasts

            except Exception as e:
                logger.error(f"Error fetching weather forecast: {e}")
                return []

    async def _fetch_weather_history(self, days: int) -> List[Dict]:
        end = datetime.utcnow()
        start = end - timedelta(days=days)

        params = {
            "start": start.strftime("%Y-%m-%dT%H:%M"),
            "end": end.strftime("%Y-%m-%dT%H:%M"),
            "filter": '{"StationId":"06180"}',
            "sort": "DateTimeUtc DESC",
            "limit": days * 24
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.energi_data_url}/DataWeather",
                    params=params,
                    timeout=30.0
                )
                response.raise_for_status()
                data = response.json()

                history = []
                for record in data.get("records", []):
                    history.append({
                        "timestamp": record.get("DateTimeUtc"),
                        "temperature": record.get("Temperature"),
                        "wind_speed": record.get("WindSpeed"),
                        "cloud_cover": record.get("CloudCover")
                    })

                logger.info(f"Fetched {len(history)} weather history records")
                return history

            except Exception as e:
                logger.error(f"Error fetching weather history: {e}")
                return []

    async def _fetch_commodity_prices(self, days: int) -> List[Dict]:
        end = datetime.utcnow()
        start = end - timedelta(days=days)

        params = {
            "start": start.strftime("%Y-%m-%dT%H:%M"),
            "end": end.strftime("%Y-%m-%dT%H:%M"),
            "sort": "Minutes5UTC DESC",
            "limit": days * 288
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.energi_data_url}/DataPrognosis",
                    params=params,
                    timeout=30.0
                )
                response.raise_for_status()
                data = response.json()

                prices = []
                for record in data.get("records", []):
                    prices.append({
                        "timestamp": record.get("Minutes5UTC"),
                        "co2_price": record.get("CO2Emis", 0),
                        "production": record.get("TotalProduction", 0),
                        "consumption": record.get("TotalConsumption", 0)
                    })

                logger.info(f"Fetched {len(prices)} commodity data points")
                return prices

            except Exception as e:
                logger.error(f"Error fetching commodity prices: {e}")
                return []

    async def _fetch_nordpool_prices(self, region: str) -> List[Dict]:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    self.nordpool_url,
                    params={"area": region},
                    timeout=30.0
                )
                response.raise_for_status()
                data = response.json()

                prices = []
                for day in data.get("data", []):
                    date = day.get("date")
                    for hour_data in day.get("prices", []):
                        prices.append({
                            "timestamp": f"{date}T{hour_data['hour']:02d}:00:00",
                            "price": hour_data["price"] / 1000
                        })

                logger.info(f"Fetched {len(prices)} Nordpool prices")
                return prices

            except Exception as e:
                logger.error(f"Error fetching Nordpool prices: {e}")
                return []
