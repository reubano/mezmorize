#!/usr/bin/env python
# -*- coding: utf-8 -*-
# vim: sw=4:ts=4:expandtab
"""
mezmorize.backends
~~~~~~~~~~~~~~~~~~

Provides mezmorize storage mechanisms
"""
# pylint: disable=range-builtin-not-iterating,filter-builtin-not-iterating

import pickle

from itertools import chain
from functools import partial
from operator import contains

from cachelib import (
    NullCache,
    SimpleCache,
    MemcachedCache as _MemcachedCache,
    FileSystemCache,
    RedisCache,
)

from .utils import (
    DEF_MC_SERVERS,
    HAS_MEMCACHE,
    AVAIL_MEMCACHES,
    get_pylibmc_client,
    get_pymemcache_client,
    get_bmemcached_client,
    DEF_REDIS_HOST,
    DEF_REDIS_PORT,
)

try:
    from redis import from_url
except ImportError:
    from_url = None

CONFIG_LOOKUP = {
    "servers": "CACHE_MEMCACHED_SERVERS",
    "threshold": "CACHE_THRESHOLD",
    "username": "CACHE_MEMCACHED_USERNAME",
    "password": "CACHE_MEMCACHED_PASSWORD",
    "key_prefix": "CACHE_KEY_PREFIX",
    "timeout": "connect_timeout",
}


def gen_defaults(*keys, **config):
    for key in keys:
        config_key = CONFIG_LOOKUP[key]

        if config_key in config:
            yield (key, config[config_key])


def get_mc_client(module_name, binary=True, **kwargs):
    servers = kwargs.pop("servers", (DEF_MC_SERVERS,))
    timeout = kwargs.pop("timeout", None)

    if module_name == "pylibmc":
        client = get_pylibmc_client(servers, timeout=timeout, binary=binary, **kwargs)
    elif module_name == "pymemcache":
        client = get_pymemcache_client(servers, timeout=timeout, **kwargs)
    elif module_name == "bmemcached":
        client = get_bmemcached_client(servers, timeout=timeout, **kwargs)

    return client


class MemcachedCache(_MemcachedCache):
    def __init__(self, *args, **kwargs):
        if not HAS_MEMCACHE:
            raise RuntimeError("No memcache module found.")

        compat_memcaches = kwargs.pop("compat_memcaches", AVAIL_MEMCACHES)
        avail_memcaches = set(AVAIL_MEMCACHES).intersection(compat_memcaches)

        if not avail_memcaches:
            raise RuntimeError("No compatible memcache module found.")

        preferred_mc = kwargs.get("preferred_memcache", "pylibmc")

        if len(avail_memcaches) == 1 or preferred_mc not in avail_memcaches:
            filterer = partial(contains, avail_memcaches)
            preferred_mc = next(filter(filterer, AVAIL_MEMCACHES))

        whitelist = {"default_timeout", "key_prefix"}
        blacklist = whitelist.union(["preferred_memcache"])
        mkwargs = {k: v for k, v in kwargs.items() if k not in blacklist}
        skwargs = {k: v for k, v in kwargs.items() if k in whitelist}

        client = get_mc_client(preferred_mc, **mkwargs)
        super(MemcachedCache, self).__init__(servers=client, **skwargs)
        self.TooBig = client.TooBig
        self.client_name = preferred_mc


class SASLMemcachedCache(MemcachedCache):
    def __init__(self, *args, **kwargs):
        kwargs["compat_memcaches"] = ("pylibmc", "bmemcached")
        super(SASLMemcachedCache, self).__init__(*args, **kwargs)


def null(config, *args, **kwargs):
    return NullCache()


def simple(config, *args, **kwargs):
    defaults = dict(gen_defaults("threshold", **config))
    defaults.update(kwargs)
    return SimpleCache(*args, **defaults)


def memcached(config, *args, **kwargs):
    keys = ("timeout", "servers", "key_prefix")
    defaults = dict(gen_defaults(*keys, **config))
    defaults.update(kwargs)
    return MemcachedCache(*args, **defaults)


def saslmemcached(config, **kwargs):
    keys = ("timeout", "servers", "username", "password", "key_prefix")
    defaults = dict(gen_defaults(*keys, **config))
    defaults.update(kwargs)
    return SASLMemcachedCache(**defaults)


def filesystem(config, *args, **kwargs):
    args = chain([config["CACHE_DIR"]], args)
    defaults = dict(gen_defaults("threshold", **config))
    defaults.update(kwargs)
    return FileSystemCache(*args, **defaults)


def redis(config, *args, **kwargs):
    kwargs.setdefault("host", config.get("CACHE_REDIS_HOST", DEF_REDIS_HOST))
    kwargs.setdefault("port", config.get("CACHE_REDIS_PORT", DEF_REDIS_PORT))
    kwargs.setdefault("password", config.get("CACHE_REDIS_PASSWORD"))
    kwargs.setdefault("key_prefix", config.get("CACHE_KEY_PREFIX"))
    kwargs.setdefault("db", config.get("CACHE_REDIS_DB"))
    redis_url = config.get("CACHE_REDIS_URL")

    if redis_url:
        kwargs["host"] = from_url(redis_url, db=kwargs.pop("db", None))

    return RedisCache(*args, **kwargs)


class SpreadSASLMemcachedCache(SASLMemcachedCache):
    """
    Simple Subclass of SASLMemcached client that spread value across multiple
    key is they are bigger than a given threshold.

    Spreading require using pickle to store the value, which can significantly
    impact the performances.
    """

    DEF_CHUNKSIZE = 2**20 - 2**7  # 1048448
    DEF_MAXCHUNKS = 32

    def __init__(self, *args, **kwargs):
        """
        Kwargs:
            chunksize (int): max length of a pickled object that can fit in
                memcached (memcache has an upper limit of 1MB for values,
                default: 1048448)
        """
        self.CHUNKSIZE = kwargs.get("chunksize", self.DEF_CHUNKSIZE)
        self.MARKER = "SpreadSASLMemcachedCache.SpreadedValue"
        self.MAXCHUNKS = kwargs.get("maxchunks", self.DEF_MAXCHUNKS)
        super(SpreadSASLMemcachedCache, self).__init__(*args, **kwargs)
        self.super = super(SpreadSASLMemcachedCache, self)

    def _genkeys(self, key):
        return ("{}.{}".format(key, i) for i in range(self.MAXCHUNKS))

    def _gen_kv(self, key, pickled):
        chunks = range(0, len(pickled), self.CHUNKSIZE)

        if len(chunks) > self.MAXCHUNKS:
            msg = "Value exceed maximum number of keys ({})"
            raise ValueError(msg.format(self.MAXCHUNKS))

        for i in chunks:
            _key = "{}.{}".format(key, i // self.CHUNKSIZE)
            _value = pickled[i : i + self.CHUNKSIZE]
            yield _key, _value

    def delete(self, key):
        value = self.super.get(key)
        self.super.delete(key)

        if value == self.MARKER:
            for skey in self._genkeys(key):
                self.super.delete(skey)

    def set(self, key, value, timeout=None):
        """set a value in cache, potentially spreading it across multiple key."""
        try:
            value = self.super.set(key, value, timeout=timeout)
        except self.TooBig:
            self.super.set(key, self.MARKER, timeout=timeout)
            pickled = pickle.dumps(value, 2)
            values = dict(self._gen_kv(key, pickled))
            value = self.super.set_many(values, timeout)

        return value

    def get(self, key):
        """get a value in cache, potentially from multiple keys."""
        value = self.super.get(key)

        if value == self.MARKER:
            keys = self._genkeys(key)
            result = self.super.get_many(*keys)
            serialized = b"".join(v for v in result if v is not None)
            value = pickle.loads(serialized) if serialized else None

        return value


def spreadsaslmemcached(config, *args, **kwargs):
    keys = ("timeout", "servers", "username", "password", "key_prefix")
    defaults = dict(gen_defaults(*keys, **config))
    defaults.update(kwargs)
    return SpreadSASLMemcachedCache(*args, **defaults)
