import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from prophet import Prophet
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)

MODEL_DIR = Path("/config/elpris_ai/models")
MODEL_DIR.mkdir(parents=True, exist_ok=True)

class PricePredictor:
    def __init__(self):
        self.model: Optional[Prophet] = None
        self.scaler = StandardScaler()
        self.model_path = MODEL_DIR / "price_model.pkl"
        self._load_model()

    def _load_model(self):
        if self.model_path.exists():
            try:
                with open(self.model_path, "rb") as f:
                    self.model = pickle.load(f)
                logger.info("Loaded existing model")
            except Exception as e:
                logger.warning(f"Could not load model: {e}")
                self.model = None

    def _save_model(self):
        try:
            with open(self.model_path, "wb") as f:
                pickle.dump(self.model, f)
            logger.info("Model saved")
        except Exception as e:
            logger.error(f"Error saving model: {e}")

    def _prepare_features(self, data: Dict[str, Any]) -> pd.DataFrame:
        records = []

        historical_prices = data.get("historical_prices", [])
        weather_history = data.get("weather_history", [])
        commodity_prices = data.get("commodity_prices", [])

        price_df = pd.DataFrame(historical_prices) if historical_prices else pd.DataFrame()
        weather_df = pd.DataFrame(weather_history) if weather_history else pd.DataFrame()
        commodity_df = pd.DataFrame(commodity_prices) if commodity_prices else pd.DataFrame()

        if not price_df.empty:
            price_df["timestamp"] = pd.to_datetime(price_df["timestamp"])
            price_df = price_df.set_index("timestamp")

        if not weather_df.empty:
            weather_df["timestamp"] = pd.to_datetime(weather_df["timestamp"])
            weather_df = weather_df.set_index("timestamp")

        if not commodity_df.empty:
            commodity_df["timestamp"] = pd.to_datetime(commodity_df["timestamp"])
            commodity_df = commodity_df.set_index("timestamp")

        if not price_df.empty:
            for ts, row in price_df.iterrows():
                record = {
                    "ds": ts,
                    "y": row.get("price", 0),
                    "hour": ts.hour,
                    "dayofweek": ts.dayofweek,
                    "month": ts.month,
                    "is_weekend": 1 if ts.dayofweek >= 5 else 0,
                    "temperature": 0,
                    "wind_speed": 0,
                    "cloud_cover": 0,
                    "co2_emission": 0,
                    "consumption": 0,
                    "production": 0
                }

                if not weather_df.empty:
                    closest_weather = weather_df.index[weather_df.index.get_indexer([ts], method="nearest")]
                    if len(closest_weather) > 0:
                        weather_row = weather_df.loc[closest_weather[0]]
                        record["temperature"] = weather_row.get("temperature", 0) or 0
                        record["wind_speed"] = weather_row.get("wind_speed", 0) or 0
                        record["cloud_cover"] = weather_row.get("cloud_cover", 0) or 0

                if not commodity_df.empty:
                    closest_commodity = commodity_df.index[commodity_df.index.get_indexer([ts], method="nearest")]
                    if len(closest_commodity) > 0:
                        commodity_row = commodity_df.loc[closest_commodity[0]]
                        record["co2_emission"] = commodity_row.get("co2_price", 0) or 0
                        record["consumption"] = commodity_row.get("consumption", 0) or 0
                        record["production"] = commodity_row.get("production", 0) or 0

                records.append(record)

        df = pd.DataFrame(records)
        return df

    def _add_lag_features(self, df: pd.DataFrame) -> pd.DataFrame:
        if "y" in df.columns:
            df["y_lag_1"] = df["y"].shift(1)
            df["y_lag_24"] = df["y"].shift(24)
            df["y_lag_168"] = df["y"].shift(168)
            df["y_rolling_24"] = df["y"].rolling(window=24, min_periods=1).mean()
            df["y_rolling_168"] = df["y"].rolling(window=168, min_periods=1).mean()
        return df

    async def train(self, data: Dict[str, Any]):
        try:
            df = self._prepare_features(data)

            if len(df) < 48:
                logger.warning("Not enough data for training, need at least 48 hours")
                return

            df = self._add_lag_features(df)
            df = df.dropna()

            extra_regressors = [
                "hour", "dayofweek", "month", "is_weekend",
                "temperature", "wind_speed", "cloud_cover",
                "co2_emission", "consumption", "production",
                "y_lag_1", "y_lag_24", "y_lag_168",
                "y_rolling_24", "y_rolling_168"
            ]

            self.model = Prophet(
                daily_seasonality=True,
                weekly_seasonality=True,
                yearly_seasonality=True,
                changepoint_prior_scale=0.05,
                seasonality_prior_scale=10
            )

            for reg in extra_regressors:
                if reg in df.columns:
                    self.model.add_regressor(reg)

            self.model.fit(df[["ds", "y"] + extra_regressors])
            self._save_model()

            logger.info(f"Model trained on {len(df)} samples")

        except Exception as e:
            logger.error(f"Error training model: {e}")
            raise

    async def predict(
        self,
        data: Dict[str, Any],
        days: int = 7,
        vat_percent: float = 25,
        fixed_cost_kwh: float = 0.1293
    ) -> List[Dict]:
        if self.model is None:
            logger.warning("No model available, using simple prediction")
            return await self._simple_predict(data, days, vat_percent, fixed_cost_kwh)

        try:
            future = pd.DataFrame()
            now = datetime.utcnow()
            hours_needed = days * 24

            future["ds"] = [now + timedelta(hours=i) for i in range(hours_needed)]
            future["hour"] = future["ds"].dt.hour
            future["dayofweek"] = future["ds"].dt.dayofweek
            future["month"] = future["ds"].dt.month
            future["is_weekend"] = (future["ds"].dt.dayofweek >= 5).astype(int)

            weather_forecast = data.get("weather_forecast", [])
            if weather_forecast:
                weather_df = pd.DataFrame(weather_forecast)
                weather_df["timestamp"] = pd.to_datetime(weather_df["timestamp"])

                for i, row in future.iterrows():
                    ts = row["ds"]
                    closest = weather_df.index[weather_df.index.get_indexer([ts], method="nearest")]
                    if len(closest) > 0:
                        weather_row = weather_df.loc[closest[0]]
                        future.at[i, "temperature"] = weather_row.get("temperature", 0) or 0
                        future.at[i, "wind_speed"] = weather_row.get("wind_speed", 0) or 0
                        future.at[i, "cloud_cover"] = weather_row.get("cloud_cover", 0) or 0
                    else:
                        future.at[i, "temperature"] = 0
                        future.at[i, "wind_speed"] = 0
                        future.at[i, "cloud_cover"] = 0
            else:
                future["temperature"] = 0
                future["wind_speed"] = 0
                future["cloud_cover"] = 0

            historical_prices = data.get("historical_prices", [])
            if historical_prices:
                hist_df = pd.DataFrame(historical_prices)
                hist_df["timestamp"] = pd.to_datetime(hist_df["timestamp"])
                hist_df = hist_df.set_index("timestamp")

                for i, row in future.iterrows():
                    ts = row["ds"]
                    lag_1_ts = ts - timedelta(hours=1)
                    lag_24_ts = ts - timedelta(hours=24)
                    lag_168_ts = ts - timedelta(hours=168)

                    for lag_ts, lag_col in [
                        (lag_1_ts, "y_lag_1"),
                        (lag_24_ts, "y_lag_24"),
                        (lag_168_ts, "y_lag_168")
                    ]:
                        if lag_ts in hist_df.index:
                            future.at[i, lag_col] = hist_df.loc[lag_ts, "price"]
                        else:
                            future.at[i, lag_col] = 0

                    window_start = ts - timedelta(hours=24)
                    window_df = hist_df[(hist_df.index >= window_start) & (hist_df.index < ts)]
                    future.at[i, "y_rolling_24"] = window_df["price"].mean() if len(window_df) > 0 else 0

                    week_start = ts - timedelta(hours=168)
                    week_df = hist_df[(hist_df.index >= week_start) & (hist_df.index < ts)]
                    future.at[i, "y_rolling_168"] = week_df["price"].mean() if len(week_df) > 0 else 0
            else:
                for col in ["y_lag_1", "y_lag_24", "y_lag_168", "y_rolling_24", "y_rolling_168"]:
                    future[col] = 0

            commodity_prices = data.get("commodity_prices", [])
            if commodity_prices:
                commodity_df = pd.DataFrame(commodity_prices)
                commodity_df["timestamp"] = pd.to_datetime(commodity_df["timestamp"])
                commodity_df = commodity_df.set_index("timestamp")

                for i, row in future.iterrows():
                    ts = row["ds"]
                    closest = commodity_df.index[commodity_df.index.get_indexer([ts], method="nearest")]
                    if len(closest) > 0:
                        commodity_row = commodity_df.loc[closest[0]]
                        future.at[i, "co2_emission"] = commodity_row.get("co2_price", 0) or 0
                        future.at[i, "consumption"] = commodity_row.get("consumption", 0) or 0
                        future.at[i, "production"] = commodity_row.get("production", 0) or 0
                    else:
                        future.at[i, "co2_emission"] = 0
                        future.at[i, "consumption"] = 0
                        future.at[i, "production"] = 0
            else:
                future["co2_emission"] = 0
                future["consumption"] = 0
                future["production"] = 0

            extra_regressors = [
                "hour", "dayofweek", "month", "is_weekend",
                "temperature", "wind_speed", "cloud_cover",
                "co2_emission", "consumption", "production",
                "y_lag_1", "y_lag_24", "y_lag_168",
                "y_rolling_24", "y_rolling_168"
            ]

            for reg in extra_regressors:
                if reg not in future.columns:
                    future[reg] = 0

            forecast = self.model.predict(future[["ds"] + extra_regressors])

            results = []
            for _, row in forecast.iterrows():
                price = max(0, row["yhat"])
                vat = price * (vat_percent / 100)
                price_with_cost = price + vat + fixed_cost_kwh

                confidence = max(0.5, min(1.0, 1 - (row.get("yhat_upper", price) - row.get("yhat_lower", price)) / (price + 0.001)))

                factors = {
                    "temperature": float(row.get("temperature", 0)),
                    "wind_speed": float(row.get("wind_speed", 0)),
                    "cloud_cover": float(row.get("cloud_cover", 0)),
                    "co2_emission": float(row.get("co2_emission", 0)),
                    "hour": int(row.get("hour", 0)),
                    "is_weekend": bool(row.get("is_weekend", 0))
                }

                results.append({
                    "timestamp": row["ds"].strftime("%Y-%m-%dT%H:%M:%S"),
                    "price": round(price, 4),
                    "price_with_cost": round(price_with_cost, 4),
                    "vat": round(vat, 4),
                    "fixed_cost": round(fixed_cost_kwh, 4),
                    "confidence": round(confidence, 2),
                    "factors": factors
                })

            return results

        except Exception as e:
            logger.error(f"Error in prediction: {e}")
            return await self._simple_predict(data, days, vat_percent, fixed_cost_kwh)

    async def _simple_predict(
        self,
        data: Dict[str, Any],
        days: int,
        vat_percent: float,
        fixed_cost_kwh: float
    ) -> List[Dict]:
        historical_prices = data.get("historical_prices", [])

        if not historical_prices:
            return []

        df = pd.DataFrame(historical_prices)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["hour"] = df["timestamp"].dt.hour
        df["dayofweek"] = df["timestamp"].dt.dayofweek

        hourly_avg = df.groupby(["hour", "dayofweek"])["price"].mean().to_dict()

        results = []
        now = datetime.utcnow()

        for i in range(days * 24):
            future_time = now + timedelta(hours=i)
            hour = future_time.hour
            dayofweek = future_time.dayofweek

            price = hourly_avg.get((hour, dayofweek), df["price"].mean())
            price = max(0, price)

            vat = price * (vat_percent / 100)
            price_with_cost = price + vat + fixed_cost_kwh

            results.append({
                "timestamp": future_time.strftime("%Y-%m-%dT%H:%M:%S"),
                "price": round(price, 4),
                "price_with_cost": round(price_with_cost, 4),
                "vat": round(vat, 4),
                "fixed_cost": round(fixed_cost_kwh, 4),
                "confidence": 0.6,
                "factors": {
                    "method": "simple_average",
                    "hour": hour,
                    "dayofweek": dayofweek
                }
            })

        return results
