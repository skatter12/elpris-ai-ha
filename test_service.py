#!/usr/bin/env python3
"""Simple test script for Elpris AI service."""
import asyncio
import aiohttp
import json
from datetime import datetime

BASE_URL = "http://localhost:8001"

async def test_health():
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_URL}/health") as resp:
            data = await resp.json()
            print(f"Health: {json.dumps(data, indent=2)}")
            return resp.status == 200

async def test_forecast():
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_URL}/forecast") as resp:
            data = await resp.json()
            print(f"Forecast: {len(data.get('forecast', []))} hours")
            if data.get('forecast'):
                first = data['forecast'][0]
                print(f"First price: {first['price_with_cost']:.4f} kr/kWh")
            return resp.status == 200

async def test_today():
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_URL}/forecast/today") as resp:
            data = await resp.json()
            print(f"Today: {len(data.get('prices', []))} hours")
            if data.get('cheapest_hour'):
                print(f"Cheapest: {data['cheapest_hour']['price_with_cost']:.4f} kr/kWh at {data['cheapest_hour']['timestamp']}")
            return resp.status == 200

async def test_update():
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{BASE_URL}/update") as resp:
            data = await resp.json()
            print(f"Update: {data}")
            return resp.status == 200

async def main():
    print("Testing Elpris AI Service...")
    print("=" * 50)

    try:
        print("\n1. Testing health endpoint...")
        if await test_health():
            print("✓ Health check passed")
        else:
            print("✗ Health check failed")
            return

        print("\n2. Testing forecast endpoint...")
        if await test_forecast():
            print("✓ Forecast check passed")
        else:
            print("✗ Forecast check failed")

        print("\n3. Testing today endpoint...")
        if await test_today():
            print("✓ Today check passed")
        else:
            print("✗ Today check failed")

        print("\n4. Testing update endpoint...")
        if await test_update():
            print("✓ Update check passed")
        else:
            print("✗ Update check failed")

        print("\n" + "=" * 50)
        print("All tests completed!")

    except Exception as e:
        print(f"Error during testing: {e}")
        print("\nMake sure the service is running on port 8001")

if __name__ == "__main__":
    asyncio.run(main())
