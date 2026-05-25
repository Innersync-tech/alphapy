#!/usr/bin/env python3
"""
Performance testing script for Alphapy bot.

Tests database performance, API response times, and memory usage.
Run with: python performance_test.py
"""

import asyncio
import os
import sys
import time

import psutil

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import config_local as config
except ImportError:
    import config

async def test_database_performance():
    """Test database connection and query performance."""
    print("🗄️  Testing Database Performance...")

    try:
        from utils.database_helpers import DatabaseManager

        manager = DatabaseManager("perf_test", {"DATABASE_URL": config.DATABASE_URL or ""})

        # Test connection pool creation
        start_time = time.time()
        pool = await manager.ensure_pool()
        pool_time = (time.time() - start_time) * 1000
        print(f"✅ Database connection: {pool_time:.1f}ms")

        # Test simple queries (uses acquire_safe via manager.connection())
        queries = [
            ("COUNT tables", "SELECT COUNT(*) FROM information_schema.tables"),
            ("COUNT reminders", "SELECT COUNT(*) FROM reminders"),
            ("COUNT settings", "SELECT COUNT(*) FROM bot_settings"),
            ("Recent reminders", "SELECT id, name FROM reminders ORDER BY id DESC LIMIT 10"),
        ]

        for name, query in queries:
            start_time = time.time()
            async with manager.connection() as conn:
                result = await conn.fetchval(query)
            query_time = (time.time() - start_time) * 1000
            print(f"✅ {name}: {query_time:.1f}ms (result: {result})")

        await pool.close()
        print("✅ Database tests completed")

    except ImportError:
        print("❌ asyncpg not available")
    except Exception as e:
        print(f"❌ Database test failed: {e}")

async def test_api_performance():
    """Test API endpoint performance."""
    print("\n🌐 Testing API Performance...")

    try:
        import httpx

        core_url = getattr(config, "CORE_API_URL", "").rstrip("/")
        api_key = getattr(config, "ALPHAPY_SERVICE_KEY", None)

        if not core_url or not api_key:
            print("⚠️  Core-API not configured, skipping API tests")
            return

        endpoints = [
            ("Health check", f"{core_url}/health", "GET"),
            ("Premium verify", f"{core_url}/api/premium/verify", "POST", {"user_id": 123, "guild_id": 456}),
        ]

        async with httpx.AsyncClient(timeout=10.0) as client:
            for name, url, method, *data in endpoints:
                try:
                    start_time = time.time()
                    headers = {"X-API-Key": api_key}

                    if method == "GET":
                        response = await client.get(url, headers=headers)
                    elif method == "POST" and data:
                        response = await client.post(url, headers=headers, json=data[0])
                    else:
                        continue

                    response_time = (time.time() - start_time) * 1000

                    if response.is_success:
                        print(f"✅ {name}: {response_time:.1f}ms (status: {response.status_code})")
                    else:
                        print(f"⚠️  {name}: {response_time:.1f}ms (status: {response.status_code})")
                except Exception as e:
                    print(f"❌ {name}: Failed ({e})")

        print("✅ API tests completed")

    except ImportError:
        print("❌ httpx not available")
    except Exception as e:
        print(f"❌ API test failed: {e}")

def test_memory_usage():
    """Test current memory usage."""
    print("\n🧠 Testing Memory Usage...")

    try:
        process = psutil.Process(os.getpid())
        memory_info = process.memory_info()

        memory_mb = memory_info.rss / 1024 / 1024
        memory_percent = process.memory_percent()

        print(f"✅ Memory usage: {memory_mb:.1f}MB ({memory_percent:.1f}%)")
        print(f"📊 Process info: PID {os.getpid()}")

        # Memory breakdown
        if hasattr(memory_info, 'vms'):
            vms_mb = memory_info.vms / 1024 / 1024
            print(f"📈 Virtual memory: {vms_mb:.1f}MB")
        print("✅ Memory test completed")

    except ImportError:
        print("❌ psutil not available")
    except Exception as e:
        print(f"❌ Memory test failed: {e}")

async def main():
    """Run all performance tests."""
    print("🚀 Alphapy Performance Test Suite")
    print("=" * 50)

    # Memory before tests
    try:
        process = psutil.Process(os.getpid())
        memory_before = process.memory_info().rss / 1024 / 1024
    except Exception:
        memory_before = 0

    # Run tests
    await test_database_performance()
    await test_api_performance()
    test_memory_usage()

    # Memory after tests
    try:
        memory_after = process.memory_info().rss / 1024 / 1024
        memory_delta = memory_after - memory_before
        print(f"📊 Memory delta: {memory_delta:+.1f}MB")
    except Exception:
        pass

    print("\n" + "=" * 50)
    print("🏁 Performance testing completed!")
    print("\nRecommendations:")
    print("- Monitor database query times in production")
    print("- Check API response times under load")
    print("- Watch memory usage for memory leaks")
    print("- Consider adding more database indexes for large datasets")

if __name__ == "__main__":
    asyncio.run(main())
