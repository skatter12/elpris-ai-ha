from pydantic import BaseModel
from typing import List, Optional

class HourlyPrice(BaseModel):
    timestamp: str
    price: float
    price_with_cost: float
    vat: float
    fixed_cost: float
    confidence: float
    factors: dict

class ForecastResponse(BaseModel):
    region: str
    generated_at: str
    forecast: List[HourlyPrice]

class WeatherData(BaseModel):
    timestamp: str
    temperature: float
    wind_speed: float
    wind_direction: float
    cloud_cover: float
    precipitation: float
    humidity: float

class CommodityPrices(BaseModel):
    timestamp: str
    oil_price: float
    gas_price: float
    co2_price: float

class HistoricalData(BaseModel):
    prices: List[HourlyPrice]
    weather: List[WeatherData]
    commodities: List[CommodityPrices]
