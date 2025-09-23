#!/usr/bin/env python
# -*- coding: utf-8 -*-
# vim: sw=4:ts=4:expandtab
"""
mezmorize.utils
~~~~~~~~~~~~~~~

Provides mezmorize utility functions
"""

from os import getenv, path as p
from pathlib import Path
from subprocess import call, DEVNULL
from copy import copy
from pickle import DEFAULT_PROTOCOL
from functools import wraps
from typing import Optional

try:
    import pylibmc
except ImportError:
    pylibmc = None

try:
    import pymemcache
except ImportError:
    pymemcache = None

try:
    import bmemcached
except ImportError:
    bmemcached = None

try:
    import redis
except ImportError:
    redis = None

BASEDIR = p.dirname(__file__)
DEF_CACHE_DIR = Path(BASEDIR).joinpath("cache", f"pickle_v{DEFAULT_PROTOCOL}")

DEF_THRESHOLD = 500
DEF_DEFAULT_TIMEOUT = 300
DEF_MC_HOST = DEF_REDIS_HOST = "localhost"
DEF_MC_PORT = 11211
DEF_REDIS_PORT = 6379
ENCODING = "utf-8"

ALL_MEMCACHES = (
    ("pylibmc", pylibmc),
    ("bmemcached", bmemcached),
    ("pymemcache", pymemcache),
)

DEF_MC_SERVERS = "{}:{}".format(DEF_MC_HOST, DEF_MC_PORT)
MC_SERVERS = getenv("MEMCACHIER_SERVERS") or getenv("MEMCACHEDCLOUD_SERVERS")
MC_SERVERS = MC_SERVERS or getenv("MEMCACHE_SERVERS") or DEF_MC_SERVERS
MC_USERNAME = getenv("MEMCACHIER_USERNAME") or getenv("MEMCACHEDCLOUD_USERNAME")
MC_PASSWORD = getenv("MEMCACHIER_PASSWORD") or getenv("MEMCACHEDCLOUD_PASSWORD")

REDIS_HOST = getenv("REDIS_PORT_6379_TCP_ADDR", DEF_REDIS_HOST)
DEF_REDIS_URL = "redis://{}:{}".format(REDIS_HOST, DEF_REDIS_PORT)
REDIS_URL = getenv("REDIS_URL") or getenv("REDISTOGO_URL") or DEF_REDIS_URL

CACHE_DIR = getenv("CACHE_DIR", default=DEF_CACHE_DIR)

CACHE_CONFIGS = {
    "simple": {"CACHE_TYPE": "simple"},
    "null": {"CACHE_TYPE": "null"},
    "redis": {"CACHE_TYPE": "redis", "CACHE_REDIS_URL": REDIS_URL},
    "filesystem": {"CACHE_TYPE": "filesystem", "CACHE_DIR": CACHE_DIR},
    "memcached": {"CACHE_TYPE": "memcached", "CACHE_MEMCACHED_SERVERS": [MC_SERVERS]},
    "saslmemcached": {
        "CACHE_TYPE": "saslmemcached",
        "CACHE_MEMCACHED_SERVERS": [MC_SERVERS],
        "CACHE_MEMCACHED_USERNAME": MC_USERNAME,
        "CACHE_MEMCACHED_PASSWORD": MC_PASSWORD,
    },
    "spreadsaslmemcached": {
        "CACHE_TYPE": "spreadsaslmemcached",
        "CACHE_MEMCACHED_SERVERS": [MC_SERVERS],
        "CACHE_MEMCACHED_USERNAME": MC_USERNAME,
        "CACHE_MEMCACHED_PASSWORD": MC_PASSWORD,
    },
}

HEROKU = getenv("HEROKU") or (getenv("DYNO") and getenv("HOME") == "/app")

HEROKU_PROCESSES = {
    "postgres": ["DATABASE_URL"],
    "redis": ["REDIS_URL", "REDISTOGO_URL"],
    "memcache": ["MEMCACHIER_SERVERS", "MEMCACHE_SERVERS"],
}


def pgrep(process):
    envs = HEROKU_PROCESSES.get(process, [])
    any_env = HEROKU and any(map(getenv, envs))
    return any_env or call(["pgrep", process], stdout=DEVNULL) == 0


HAS_MEMCACHE = (pylibmc or pymemcache or bmemcached) and pgrep("memcache")
AVAIL_MEMCACHES = [k for k, v in ALL_MEMCACHES if HAS_MEMCACHE and v]
HAS_REDIS = redis and pgrep("redis")


def parse_verbosity(verbose=0, quiet: Optional[bool] = None):
    if quiet:
        verbosity = "0"
    elif verbose:
        verbosity = str(verbose)
    else:
        verbosity = ""

    return verbosity


def get_cache_type(cache=None, spread=False, cache_dir=CACHE_DIR, **kwargs):
    if HAS_REDIS and HAS_MEMCACHE and not cache:
        cache = "memcached"
    elif not cache:
        cache = "redis" if HAS_REDIS else "memcached"

    if HAS_MEMCACHE and "memcached" in cache:
        if MC_USERNAME and spread:
            cache_type = "spreadsaslmemcached"
        elif MC_USERNAME:
            cache_type = "saslmemcached"
        else:
            cache_type = "memcached"
    elif HAS_REDIS and cache == "redis":
        cache_type = "redis"
    elif cache_dir and cache not in {"simple", "null"}:
        cache_type = "filesystem"
    elif cache != "null":
        cache_type = "simple"
    else:
        cache_type = "null"

    return cache_type


def get_cache_config(cache_type, db=None, SELF_SIGNED_CERTS=False, **kwargs):
    config = copy(CACHE_CONFIGS[cache_type])
    redis_url = config.get("CACHE_REDIS_URL")

    if redis_url and SELF_SIGNED_CERTS:
        # https://stackoverflow.com/q/65042551/408556
        redis_url += "?ssl_cert_reqs=none"

    if redis_url and db:
        config["CACHE_REDIS_URL"] = "{}/{}".format(redis_url, db)

    options = {k: v for k, v in kwargs.items() if v is not None}
    options.setdefault("CACHE_THRESHOLD", DEF_THRESHOLD)
    options.setdefault("CACHE_DEFAULT_TIMEOUT", DEF_DEFAULT_TIMEOUT)
    config.update(options)
    return config


def get_pylibmc_client(servers, timeout=None, binary=True, **kwargs):
    from pylibmc import Client

    try:
        from pylibmc import TooBig
    except ImportError:
        from pylibmc import Error, ServerError

        TooBig = (Error, ServerError)

    if timeout:
        kwargs["behaviors"] = {"connect_timeout": timeout}

    client = Client(servers, binary=binary, **kwargs)
    client.TooBig = TooBig
    return client


def get_pymemcache_client(servers, timeout=None, **kwargs):
    from pymemcache.client.hash import HashClient

    from pymemcache.serde import (
        python_memcache_serializer,
        python_memcache_deserializer,
    )

    kwargs.setdefault("serializer", python_memcache_serializer)
    kwargs.setdefault("deserializer", python_memcache_deserializer)

    if timeout:
        kwargs["timeout"] = timeout

    split = [s.split(":") for s in servers]
    _servers = [(host, int(port)) for host, port in split]
    client = HashClient(_servers, **kwargs)

    try:
        client.TooBig = ConnectionResetError
    except NameError:
        import socket

        client.TooBig = socket.error

    return client


def get_bmemcached_client(servers, timeout=None, **kwargs):
    from bmemcached import Client

    if timeout:
        kwargs["socket_timeout"] = timeout

    client = Client(servers, **kwargs)
    client.TooBig = None
    return client


def decode(word):
    try:
        decoded = word.decode(ENCODING)
    except AttributeError:
        decoded = word

    return decoded


def hashable(obj):
    try:
        items = frozenset((k, hashable(v)) for k, v in obj.iteritems())
    except TypeError:
        items = tuple(hashable(item) for item in obj)
    except AttributeError:
        items = obj

    return items


# https://stackoverflow.com/questions/11815873
def simple_memoize(func):
    seen = {}

    @wraps(func)
    def wrapper(*args, **kwargs):
        hashed = hashable(args[0])

        if hashed in seen:
            new_piece = seen[hashed]
        else:
            new_piece = func(*args, **kwargs)
            seen.update({hashed: new_piece})

        return new_piece

    return wrapper
