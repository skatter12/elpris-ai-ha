import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)

MODEL_DIR = Path("/config/elpris_ai")
MODEL_DIR.mkdir(parents=True, exist_ok=True)


class PricePredictor:
    def __init__(self):
        self.model: Optional[GradientBoostingRegressor] = None
        self.scaler = StandardScaler()
        self.model_path = MODEL_DIR / "price_model.pkl"
        self.scaler_path = MODEL_DIR / "scaler.pkl"
        self._load_model()

    def _load_model(self):
        if self.model_path.exists() and self.scaler_path.exists():
            try:
                with open(self.model_path, "rb") as f:
                    self.model = pickle.load(f)
                with open(self.scaler_path, "rb") as f:
                    self.scaler = pickle.load(f)
                logger.info("Loaded existing model")
            except Exception as e:
                logger.warning(f"Could not load model: {e}")
                self.model = None

    def _save_model(self):
        try:
            with open(self.model_path, "wb") as f:
                pickle.dump(self.model, f)
            with open(self.scaler_path, "wb") as f:
                pickle.dump(self.scaler, f)
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

        price_df = self._normalize_df(price_df)
        weather_df = self._normalize_df(weather_df)
        commodity_df = self._normalize_df(commodity_df)

        if not price_df.empty:
            for ts, row in price_df.iterrows():
                record = {
                    "timestamp": ts,
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
                    "production": 0,
                }

                if not weather_df.empty:
                    try:
                        closest_weather = weather_df.index[weather_df.index.get_indexer([ts], method="nearest")]
                        if len(closest_weather) > 0:
                            weather_row = weather_df.loc[closest_weather[0]]
                            record["temperature"] = weather_row.get("temperature", 0) or 0
                            record["wind_speed"] = weather_row.get("wind_speed", 0) or 0
                            record["cloud_cover"] = weather_row.get("cloud_cover", 0) or 0
                    except Exception:
                        pass

                if not commodity_df.empty:
                    try:
                        closest_commodity = commodity_df.index[commodity_df.index.get_indexer([ts], method="nearest")]
                        if len(closest_commodity) > 0:
                            commodity_row = commodity_df.loc[closest_commodity[0]]
                            record["co2_emission"] = commodity_row.get("co2_price", 0) or 0
                            record["consumption"] = commodity_row.get("consumption", 0) or 0
                            record["production"] = commodity_row.get("production", 0) or 0
                    except Exception:
                        pass

                records.append(record)

        return pd.DataFrame(records)

    def _add_lag_features(self, df: pd.DataFrame) -> pd.DataFrame:
        if "y" in df.columns:
            df["y_lag_1"] = df["y"].shift(1)
            df["y_lag_24"] = df["y"].shift(24)
            df["y_lag_168"] = df["y"].shift(168)
            df["y_rolling_24"] = df["y"].rolling(window=24, min_periods=1).mean()
            df["y_rolling_168"] = df["y"].rolling(window=168, min_periods=1).mean()
        return df

    @staticmethod
    def _normalize_df(df: pd.DataFrame, ts_col: str = "timestamp") -> pd.DataFrame:
        if df.empty or ts_col not in df.columns:
            return df
        df[ts_col] = pd.to_datetime(df[ts_col], utc=True).dt.tz_localize(None)
        df = df.drop_duplicates(subset=[ts_col], keep="first")
        df = df.set_index(ts_col)
        return df

    FEATURE_COLS = [
        "hour", "dayofweek", "month", "is_weekend",
        "temperature", "wind_speed", "cloud_cover",
        "co2_emission", "consumption", "production",
        "y_lag_1", "y_lag_24", "y_lag_168",
        "y_rolling_24", "y_rolling_168",
    ]

    async def train(self, data: Dict[str, Any]):
        try:
            df = self._prepare_features(data)

            if len(df) < 48:
                logger.warning("Not enough data for training, need at least 48 hours")
                return

            df = self._add_lag_features(df)
            df = df.dropna()

            X = df[self.FEATURE_COLS].fillna(0)
            y = df["y"]

            self.scaler = StandardScaler()
            X_scaled = self.scaler.fit_transform(X)

            self.model = GradientBoostingRegressor(
                n_estimators=200,
                max_depth=5,
                learning_rate=0.1,
                random_state=42,
            )
            self.model.fit(X_scaled, y)
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
        fixed_cost_kwh: float = 0.1293,
    ) -> List[Dict]:
        if self.model is None:
            logger.warning("No model available, using simple prediction")
            return await self._simple_predict(data, days, vat_percent, fixed_cost_kwh)

        try:
            now = datetime.utcnow()
            hours_needed = days * 24

            future_times = [now + timedelta(hours=i) for i in range(hours_needed)]

            future = pd.DataFrame({
                "ds": future_times,
                "hour": [t.hour for t in future_times],
                "dayofweek": [t.weekday() for t in future_times],
                "month": [t.month for t in future_times],
                "is_weekend": [1 if t.weekday() >= 5 else 0 for t in future_times],
            })

            historical_prices = data.get("historical_prices", [])
            hist_df = self._normalize_df(pd.DataFrame(historical_prices) if historical_prices else pd.DataFrame())

            weather_forecast = data.get("weather_forecast", [])
            weather_df = self._normalize_df(pd.DataFrame(weather_forecast) if weather_forecast else pd.DataFrame())

            commodity_prices = data.get("commodity_prices", [])
            commodity_df = self._normalize_df(pd.DataFrame(commodity_prices) if commodity_prices else pd.DataFrame())

            for col in ["temperature", "wind_speed", "cloud_cover"]:
                future[col] = 0.0
                if not weather_df.empty:
                    for i, row in future.iterrows():
                        ts = row["ds"]
                        try:
                            closest = weather_df.index[weather_df.index.get_indexer([ts], method="nearest")]
                            if len(closest) > 0:
                                future.at[i, col] = weather_df.loc[closest[0], col] or 0
                        except Exception:
                            pass

            for col in ["co2_emission", "consumption", "production"]:
                future[col] = 0.0
            if not commodity_df.empty:
                for i, row in future.iterrows():
                    ts = row["ds"]
                    try:
                        closest = commodity_df.index[commodity_df.index.get_indexer([ts], method="nearest")]
                        if len(closest) > 0:
                            c_row = commodity_df.loc[closest[0]]
                            future.at[i, "co2_emission"] = c_row.get("co2_price", 0) or 0
                            future.at[i, "consumption"] = c_row.get("consumption", 0) or 0
                            future.at[i, "production"] = c_row.get("production", 0) or 0
                    except Exception:
                        pass

            for col in ["y_lag_1", "y_lag_24", "y_lag_168", "y_rolling_24", "y_rolling_168"]:
                future[col] = 0.0

            if not hist_df.empty:
                for i, row in future.iterrows():
                    try:
                        ts = row["ds"]
                        for lag_hours, lag_col in [(1, "y_lag_1"), (24, "y_lag_24"), (168, "y_lag_168")]:
                            lag_ts = ts - timedelta(hours=lag_hours)
                            if lag_ts in hist_df.index:
                                future.at[i, lag_col] = hist_df.loc[lag_ts, "price"]
                            else:
                                nearest = hist_df.index[hist_df.index.get_indexer([lag_ts], method="nearest")]
                                if len(nearest) > 0:
                                    future.at[i, lag_col] = hist_df.loc[nearest[0], "price"]

                        window_start = ts - timedelta(hours=24)
                        w = hist_df[(hist_df.index >= window_start) & (hist_df.index < ts)]
                        future.at[i, "y_rolling_24"] = w["price"].mean() if len(w) > 0 else 0

                        week_start = ts - timedelta(hours=168)
                        w = hist_df[(hist_df.index >= week_start) & (hist_df.index < ts)]
                        future.at[i, "y_rolling_168"] = w["price"].mean() if len(w) > 0 else 0
                    except Exception:
                        pass

            X_future = future[self.FEATURE_COLS].fillna(0)
            X_scaled = self.scaler.transform(X_future)
            predictions = self.model.predict(X_scaled)

            actual_prices = {}
            for p in historical_prices:
                try:
                    ts = pd.Timestamp(p["timestamp"]).tz_localize(None)
                    actual_prices[ts] = p["price"]
                except Exception:
                    pass

            results = []
            for i, (ts, price_raw) in enumerate(zip(future_times, predictions)):
                ts_normalized = pd.Timestamp(ts).tz_localize(None)
                if ts_normalized in actual_prices:
                    price = max(0, actual_prices[ts_normalized])
                    method = "actual"
                    conf = 1.0
                else:
                    price = max(0, float(price_raw))
                    method = "gradient_boosting"
                    conf = 0.7

                vat = price * (vat_percent / 100)
                price_with_cost = price + vat + fixed_cost_kwh

                results.append({
                    "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S"),
                    "price": round(price, 4),
                    "price_with_cost": round(price_with_cost, 4),
                    "vat": round(vat, 4),
                    "fixed_cost": round(fixed_cost_kwh, 4),
                    "confidence": conf,
                    "factors": {
                        "method": method,
                        "hour": ts.hour,
                        "dayofweek": ts.weekday(),
                    },
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
        fixed_cost_kwh: float,
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
            dayofweek = future_time.weekday()

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
                    "dayofweek": dayofweek,
                },
            })

        return results
