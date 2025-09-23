# -*- coding: utf-8 -*-
# vim: sw=4:ts=4:expandtab
"""
tests.test_cache
~~~~~~~~~~~~~~~~

Provides unit tests.
"""

import time
import random
import pytest

from mezmorize import Cache, function_namespace
from mezmorize.utils import HAS_MEMCACHE, HAS_REDIS, get_cache_config

from mezmorize.backends import (
    SimpleCache,
    FileSystemCache,
    RedisCache,
    MemcachedCache,
    SASLMemcachedCache,
    SpreadSASLMemcachedCache,
    AVAIL_MEMCACHES,
)

BIGINT = 2**21
BIGGERINT = 2**28


def setup_func(*args, **kwargs):
    namespace = kwargs.pop("namespace", None)
    client_name = kwargs.pop("client_name", None)

    if client_name:
        CACHE_OPTIONS = kwargs.get("CACHE_OPTIONS", {})
        CACHE_OPTIONS["preferred_memcache"] = client_name
        kwargs["CACHE_OPTIONS"] = CACHE_OPTIONS

    config = get_cache_config(*args, **kwargs)
    cache = Cache(namespace=namespace, **config)
    return cache


def check_cache_type(cache, cache_type):
    assert cache.config["CACHE_TYPE"] == cache_type


def check_cache_instance(cache, cache_instance):
    assert isinstance(cache.cache, cache_instance)


def check_client_name(cache, expected):
    assert cache.cache.client_name == expected


def check_too_big(cache, times, error=None):
    if cache.cache.TooBig:
        with pytest.raises(error or cache.cache.TooBig):
            cache.set("big", "a" * times)

        assert cache.get("big") is None
    else:
        cache.set("big", "a" * times)
        assert cache.get("big") == "a" * times


def check_set_delete(cache, key, value, multiplier=None):
    if multiplier:
        value *= multiplier

    cache.set(key, value)
    assert cache.get(key) == value

    cache.delete(key)
    assert cache.get(key) is None


class CacheClient:
    def __init__(self, cache_type="simple", **kwargs):
        self.cache = setup_func(cache_type, **kwargs)

    def clear_cache(self):
        self.cache.clear()


@pytest.fixture
def simple_client():
    client = CacheClient()
    yield client
    client.clear_cache()


@pytest.fixture
def timeout_client():
    client = CacheClient(CACHE_DEFAULT_TIMEOUT=1)
    yield client
    client.clear_cache()


@pytest.fixture
def namespace_client():
    client = CacheClient(namespace="https://github.com/reubano/mezmorize")
    yield client
    client.clear_cache()


@pytest.fixture
def fs_client():
    client = CacheClient("filesystem", CACHE_DIR="/tmp")
    yield client
    client.clear_cache()


@pytest.fixture(params=AVAIL_MEMCACHES)
def memcached_client(request):
    client = CacheClient("memcached", client_name=request.param)
    yield client
    client.clear_cache()


@pytest.fixture(params=AVAIL_MEMCACHES)
def saslmemcached_client(request):
    client = CacheClient("saslmemcached", client_name=request.param)
    yield client
    client.clear_cache()


@pytest.fixture(params=AVAIL_MEMCACHES)
def spreadsaslmemcached_client(request):
    client = CacheClient("spreadsaslmemcached", client_name=request.param)
    yield client
    client.clear_cache()


@pytest.fixture
def redis_client():
    client = CacheClient("redis", db=0)
    yield client
    client.clear_cache()


@pytest.fixture
def redis_custom_client():
    client = CacheClient("redis", db=2)
    yield client
    client.clear_cache()


class TestCache:
    def test_dict_config(self, simple_client):
        check_cache_type(simple_client.cache, "simple")
        check_cache_instance(simple_client.cache, SimpleCache)

    def test_000_set(self, simple_client):
        simple_client.cache.set("hi", "hello")
        assert simple_client.cache.get("hi") == "hello"

    def test_add(self, simple_client):
        simple_client.cache.add("hi", "hello")
        assert simple_client.cache.get("hi") == "hello"

        simple_client.cache.add("hi", "foobar")
        assert simple_client.cache.get("hi") == "hello"

    def test_add_unicode(self, simple_client):
        simple_client.cache.add("ȟį", "ƕɛĺłö")
        assert simple_client.cache.get("ȟį") == "ƕɛĺłö"

        simple_client.cache.add("ȟį", "fööƀåř")
        assert simple_client.cache.get("ȟį") == "ƕɛĺłö"

    def test_add_bytes(self, simple_client):
        simple_client.cache.add(b"hi", b"hello")
        assert simple_client.cache.get(b"hi") == b"hello"

        simple_client.cache.add(b"hi", b"foobar")
        assert simple_client.cache.get(b"hi") == b"hello"

    def test_delete(self, simple_client):
        check_set_delete(simple_client.cache, "hi", "hello")

    def test_delete_unicode(self, simple_client):
        check_set_delete(simple_client.cache, "ȟį", "ƕɛĺłö")

    def test_delete_bytes(self, simple_client):
        check_set_delete(simple_client.cache, b"hi", b"foobar")

    def test_memoize(self, simple_client):
        @simple_client.cache.memoize(5)
        def func(a, b):
            return a + b + random.randrange(0, 100000)

        result = func(5, 2)
        time.sleep(1)
        assert func(5, 2) == result

        result2 = func(5, 3)
        assert result2 != result

        time.sleep(6)
        assert func(5, 2) != result

        time.sleep(1)
        assert func(5, 3) != result2

    def test_timeout(self, timeout_client):
        @timeout_client.cache.memoize(50)
        def func(a, b):
            return a + b + random.randrange(0, 100000)

        result = func(5, 2)
        time.sleep(2)
        assert func(5, 2) == result

    def test_delete_timeout(self, simple_client):
        @simple_client.cache.memoize(5)
        def func(a, b):
            return a + b + random.randrange(0, 100000)

        result = func(5, 2)
        result2 = func(5, 3)
        time.sleep(1)

        assert func(5, 2) == result
        assert func(5, 2) == result
        assert func(5, 3) != result
        assert func(5, 3) == result2

        simple_client.cache.delete_memoized(func)
        assert func(5, 2) != result
        assert func(5, 3) != result2

    def test_delete_verhash(self, simple_client):
        @simple_client.cache.memoize(5)
        def func(a, b):
            return a + b + random.randrange(0, 100000)

        result = func(5, 2)
        result2 = func(5, 3)
        time.sleep(1)

        assert func(5, 2) == result
        assert func(5, 2) == result
        assert func(5, 3) != result
        assert func(5, 3) == result2

        fname = function_namespace(func)[0]
        version_key = simple_client.cache._memvname(fname)
        assert simple_client.cache.get(version_key) is not None

        simple_client.cache.delete_memoized_verhash(func)
        assert simple_client.cache.get(version_key) is None
        assert func(5, 2) != result
        assert func(5, 3) != result2
        assert simple_client.cache.get(version_key) is not None

    def test_delete_rand(self, simple_client):
        @simple_client.cache.memoize()
        def func(a, b):
            return a + b + random.randrange(0, 100000)

        result_a = func(5, 1)
        result_b = func(5, 2)
        assert func(5, 1) == result_a
        assert func(5, 2) == result_b

        simple_client.cache.delete_memoized(func, 5, 2)
        assert func(5, 1) == result_a
        assert func(5, 2) != result_b

    def test_args(self, simple_client):
        @simple_client.cache.memoize()
        def func(a, b):
            return sum(a) + sum(b) + random.randrange(0, 100000)

        result_a = func([5, 3, 2], [1])
        result_b = func([3, 3], [3, 1])
        assert func([5, 3, 2], [1]) == result_a
        assert func([3, 3], [3, 1]) == result_b

        simple_client.cache.delete_memoized(func, [5, 3, 2], [1])
        assert func([5, 3, 2], [1]) != result_a
        assert func([3, 3], [3, 1]) == result_b

    def test_kwargs(self, simple_client):
        @simple_client.cache.memoize()
        def func(a, b=None):
            return a + sum(b.values()) + random.randrange(0, 100000)

        result_a = func(1, {"one": 1, "two": 2})
        result_b = func(5, {"three": 3, "four": 4})
        assert func(1, {"one": 1, "two": 2}) == result_a
        assert func(5, {"three": 3, "four": 4}) == result_b

        simple_client.cache.delete_memoized(func, 1, {"one": 1, "two": 2})
        assert func(1, {"one": 1, "two": 2}) != result_a
        assert func(5, {"three": 3, "four": 4}) == result_b

    def test_kwargonly(self, simple_client):
        @simple_client.cache.memoize()
        def func(a=None):
            if a is None:
                a = 0
            return a + random.random()

        result_a = func()
        result_b = func(5)

        assert func() == result_a
        assert func() < 1
        assert func(5) == result_b
        assert func(5) >= 5
        assert func(5) < 6

    def test_arg_kwarg(self, simple_client):
        @simple_client.cache.memoize()
        def func(a, b, c=1):
            return a + b + c + random.randrange(0, 100000)

        assert func(1, 2) == func(1, 2, c=1)
        assert func(1, 2) == func(1, 2, 1)
        assert func(1, 2) == func(1, 2)
        assert func(1, 2, 3) != func(1, 2)

        with pytest.raises(TypeError):
            func(1)

    def test_classarg(self, simple_client):
        @simple_client.cache.memoize()
        def func(a):
            return a.value + random.random()

        class Adder(object):
            def __init__(self, value):
                self.value = value

        adder = Adder(15)
        adder2 = Adder(20)

        y = func(adder)
        z = func(adder2)
        assert y != z
        assert func(adder) == y
        assert func(adder) != z

        adder.value = 14
        assert func(adder) == y
        assert func(adder) != z
        assert func(adder) != func(adder2)
        assert func(adder2) == z

    def test_classfunc(self, simple_client):
        class Adder(object):
            def __init__(self, initial):
                self.initial = initial

            @simple_client.cache.memoize()
            def add(self, b):
                return self.initial + b

        adder1 = Adder(1)
        adder2 = Adder(2)

        x = adder1.add(3)
        assert adder1.add(3) == x
        assert adder1.add(4) != x
        assert adder1.add(3) != adder2.add(3)

    def test_delete_classfunc(self, simple_client):
        class Adder(object):
            def __init__(self, initial):
                self.initial = initial

            @simple_client.cache.memoize()
            def add(self, b):
                return self.initial + b + random.random()

        adder1 = Adder(1)
        adder2 = Adder(2)

        a1 = adder1.add(3)
        a2 = adder2.add(3)
        assert a1 != a2
        assert adder1.add(3) == a1
        assert adder2.add(3) == a2

        simple_client.cache.delete_memoized(adder1.add)
        a3 = adder1.add(3)
        a4 = adder2.add(3)

        assert a1 != a3
        assert a2 != a3
        assert a2 == a4

        simple_client.cache.delete_memoized(Adder.add)
        a5 = adder1.add(3)
        a6 = adder2.add(3)

        assert a3 != a5
        assert a4 != a6
        assert a5 != a6

    def test_delete_classmethod(self, simple_client):
        class Mock(object):
            @classmethod
            @simple_client.cache.memoize(5)
            def func(cls, a, b):
                return a + b + random.randrange(0, 100000)

        result = Mock.func(5, 2)
        result2 = Mock.func(5, 3)
        time.sleep(1)

        assert Mock.func(5, 2) == result
        assert Mock.func(5, 2) == result
        assert Mock.func(5, 3) != result
        assert Mock.func(5, 3) == result2

        simple_client.cache.delete_memoized(Mock.func)
        assert Mock.func(5, 2) != result
        assert Mock.func(5, 3) != result2

    def test_multiple_arg_kwarg_calls(self, simple_client):
        @simple_client.cache.memoize()
        def func(a, b, c=[1, 1], d=[1, 1]):
            rand = random.randrange(0, 100000)
            return sum(a) + sum(b) + sum(c) + sum(d) + rand

        expected = func([5, 3, 2], [1], c=[3, 3], d=[3, 3])
        assert func([5, 3, 2], [1], d=[3, 3], c=[3, 3]) == expected

        result = func(b=[1], a=[5, 3, 2], c=[3, 3], d=[3, 3])
        assert result == expected
        assert func([5, 3, 2], [1], [3, 3], [3, 3]) == expected

    def test_delete_multiple_arg_kwarg(self, simple_client):
        @simple_client.cache.memoize()
        def func(a, b, c=[1, 1], d=[1, 1]):
            rand = random.randrange(0, 100000)
            return sum(a) + sum(b) + sum(c) + sum(d) + rand

        result_a = func([5, 3, 2], [1], c=[3, 3], d=[3, 3])
        simple_client.cache.delete_memoized(func, [5, 3, 2], [1], [3, 3], [3, 3])
        result_b = func([5, 3, 2], [1], c=[3, 3], d=[3, 3])
        assert result_a != result_b

        simple_client.cache.delete_memoized(func, [5, 3, 2], b=[1], c=[3, 3], d=[3, 3])
        result_b = func([5, 3, 2], [1], c=[3, 3], d=[3, 3])
        assert result_a != result_b

        simple_client.cache.delete_memoized(func, [5, 3, 2], [1], c=[3, 3], d=[3, 3])
        result_a = func([5, 3, 2], [1], c=[3, 3], d=[3, 3])
        assert result_a != result_b

        simple_client.cache.delete_memoized(func, [5, 3, 2], b=[1], c=[3, 3], d=[3, 3])
        result_a = func([5, 3, 2], [1], c=[3, 3], d=[3, 3])
        assert result_a != result_b

        simple_client.cache.delete_memoized(func, [5, 3, 2], [1], c=[3, 3], d=[3, 3])
        result_b = func([5, 3, 2], [1], c=[3, 3], d=[3, 3])
        assert result_a != result_b

        simple_client.cache.delete_memoized(func, [5, 3, 2], [1], [3, 3], [3, 3])
        result_a = func([5, 3, 2], [1], c=[3, 3], d=[3, 3])
        assert result_a != result_b

    def test_kwargs_to_args(self, simple_client):
        def func(a, b, c=None, d=None):
            return sum(a) + sum(b) + random.randrange(0, 100000)

        expected = (1, 2, "foo", "bar")

        args = simple_client.cache._gen_args(func, 1, 2, "foo", "bar")
        assert tuple(args) == expected

        args = simple_client.cache._gen_args(func, 2, "foo", "bar", a=1)
        assert tuple(args) == expected

        args = simple_client.cache._gen_args(func, a=1, b=2, c="foo", d="bar")
        assert tuple(args) == expected

        args = simple_client.cache._gen_args(func, d="bar", b=2, a=1, c="foo")
        assert tuple(args) == expected

        args = simple_client.cache._gen_args(func, 1, 2, d="bar", c="foo")
        assert tuple(args) == expected


class TestNSCache(object):
    def test_memoize(self, namespace_client):
        def func(a, b):
            return a + b + random.randrange(0, 100000)

        cache = setup_func("simple", namespace=namespace_client.cache.namespace)
        cache_key1 = namespace_client.cache._memoize_make_cache_key()(func)
        cache_key2 = cache._memoize_make_cache_key()(func)
        assert cache_key1 == cache_key2


class TestFileSystemCache(TestCache):
    def test_dict_config(self, fs_client):
        check_cache_type(fs_client.cache, "filesystem")
        check_cache_instance(fs_client.cache, FileSystemCache)

    def test_add_bytes(self, fs_client):
        fs_client.cache.add(b"hi", "hello")
        assert fs_client.cache.get(b"hi") == "hello"

        fs_client.cache.add(b"hi", b"foobar")
        assert fs_client.cache.get(b"hi") == "hello"


if HAS_MEMCACHE:

    class TestMemcachedCache(TestCache):
        def test_dict_config(self, memcached_client):
            check_cache_type(memcached_client.cache, "memcached")
            check_cache_instance(memcached_client.cache, MemcachedCache)
            # check_client_name(memcached_client.cache, client_name)

        def test_mc_large_value(self, memcached_client):
            check_too_big(memcached_client.cache, BIGGERINT)
else:
    print("TestMemcachedCache requires Memcache")

if HAS_MEMCACHE:

    class TestSASLMemcachedCache(TestCache):
        def test_dict_config(self, saslmemcached_client):
            check_cache_type(saslmemcached_client.cache, "saslmemcached")
            check_cache_instance(saslmemcached_client.cache, SASLMemcachedCache)

        def test_mc_large_value(self, saslmemcached_client):
            check_too_big(saslmemcached_client.cache, BIGGERINT)

else:
    print("TestSASLMemcachedCache requires Memcache")

if HAS_MEMCACHE:

    class TestSpreadSASLMemcachedCache(TestCache):
        def test_dict_config(self, spreadsaslmemcached_client):
            cache_instance = SpreadSASLMemcachedCache
            check_cache_type(spreadsaslmemcached_client.cache, "spreadsaslmemcached")
            check_cache_instance(spreadsaslmemcached_client.cache, cache_instance)

        def test_mc_large_value(self, spreadsaslmemcached_client):
            check_set_delete(spreadsaslmemcached_client.cache, "big", "a", BIGINT)
            check_set_delete(spreadsaslmemcached_client.cache, "ƅıɠ", "ą", BIGINT)
            check_set_delete(spreadsaslmemcached_client.cache, b"big", b"a", BIGINT)
            check_too_big(spreadsaslmemcached_client.cache, BIGGERINT, ValueError)
else:
    print("TestSpreadSASLMemcachedCache requires Memcache")

if HAS_REDIS:

    class TestRedisCache(TestCache):
        def test_dict_config(self, redis_client):
            check_cache_type(redis_client.cache, "redis")
            check_cache_instance(redis_client.cache, RedisCache)

        def test_add_bytes(self, redis_client):
            pass

        def test_delete_bytes(self, redis_client):
            pass

        def test_redis_url_default_db(self, redis_client):
            client = redis_client.cache.cache._client
            rconn = client.connection_pool.get_connection("foo")
            assert rconn.db == 0

        def test_redis_url_custom_db(self, redis_custom_client):
            client = redis_client.cache.cache._client
            rconn = client.connection_pool.get_connection("foo")
            assert rconn.db == 2
else:
    print("TestRedisCache requires Redis")

if __name__ == "__main__":
    pytest.main()
