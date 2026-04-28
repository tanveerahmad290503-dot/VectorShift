import os

import redis.asyncio as redis
from fastapi import HTTPException
from kombu.utils.url import safequote
from redis.exceptions import RedisError

redis_host = safequote(os.environ.get('REDIS_HOST', 'localhost'))
redis_client = redis.Redis(host=redis_host, port=6379, db=0)


async def _raise_if_redis_unavailable(exc: Exception):
    raise HTTPException(
        status_code=503,
        detail='Redis is unavailable at localhost:6379. Start redis-server and retry.',
    ) from exc


async def add_key_value_redis(key, value, expire=None):
    try:
        await redis_client.set(key, value)
        if expire:
            await redis_client.expire(key, expire)
    except RedisError as exc:
        await _raise_if_redis_unavailable(exc)


async def get_value_redis(key):
    try:
        return await redis_client.get(key)
    except RedisError as exc:
        await _raise_if_redis_unavailable(exc)


async def delete_key_redis(key):
    try:
        await redis_client.delete(key)
    except RedisError as exc:
        await _raise_if_redis_unavailable(exc)
