#!/usr/bin/env python
# -*- coding: utf-8 -*-
# vim: sw=4:ts=4:expandtab
"""
    mezmorize
    ~~~~~~~~~

    Adds function memoization support
"""
# pylint: disable=W1636,W1637,W1638,W1639

import base64
import hashlib
import inspect
import uuid
import warnings

from importlib import import_module
from functools import partial, wraps
from inspect import getfullargspec

from cachelib.memcached import _test_memcached_key

from . import backends
from .utils import (
    DEF_THRESHOLD, DEF_DEFAULT_TIMEOUT, ENCODING, decode, get_cache_config,
    get_cache_type)

__version__ = '0.28.2'
__copyright__ = 'Copyright 2015 Reuben Cummings'

# Used to remove control characters and whitespace from cache keys.
is_invalid = lambda c: not (c in {'_', '.'} or c.isalnum())
delchars = filter(is_invalid, map(chr, range(256)))

trans_tbl = ''.maketrans({k: None for k in delchars})
NULL_CONTROL = (trans_tbl,)
FIRST_NC = NULL_CONTROL[0]


def get_namespace(*names):
    text = '.'.join(map(decode, names))

    if FIRST_NC:
        namespace = text.translate(FIRST_NC)
    else:
        encoded = text.encode(ENCODING)
        namespace = encoded.translate(*NULL_CONTROL).decode(ENCODING)

    return namespace


def function_namespace(f, *args):
    """
    Attempts to returns unique a namespace for a function
    """
    m_args = getfullargspec(f).args
    m_arg = m_args[0] if args and m_args else ''
    arg = args[0] if args else None
    self_instance = getattr(f, '__self__', None)
    not_class = self_instance and not inspect.isclass(self_instance)
    is_self = m_arg == 'self'

    try:
        name = f.__qualname__
    except AttributeError:
        klass = self_instance.__class__ if not_class else self_instance

        if not klass:
            klass = getattr(f, 'im_class', None)

        if not klass and is_self:
            klass = arg.__class__
        elif not klass and m_arg == 'cls':
            klass = arg

        name = '.'.join(n.__name__ for n in (klass, f) if n)

    module = f.__module__
    ns = get_namespace(module, name)

    if not_class or is_self:
        instance = f.__self__ if not_class else arg
        ins = get_namespace(module, name, repr(instance))
    else:
        ins = None

    return ns, ins


class Cache(object):
    """
    This class is used to control the cache objects.
    """
    def __init__(self, namespace=None, **config):
        config.setdefault('CACHE_DEFAULT_TIMEOUT', DEF_DEFAULT_TIMEOUT)
        config.setdefault('CACHE_THRESHOLD', DEF_THRESHOLD)
        config.setdefault('CACHE_KEY_PREFIX', 'mezmorize_')
        config.setdefault('CACHE_MEMCACHED_SERVERS', None)
        config.setdefault('CACHE_DIR', None)
        config.setdefault('CACHE_OPTIONS', {})
        config.setdefault('CACHE_ARGS', [])
        config.setdefault('CACHE_TYPE', 'simple')
        config.setdefault('CACHE_NO_NULL_WARNING', False)

        warning = not config['CACHE_NO_NULL_WARNING']

        if config['CACHE_TYPE'] == 'null' and warning:
            warnings.warn(
                'CACHE_TYPE is set to null, caching is effectively disabled.')

        self.namespace = str(namespace or '')
        self.config = config
        self._set_cache()

    def _set_cache(self):
        module_string = self.config['CACHE_TYPE']
        default_timeout = self.config['CACHE_DEFAULT_TIMEOUT']

        if '.' not in module_string:
            try:
                cache_obj = getattr(backends, module_string)
            except AttributeError:
                msg = '{} is not a valid Mezmorize backend'
                raise ImportError(msg.format(module_string))
        else:
            cache_obj = import_module(module_string)

        self.cache_type = cache_obj.__name__
        self.is_memcached = 'memcache' in self.cache_type

        args = self.config['CACHE_ARGS']
        kwargs = self.config['CACHE_OPTIONS']
        kwargs.setdefault('default_timeout', default_timeout)

        if not self.is_memcached:
            kwargs.pop('preferred_memcache', None)
            kwargs.pop('connect_timeout', None)

        self.cache = cache_obj(self.config, *args, **kwargs)

        try:
            self.client_name = self.cache.client_name
        except AttributeError:
            self.client_name = None

    def _gen_mapping(self, *args):
        for key in args:
            if _test_memcached_key(key):
                encoded_key = self.cache._normalize_key(key)
                yield (encoded_key, key)

    # https://github.com/pallets/werkzeug/pull/1161
    def get_values(self, *args):
        are_strings = (isinstance(key, str) for key in args)
        has_encoded_keys = self.cache.key_prefix or not all(are_strings)
        key_mapping = dict(self._gen_mapping(*args))
        keys = list(key_mapping)
        rv = self.cache._client.get_multi(keys)

        if has_encoded_keys:
            rv = {key_mapping[key]: value for key, value in rv.items()}

        return [rv.get(key) for key in args]

    def get(self, *args, **kwargs):
        "Proxy function for internal cache object."
        return self.cache.get(*args, **kwargs)

    def set(self, *args, **kwargs):
        "Proxy function for internal cache object."
        self.cache.set(*args, **kwargs)

    def add(self, *args, **kwargs):
        "Proxy function for internal cache object."
        self.cache.add(*args, **kwargs)

    def delete(self, *args, **kwargs):
        "Proxy function for internal cache object."
        self.cache.delete(*args, **kwargs)

    def delete_many(self, *args, **kwargs):
        "Proxy function for internal cache object."
        self.cache.delete_many(*args, **kwargs)

    def clear(self):
        "Proxy function for internal cache object."
        self.cache.clear()

    def get_many(self, *args, **kwargs):
        "Proxy function for internal cache object."
        if self.is_memcached:
            values = self.get_values(*args)
        else:
            values = self.cache.get_many(*args, **kwargs)

        return values

    def set_many(self, *args, **kwargs):
        "Proxy function for internal cache object."
        self.cache.set_many(*args, **kwargs)

    def _memvname(self, funcname):
        return funcname + '_memver'

    def _memoize_make_version_hash(self):
        if self.namespace.startswith('http'):
            UUID = uuid.uuid3(uuid.NAMESPACE_URL, self.namespace)
        if self.namespace:
            UUID = uuid.uuid3(uuid.NAMESPACE_DNS, self.namespace)
        else:
            UUID = uuid.uuid4()

        return base64.b64encode(UUID.bytes)[:6].decode(ENCODING)

    def _memoize_version(self, f, *args, **kwargs):
        """
        Updates the hash version associated with a memoized function or method.
        """
        reset = kwargs.pop('reset', None)
        delete = kwargs.pop('delete', None)
        fname, instance_fname = function_namespace(f, *args)
        version_key = self._memvname(fname)

        if instance_fname:
            fetch_keys = [version_key, self._memvname(instance_fname)]
        else:
            fetch_keys = [version_key]

        # Only delete the per-instance version key or per-function version
        # key but not both.
        if delete:
            self.cache.delete_many(fetch_keys[-1])
            return fname, None

        version_data_list = list(self.get_many(*fetch_keys))
        dirty = False

        if version_data_list[0] is None:
            version_data_list[0] = self._memoize_make_version_hash()
            dirty = True

        if instance_fname and version_data_list[1] is None:
            version_data_list[1] = self._memoize_make_version_hash()
            dirty = True

        # Only reset the per-instance version or the per-function version
        # but not both.
        if reset:
            fetch_keys = fetch_keys[-1:]
            version_data_list = [self._memoize_make_version_hash()]
            dirty = True

        if dirty:
            zipped = zip(fetch_keys, version_data_list)
            self.cache.set_many(dict(zipped), **kwargs)

        return fname, ''.join(map(decode, version_data_list))

    def _memoize_make_cache_key(self, make_name=None, decorated=None):
        """
        Function used to create the cache_key for memoized functions.
        """
        def make_cache_key(f, *args, **kwargs):
            mkwargs = {'timeout': decorated.cache_timeout} if decorated else {}
            fname, version_data = self._memoize_version(f, *args, **mkwargs)

            # this should have to be after version_data, so that it
            # does not break the delete_memoized functionality.
            altfname = make_name(fname) if callable(make_name) else fname

            if callable(f):
                keyargs = tuple(self._gen_args(f, *args, **kwargs))
                keykwargs = {}
            else:
                keyargs, keykwargs = args, kwargs

            updated = '{0}{1}{2}'.format(altfname, keyargs, keykwargs)
            cache_key = hashlib.md5()
            cache_key.update(updated.encode(ENCODING))
            cache_key = base64.b64encode(cache_key.digest())[:16]
            cache_key = cache_key.decode(ENCODING)
            cache_key += version_data

            return cache_key
        return make_cache_key

    def _gen_args(self, f, *args, **kwargs):
        # Inspect the arguments to the function
        # This allows the memoization to be the same
        # whether the function was called with
        # 1, b=2 is equivalent to a=1, b=2, etc.
        num_args = len(args)
        argspec = getfullargspec(f)
        _defaults = argspec.defaults or []
        m_args = argspec.args
        defaults = dict(zip(reversed(m_args), reversed(_defaults)))
        counter = 0

        for i, m_arg in enumerate(m_args):
            # Subtract from i, m_args that aren't in args
            arg_num = i - counter

            if not i and m_arg in ('self', 'cls'):
                # supports instance methods for the memoized functions
                new_arg = repr(args[0])
            elif kwargs.get(m_arg) is not None:
                new_arg = kwargs[m_arg]
                counter += 1
            elif arg_num < num_args:
                new_arg = args[arg_num]
            elif defaults.get(m_arg) is not None:
                new_arg = defaults[m_arg]
            else:
                new_arg = None

            yield new_arg

    def memoize(self, timeout=None, make_name=None, unless=None):
        """
        Use this to cache the result of a function, taking its arguments into
        account in the cache key.

        Information on
        `Memoization <http://en.wikipedia.org/wiki/Memoization>`_.

        Example::
            >>> import random
            >>>
            >>> cache = Cache()
            >>>
            >>> @cache.memoize(timeout=50)
            ... def big_foo(a, b):
            ...     return a + b + random.random()

        .. code-block:: python

            >>> rand_res = big_foo(5, 2)
            >>> rand_res == big_foo(5, 3)
            False
            >>> rand_res == big_foo(5, 2)
            True

        .. versionadded:: 0.4
            The returned decorated function now has three function attributes
            assigned to it.

                **uncached**
                    The original undecorated function. readable only

                **cache_timeout**
                    The cache timeout value for this function. For a custom
                    value to take affect, this must be set before the function
                    is called.

                    readable and writable

                **make_cache_key**
                    A function used in generating the cache_key used.

                    readable and writable


        :param timeout: Default None. If set to an integer, will cache for that
                        amount of time. Unit of time is in seconds.
        :param make_name: Default None. If set this is a function that accepts
                          a single argument, the function name, and returns a
                          new string to be used as the function name. If not
                          set then the function name is used.
        :param unless: Default None. Cache will *always* execute the caching
                       facilities unless this callable is true.
                       This will bypass the caching entirely.

        .. versionadded:: 0.5
            params ``make_name``, ``unless``
        """

        def _memoize(f):
            @wraps(f)
            def decorated(*args, **kwargs):
                if callable(unless) and unless():  # bypass cache
                    return f(*args, **kwargs)

                cache_key = decorated.make_cache_key(f, *args, **kwargs)
                value = self.cache.get(cache_key)

                if value is None:
                    value = f(*args, **kwargs)
                    ckwargs = {'timeout': decorated.cache_timeout}

                    # value is first for addCallback compatibility
                    def set_cache(value, key):
                        self.cache.set(key, value, **ckwargs)
                        return value

                    try:
                        value.addCallback(set_cache, cache_key)
                    except AttributeError:
                        set_cache(value, cache_key)

                return value

            decorated.uncached = f
            decorated.cache_timeout = timeout
            m_make_cache_key = self._memoize_make_cache_key
            decorated.make_cache_key = m_make_cache_key(make_name, decorated)
            decorated.delete_memoized = partial(self.delete_memoized, f)
            return decorated

        return _memoize

    def delete_memoized(self, f, *args, **kwargs):
        """
        Deletes the specified functions caches, based by given parameters.
        If parameters are given, only the functions that were memoized with
        them will be erased. Otherwise all versions of the caches will be
        forgotten.

        Example::
            >>> import random
            >>>
            >>> cache = Cache()
            >>>
            >>> @cache.memoize(50)
            ... def random_func():
            ...    return random.random()

            >>> @cache.memoize()
            ... def param_func(a, b):
            ...    return a + b + random.random()

        .. code-block:: python

            >>> rand_res = random_func()
            >>> rand_res == random_func()
            True
            >>> cache.delete_memoized(random_func)
            >>> rand_res == random_func()
            False
            >>> param_res = param_func(1, 2)
            >>> param_res == param_func(1, 2)
            True
            >>> param_res2 = param_func(2, 2)
            >>> param_res == param_res2
            False
            >>> cache.delete_memoized(param_func, 1, 2)
            >>> param_res == param_func(1, 2)
            False
            >>> param_res2 == param_func(2, 2)
            True

        Delete memoized is also smart about instance methods vs class methods.

        When passing an instancemethod, it will only clear the cache related
        to that instance of that object. (object uniqueness can be overridden
            by defining the __repr__ method, such as user id).

        When passing a classmethod, it will clear all caches related across
        all instances of that class.

        Example::

            >>> class Adder(object):
            ...    @cache.memoize()
            ...    def add(self, b):
            ...        return b + random.random()

        .. code-block:: python

            >>> adder1 = Adder()
            >>> adder2 = Adder()
            >>> adder1_res = adder1.add(3)
            >>> adder2_res = adder2.add(3)
            >>> cache.delete_memoized(adder1.add)
            >>> adder1_res == adder1.add(3)
            False
            >>> adder1_res = adder1.add(3)
            >>> adder2_res == adder2.add(3)
            True
            >>> cache.delete_memoized(Adder.add)
            >>> adder1_res == adder1.add(3)
            False
            >>> adder2_res == adder2.add(3)
            False

        :param fname: Name of the memoized function, or a reference to
            the function.
        :param *args: A list of positional parameters used with memoized
            function.
        :param **kwargs: A dict of named parameters used with memoized
            function.

        .. note::

            Flask-Cache uses inspect to order kwargs into positional args when
            the function is memoized. If you pass a function reference into
            ``fname`` instead of the function name, Flask-Cache will be able to
            place the args/kwargs in the proper order, and delete the
            positional cache.

            However, if ``delete_memoized`` is just called with the name of the
            function, be sure to pass in potential arguments in the same order
            as defined in your function as args only, otherwise Flask-Cache
            will not be able to compute the same cache key.

        .. note::

            Flask-Cache maintains an internal random version hash for the
            function. Using delete_memoized will only swap out the version
            hash, causing the memoize function to recompute results and put
            them into another key.

            This leaves any computed caches for this memoized function within
            the caching backend.

            It is recommended to use a very high timeout with memoize if using
            this function, so that when the version has been swapped, the old
            cached results would eventually be reclaimed by the caching
            backend.
        """
        if not callable(f):
            raise DeprecationWarning(
                "Deleting messages by relative name is no longer"
                " reliable, please switch to a function reference")

        if not (args or kwargs):
            self._memoize_version(f, reset=True)
        else:
            cache_key = f.make_cache_key(f.uncached, *args, **kwargs)
            self.cache.delete(cache_key)

    def delete_memoized_verhash(self, f, *args):
        """
        Delete the version hash associated with the function.

        ..warning::

            Performing this operation could leave keys behind that have
            been created with this version hash. It is up to the application
            to make sure that all keys that may have been created with this
            version hash at least have timeouts so they will not sit orphaned
            in the cache backend.
        """
        if not callable(f):
            raise DeprecationWarning(
                "Deleting messages by relative name is no longer"
                " reliable, please use a function reference")

        self._memoize_version(f, delete=True)


def get_cache(*args, **kwargs):
    """Helper function to intelligently configure an appropriate cache

    Kwargs:
        cache_type (str): The type of cache backend to use. Default depends on
            installed libraries and running servers.

        spread (bool): Use spreadsaslmemcached if available. Default False.
        preferred_memcache (str): Use this memcached client if available.
            Default None.

        cache_threshold (int): The max number of keys to store.
        cache_options (dict): Passed as kwargs to the cache backend client.
        connect_timeout (int): Max number of seconds to wait for response.
            Default None, e.g., forever.

        cache_default_timeout (int): Number of seconds to store cache result if
            `timeout` is not set.

        self_signed_certs (bool): Allow self-signed certificates.

    Returns:
        decorator: an iterator of items

    Example:
        >>> from os import getenv
        >>> from .utils import HAS_MEMCACHE, HAS_REDIS
        >>>
        >>> cache = get_cache()
        >>> cache.set('key', 'value')
        >>> cache.get('key') == 'value'
        True
        >>> cache.delete('key')
        >>> cache.get('key') is None
        True
        >>>
        >>> if cache.client_name and HAS_MEMCACHE:
        ...     cache.cache_type == 'memcached'
        ...     cache.client_name == 'pylibmc'
        ... elif HAS_REDIS:
        ...     cache.cache_type == 'redis'
        ...     cache.client_name is None
        ... else:
        ...     cache.cache_type == 'filesystem'
        ...     cache.client_name is None
        True
        True
        >>>
        >>> cache = get_cache(cache_type='simple')
        >>> cache.cache_type
        'simple'
        >>>
        >>> cache = get_cache(cache_type='filesystem')
        >>> cache.cache_type
        'filesystem'
        >>>
        >>> cache = get_cache(preferred_memcache='bmemcached')
        >>>
        >>> if cache.client_name:
        ...     cache.client_name == 'bmemcached'
        ... else:
        ...     cache.client_name is None
        True
    """
    _cache_type = kwargs.get('cache_type')
    spread = kwargs.get('spread')
    namespace = kwargs.get('namespace')
    whitelist = {
        'cache_default_timeout', 'cache_threshold', 'cache_options',
        'cache_key_prefix', 'self_signed_certs'}

    ckwargs = {k.upper(): v for k, v in kwargs.items() if k in whitelist}

    keys = {'preferred_memcache', 'connect_timeout'}
    extra_options = {k: v for k, v in kwargs.items() if k in keys}

    if extra_options:
        CACHE_OPTIONS = ckwargs.get('CACHE_OPTIONS', {})
        CACHE_OPTIONS.update(extra_options)
        ckwargs['CACHE_OPTIONS'] = CACHE_OPTIONS

    cache_type = get_cache_type(cache=_cache_type, spread=spread)
    config = get_cache_config(cache_type, **ckwargs)
    return Cache(namespace=namespace, **config)


def memoize(*args, **kwargs):
    """Use this to cache the result of a function, taking its arguments into
    account in the cache key.

    `Memoization <http://en.wikipedia.org/wiki/Memoization>`_.

    Kwargs:
        timeout (int): Number of seconds to store cache result. Default None,
            e.g., forever. Note: If this is not set, it is overridden by
            `cache_default_timeout`.

        unless (func): Don't use cache if this callable is true. Default None.

    Returns:
        decorator: a memoizer

    Example:
        >>> import random
        >>> from os import getenv
        >>> from .utils import HAS_MEMCACHE, HAS_REDIS
        >>>
        >>> get_rand = lambda: random.random()
        >>> rand_value = get_rand()
        >>> rand_value == get_rand()
        False
        >>> memoizer = memoize(timeout=30)
        >>> memoized_get_rand = memoizer(get_rand)
        >>> memoized_rand_value = memoized_get_rand()
        >>> memoized_rand_value == memoized_get_rand()
        True
        >>>
        >>> if memoizer.client_name and HAS_MEMCACHE:
        ...     memoizer.cache_type == 'memcached'
        ...     memoizer.client_name == 'pylibmc'
        ... elif HAS_REDIS:
        ...     memoizer.cache_type == 'redis'
        ...     memoizer.client_name is None
        ... else:
        ...     memoizer.cache_type == 'filesystem'
        ...     memoizer.client_name is None
        True
        True
        >>>
        >>> memoizer = memoize(cache_type='simple')
        >>> memoizer.cache_type
        'simple'
        >>>
        >>> memoizer = memoize(cache_type='filesystem')
        >>> memoizer.cache_type
        'filesystem'
        >>>
        >>> memoizer = memoize(preferred_memcache='bmemcached')
        >>>
        >>> if memoizer.client_name:
        ...     memoizer.client_name == 'bmemcached'
        ... else:
        ...     memoizer.client_name is None
        True
    """
    cache = get_cache(*args, **kwargs)
    mkwargs = {k: v for k, v in kwargs.items() if k in {'timeout', 'unless'}}
    memoizer = cache.memoize(*args, **mkwargs)
    memoizer.cache_type = cache.cache_type
    memoizer.client_name = cache.client_name
    return memoizer
