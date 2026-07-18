import os
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
import uvicorn

from data_collector import DataCollector
from ml_model import PricePredictor
from models import ForecastResponse, HourlyPrice

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
ADDON_CONFIG = CONFIG_DIR / "elpris_ai" / "settings.json"

class AppState:
    def __init__(self):
        self.collector = DataCollector()
        self.predictor = PricePredictor()
        self.last_update = None
        self.last_train = None
        self.forecast_data = None
        self.settings = self._load_settings()

    def _load_settings(self) -> dict:
        if ADDON_CONFIG.exists():
            with open(ADDON_CONFIG, "r") as f:
                return json.load(f)
        return {
            "region": "DK1",
            "vat_percent": 25,
            "fixed_cost_kwh": 0.1293,
            "update_interval_minutes": 60,
            "forecast_days": 7,
            "model_retrain_days": 7
        }

app_state = AppState()

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Elpris AI service")
    await update_data()
    yield
    logger.info("Shutting down Elpris AI service")

app = FastAPI(
    title="Elpris AI",
    description="AI-powered electricity price calculator for Denmark",
    version="1.0.0",
    lifespan=lifespan
)

async def update_data():
    try:
        logger.info("Fetching data from all sources...")
        data = await app_state.collector.collect_all(
            region=app_state.settings["region"],
            forecast_days=app_state.settings["forecast_days"]
        )

        should_retrain = (
            app_state.last_train is None or
            datetime.now() - app_state.last_train > timedelta(days=app_state.settings["model_retrain_days"])
        )

        if should_retrain:
            logger.info("Retraining ML model...")
            await app_state.predictor.train(data)
            app_state.last_train = datetime.now()

        logger.info("Generating forecast...")
        app_state.forecast_data = await app_state.predictor.predict(
            data,
            days=app_state.settings["forecast_days"],
            vat_percent=app_state.settings["vat_percent"],
            fixed_cost_kwh=app_state.settings["fixed_cost_kwh"]
        )
        app_state.last_update = datetime.now()
        logger.info(f"Forecast updated: {len(app_state.forecast_data)} hours")

    except Exception as e:
        logger.error(f"Error updating data: {e}")
        raise

@app.get("/")
async def root():
    return {"message": "Elpris AI Service", "status": "running"}

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "last_update": app_state.last_update.isoformat() if app_state.last_update else None,
        "last_train": app_state.last_train.isoformat() if app_state.last_train else None,
        "forecast_hours": len(app_state.forecast_data) if app_state.forecast_data else 0
    }

@app.get("/forecast", response_model=ForecastResponse)
async def get_forecast():
    if not app_state.forecast_data:
        raise HTTPException(status_code=503, detail="Forecast not available yet")

    return ForecastResponse(
        region=app_state.settings["region"],
        generated_at=datetime.now().isoformat(),
        forecast=app_state.forecast_data
    )

@app.get("/forecast/today")
async def get_today_forecast():
    if not app_state.forecast_data:
        raise HTTPException(status_code=503, detail="Forecast not available yet")

    today = datetime.now().date().isoformat()
    today_prices = [p for p in app_state.forecast_data if p["timestamp"].startswith(today)]

    return {
        "date": today,
        "prices": today_prices,
        "cheapest_hour": min(today_prices, key=lambda x: x["price_with_cost"]) if today_prices else None,
        "most_expensive_hour": max(today_prices, key=lambda x: x["price_with_cost"]) if today_prices else None,
        "average_price": sum(p["price_with_cost"] for p in today_prices) / len(today_prices) if today_prices else None
    }

@app.get("/forecast/tomorrow")
async def get_tomorrow_forecast():
    if not app_state.forecast_data:
        raise HTTPException(status_code=503, detail="Forecast not available yet")

    tomorrow = (datetime.now() + timedelta(days=1)).date().isoformat()
    tomorrow_prices = [p for p in app_state.forecast_data if p["timestamp"].startswith(tomorrow)]

    return {
        "date": tomorrow,
        "prices": tomorrow_prices,
        "cheapest_hour": min(tomorrow_prices, key=lambda x: x["price_with_cost"]) if tomorrow_prices else None,
        "most_expensive_hour": max(tomorrow_prices, key=lambda x: x["price_with_cost"]) if tomorrow_prices else None,
        "average_price": sum(p["price_with_cost"] for p in tomorrow_prices) / len(tomorrow_prices) if tomorrow_prices else None
    }

@app.get("/forecast/week")
async def get_week_forecast():
    if not app_state.forecast_data:
        raise HTTPException(status_code=503, detail="Forecast not available yet")

    daily_stats = {}
    for price in app_state.forecast_data:
        date = price["timestamp"][:10]
        if date not in daily_stats:
            daily_stats[date] = {
                "date": date,
                "prices": [],
                "min_price": float("inf"),
                "max_price": float("-inf")
            }
        daily_stats[date]["prices"].append(price["price_with_cost"])
        daily_stats[date]["min_price"] = min(daily_stats[date]["min_price"], price["price_with_cost"])
        daily_stats[date]["max_price"] = max(daily_stats[date]["max_price"], price["price_with_cost"])

    for date in daily_stats:
        daily_stats[date]["average_price"] = sum(daily_stats[date]["prices"]) / len(daily_stats[date]["prices"])
        daily_stats[date]["price_count"] = len(daily_stats[date]["prices"])
        del daily_stats[date]["prices"]

    return {
        "region": app_state.settings["region"],
        "generated_at": datetime.now().isoformat(),
        "daily": list(daily_stats.values())
    }

@app.post("/update")
async def trigger_update():
    await update_data()
    return {"message": "Update triggered", "timestamp": datetime.now().isoformat()}

@app.get("/settings")
async def get_settings():
    return app_state.settings

@app.post("/settings")
async def update_settings(settings: dict):
    app_state.settings.update(settings)
    ADDON_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    with open(ADDON_CONFIG, "w") as f:
        json.dump(app_state.settings, f, indent=2)
    await update_data()
    return {"message": "Settings updated", "settings": app_state.settings}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
