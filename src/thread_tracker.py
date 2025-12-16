import threading

_thread_store = {}
_lock = threading.Lock()

def track_thread(signature, payload):
    with _lock:
        if signature not in _thread_store:
            _thread_store[signature] = []
        _thread_store[signature].append(payload)

def get_threads():
    with _lock:
        return _thread_store
