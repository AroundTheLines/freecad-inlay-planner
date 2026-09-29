"""Packed, temporary-file-backed motion with streaming JSON-list compatibility.

The worker keeps forty bytes per commanded point instead of a retained Python
dictionary. Consumers iterate the same six fields; slices share packed storage.
The ordinary worker JSON writer and canonical hash stream records one at a time.
No temporary path is part of the document or exported planning result.
"""
from __future__ import annotations

import itertools
import mmap
import struct
import tempfile

_ROW = struct.Struct('<4dBBB5x')
_NUMBERS = ('x', 'y', 'z', 'feed_mm_min')
_KINDS = ('feed', 'rapid')
_ROLES = ('cut', 'plunge', 'travel')
_FIELDS = frozenset((*_NUMBERS, 'kind', 'role'))


class _MotionRecord(dict):
    def __init__(self, owner, index, values):
        dict.__init__(self, values)
        self._owner, self._index = owner, index

    def __setitem__(self, key, value):
        if key not in _FIELDS:
            raise KeyError('Packed motions contain only the six contract fields')
        changed = dict(self)
        changed[key] = value
        self._owner._write(self._index, changed)
        dict.__setitem__(self, key, value)

    def update(self, *args, **kwargs):
        changed = dict(self)
        changed.update(*args, **kwargs)
        self._owner._write(self._index, changed)
        dict.update(self, changed)

    def __ior__(self, other):
        self.update(other)
        return self

    def _cannot_remove(self, *args):
        raise TypeError('Packed motion fields cannot be removed')

    __delitem__ = clear = pop = popitem = _cannot_remove

    def __deepcopy__(self, memo):
        return dict(self)


class _MotionView(list):
    """A list-compatible read/slice view without an underlying object list."""
    def __init__(self, owner, indices):
        self._store, self._indices = owner, indices

    @property
    def _owner(self):
        return self if self._store is None else self._store

    def __len__(self):
        return len(self._indices)

    def __iter__(self):
        owner = self._owner
        for index in self._indices:
            yield owner._read(index)

    def __reversed__(self):
        owner = self._owner
        for index in reversed(self._indices):
            yield owner._read(index)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return _MotionView(self._owner, self._indices[index])
        return self._owner._read(self._indices[index])

    def __setitem__(self, index, value):
        if isinstance(index, slice):
            raise TypeError('Packed motion slices cannot change length')
        self._owner._write(self._indices[index], value)

    def __contains__(self, value):
        return any(record == value for record in self)

    def __eq__(self, other):
        if not isinstance(other, (list, tuple)):
            return False
        marker = object()
        return all(a == b for a, b in itertools.zip_longest(self, other, fillvalue=marker))

    def __ne__(self, other):
        return not self == other

    def __repr__(self):
        return f'<{type(self).__name__}: {len(self)} packed motions>'

    def copy(self):
        return [dict(record) for record in self]

    def __deepcopy__(self, memo):
        result = self.copy()
        memo[id(self)] = result
        return result

    def _fixed_view(self, *args, **kwargs):
        raise TypeError('A packed motion view cannot change length')

    append = extend = insert = pop = remove = clear = reverse = sort = __delitem__ = _fixed_view


class PackedMoves(_MotionView):
    """Appendable worker storage, grown in bounded mapped-file blocks."""
    def __init__(self):
        super().__init__(None, range(0))
        self._file, self._map, self._capacity = None, None, 0
        try:
            self._file = tempfile.TemporaryFile(prefix='freecad-inlay-motion-')
        except OSError as exc:
            raise MemoryError('Temporary motion storage is unavailable: '+str(exc)) from exc

    def _reserve(self, count):
        if count <= self._capacity:
            return
        capacity = max(4096, self._capacity*2, count)
        try:
            if self._map is not None:
                self._map.close()
                self._map = None
            self._file.truncate(capacity*_ROW.size)
            self._map = mmap.mmap(self._file.fileno(), capacity*_ROW.size)
            self._capacity = capacity
        except (OSError, ValueError) as exc:
            raise MemoryError('Temporary motion storage could not grow: '+str(exc)) from exc

    def _write(self, index, record):
        if set(record) != _FIELDS:
            raise ValueError('A packed motion needs exactly the six contract fields')
        values = [record[name] for name in _NUMBERS]
        integers = sum(1 << i for i, value in enumerate(values) if isinstance(value, int))
        _ROW.pack_into(self._map, index*_ROW.size, *values,
                       _KINDS.index(record['kind']), _ROLES.index(record['role']), integers)

    def _read(self, index):
        values = _ROW.unpack_from(self._map, index*_ROW.size)
        record = {name: int(values[i]) if values[6] & (1 << i) else values[i]
                  for i, name in enumerate(_NUMBERS)}
        record.update(kind=_KINDS[values[4]], role=_ROLES[values[5]])
        return _MotionRecord(self, index, record)

    def append(self, record):
        size = len(self)
        self._reserve(size+1)
        self._write(size, record)
        self._indices = range(size+1)

    def extend(self, records):
        for record in records:
            self.append(record)

    @property
    def storage_bytes(self):
        return self._capacity*_ROW.size

    def close(self):
        if self._map is not None:
            self._map.close()
            self._map = None
        if self._file is not None:
            self._file.close()
            self._file = None

    def __del__(self):
        self.close()
