import asyncio
import os
from crawl4ai_service import Crawl4AIService

async def main():
    service = Crawl4AIService(
        redis_url=os.getenv("REDIS_URL", "redis://redis:6379"),
        database_url=os.getenv("DATABASE_URL"),
        config_path=os.getenv("ENRICHMENT_CONFIG_PATH", "/app/config/enrichment_policy.yml")
    )
    await service.start()

if __name__ == "__main__":
    asyncio.run(main())