#!/usr/bin/env python
# -*- coding: utf-8 -*-

import random

from os import environ
from mezmorize import Cache

if True:
    config = {
        "DEBUG": True,
        "CACHE_TYPE": "memcached",
        "CACHE_MEMCACHED_SERVERS": [environ.get("MEMCACHE_SERVERS")],
    }
else:
    config = {"CACHE_TYPE": "simple"}

cache = Cache(**config)


@cache.memoize(60)
def add(a, b):
    return a + b + random.randrange(0, 1000)


@cache.memoize(60)
def sub(a, b):
    return a - b - random.randrange(0, 1000)


def delete_cache():
    cache.delete_memoized(add)
    cache.delete_memoized(sub)
    return "caches deleted"


if __name__ == "__main__":
    print("Initial add(2, 5): %s" % add(2, 5))
    print("Memoized add(2, 5): %s" % add(2, 5))
    print("Initial sub(2, 5): %s" % sub(2, 5))
    print("Memoized (sub(2, 5): %s" % sub(2, 5))
    print("Delete all caches")
    delete_cache()
    print("Initial add(2, 5): %s" % add(2, 5))
    print("Initial sub(2, 5): %s" % sub(2, 5))
