"""
A tiny pretend Firestore that lives in memory, for the tests.

It copies just the parts of the real Firestore library that MosshyBot uses
(collections, documents, where/order_by/limit queries), so the tests run
anywhere (your PC, GitHub) without a login, without internet, and without
ever touching your real data.
"""

import copy
import itertools
import operator

from google.api_core.exceptions import AlreadyExists

_ids = itertools.count(1)
_OPS = {"<": operator.lt, "<=": operator.le, "==": operator.eq, ">=": operator.ge, ">": operator.gt}


class Snapshot:
    def __init__(self, ref, data):
        self.reference, self.id, self._data = ref, ref.id, data

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return copy.deepcopy(self._data)


class Document:
    def __init__(self, store, doc_id):
        self._store, self.id = store, doc_id

    def get(self):
        return Snapshot(self, copy.deepcopy(self._store.get(self.id)))

    def set(self, data, merge=False):
        if merge and self.id in self._store:
            self._store[self.id].update(copy.deepcopy(data))
        else:
            self._store[self.id] = copy.deepcopy(data)

    def create(self, data):
        if self.id in self._store:
            raise AlreadyExists("document already exists")
        self.set(data)

    def update(self, changes):
        if self.id not in self._store:
            raise KeyError(f"no document {self.id}")
        self._store[self.id].update(copy.deepcopy(changes))

    def delete(self):
        self._store.pop(self.id, None)


class Query:
    def __init__(self, store, filters=(), order=None, limit=None):
        self._store, self._filters, self._order, self._limit = store, list(filters), order, limit

    def where(self, filter):
        return Query(self._store, [*self._filters, filter], self._order, self._limit)

    def order_by(self, field):
        return Query(self._store, self._filters, field, self._limit)

    def limit(self, count):
        return Query(self._store, self._filters, self._order, count)

    def stream(self):
        docs = [(doc_id, data) for doc_id, data in self._store.items()
                if all(f.field_path in data and _OPS[f.op_string](data[f.field_path], f.value)
                       for f in self._filters)]
        if self._order:
            docs = [d for d in docs if self._order in d[1]]  # like Firestore: missing field = left out
            docs.sort(key=lambda d: d[1][self._order])
        if self._limit is not None:
            docs = docs[:self._limit]
        return [Snapshot(Document(self._store, doc_id), copy.deepcopy(data)) for doc_id, data in docs]


class Collection(Query):
    def __init__(self, store, name):
        super().__init__(store)
        self.id = name

    def document(self, doc_id=None):
        return Document(self._store, doc_id or f"doc{next(_ids)}")

    def add(self, data):
        doc = self.document()
        doc.set(data)
        return None, doc


class FakeClient:
    project = "test-project"

    def __init__(self):
        self._collections = {}

    def collection(self, name):
        return Collection(self._collections.setdefault(name, {}), name)

    def collections(self):
        return [Collection(store, name) for name, store in self._collections.items()]
