'''
IFCB connection lifecycle.

           START
             |
             v
    +--> CONNECTING -- failure -----------------------------+
    |        |                                              |
    |        | connect() returns                            |
    |        v                                              |
    |    REGISTERING -- 10s timeout / close / error --------+
    |        |                                              |
    |        | startedAsClient                              |
    |        v                                              |
    |      READY <----- heartbeat ----+                     |
    |        |                        |                     |
    |        +------------------------+                     |
    |        |                                              |
    |        +-- 60s without heartbeat / close / error -----+
    |                                                       |
    |                                                       v
    |    STOPPED <---- stopping / retries disabled -- RETIRE / CLEANUP
    |                                                       |
    |                                             retries enabled
    |                                                       v
    +----------------------------------------------------BACKOFF
'''

import logging
import threading
import time

from collections import deque
from concurrent.futures import Future


# The IFCB server broadcasts a heartbeat every 20 seconds
HEARTBEAT_TIMEOUT = 60.0
REGISTRATION_TIMEOUT = 10.0
RETRY_INTERVAL = 5.0
MAX_RETRY_INTERVAL = 60.0


# Own connection attempts; the condition protects state, never I/O or callbacks
class ConnectionManager:
    def __init__(self, owner, clock=time.monotonic):
        self.owner = owner
        self.clock = clock
        self.condition = threading.Condition(threading.RLock())
        self.callback_context = threading.local()
        self.events = deque()
        self.dispatcher = None
        self.worker = None
        self.client = None
        self.ready = False
        self.active = False
        self.stopping = True
        self.last_heartbeat = 0.0
        self.ever_ready = False
        self.retry = RETRY_INTERVAL

    def start(self, background=False):
        with self.condition:

            # Never overlap workers, including an unfinished shutdown
            if self.worker is not None and self.worker.is_alive():
                if self.stopping:
                    raise RuntimeError('Previous IFCB connection is stopping')
                return
            self.stopping = False
            self.ever_ready = False
            self.retry = RETRY_INTERVAL
            startup = Future()
            worker = threading.Thread(
                target=self.run, args=(background, startup), daemon=True
            )
            self.worker = worker
            worker.start()

        # A failed caller must never close a subsequently started worker
        if not background:
            try:
                startup.result()
            except Exception:
                worker.join(timeout=5)
                raise

    # Queue observers in state-transition order and bind them to their attempt
    def dispatch(self, callback, *args, client=None, active=False):
        with self.condition:
            self.events.append((callback, args, client, active))
            if self.dispatcher is None:
                self.dispatcher = threading.Thread(
                    target=self._dispatch, daemon=True
                )
                self.dispatcher.start()
            self.condition.notify_all()

    def _dispatch(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.events or self.stopping)
                if not self.events:
                    self.dispatcher = None
                    return
                callback, args, client, active = self.events.popleft()
                if active and not self.is_current(client):
                    continue

            # Run user code on one dispatcher, without the lifecycle lock
            self.callback_context.client = client
            try:
                callback(*args)
            except Exception:
                logging.getLogger(__name__).exception('IFCB callback failed')
            finally:
                self.callback_context.client = None

    def is_current(self, client):
        with self.condition:
            return self.active and self.client is client

    def _disconnect(self, *args):

        # Retire callbacks and wake waiters before scheduling notifications
        was_active = self.active
        was_ready = self.ready
        self.active = False
        self.ready = False
        self.condition.notify_all()
        if was_ready:
            self.owner._notify(self.client, 'ready', False)
        if was_active:
            self.owner._notify(self.client, 'close', *args)

    def _failed(self, error):
        if self.active:
            if self.client is not None:
                self.failed(self.client, error)
            else:
                self._disconnect()
                self.owner._notify(None, 'error', error)

    # Report failure once per attempt, even when closure preceded the exception
    def failed(self, client, error):
        with self.condition:
            if client.failure is not None:
                return
            client.failure = error
            if self.is_current(client):
                self._disconnect()
            self.owner._notify(client, 'error', error)

    def _registered(self, response):
        if self.ready:
            return

        # Registration grants readiness and a grace period for the heartbeat
        reconnected = self.ever_ready
        self.last_heartbeat = self.clock()
        self.ready = True
        self.ever_ready = True
        self.retry = RETRY_INTERVAL
        self.condition.notify_all()
        self.owner._notify(self.client, 'ready', True)
        self.owner._notify(self.client, 'started', response)
        if reconnected:
            self.owner._notify(self.client, 'reconnect')

    def _heartbeat(self, response):
        self.last_heartbeat = self.clock()
        self.owner._notify(self.client, 'heartbeat', response)

    # Guard internal bookkeeping callbacks, which only change state or enqueue
    def _guard(self, client, callback):
        def guarded(*args, **kwargs):
            with self.condition:
                if self.is_current(client):
                    return callback(*args, **kwargs)

        return guarded

    def send(self, command, timeout=5.0):
        with self.condition:

            # Callback sends stay bound to their originating connection
            client = getattr(self.callback_context, 'client', None)
            if client is not None:
                if not self.is_current(client):
                    raise ConnectionError('IFCB connection has been retired')
            else:
                self.condition.wait_for(
                    lambda: self.ready or self.stopping, timeout
                )
                if not self.ready or self.stopping:
                    raise ConnectionError('IFCB is not registered')
                client = self.client

        # Socket I/O must not prevent the worker from retiring and stopping it
        try:
            client.relay_message_to_host(command)
        except Exception as error:
            self.failed(client, error)
            raise
        with self.condition:
            if not self.is_current(client):
                raise ConnectionError('IFCB connection closed during send')

    def close(self):
        with self.condition:
            self.stopping = True
            self._disconnect()
            worker = self.worker

        # The worker owns transport cleanup and never waits for user callbacks
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=5)

    def run(self, background, startup):
        try:
            self._run(background, startup)
        finally:
            with self.condition:
                self.stopping = True
                if not startup.done():
                    startup.set_exception(
                        ConnectionError('IFCB client stopped')
                    )
                self.condition.notify_all()

    def _run(self, background, startup):
        while True:

            # Begin a new connection attempt unless shutdown was requested
            with self.condition:
                if self.stopping:
                    return
                self.active = True
                self.owner._notify(None, 'connecting')

            # Attach internal callbacks before opening the new transport
            client = None
            try:
                client = self.owner._make_connection()
                with self.condition:
                    self.client = client
                    if self.stopping:
                        return
                client.connect()

                with self.condition:
                    if not startup.done():
                        startup.set_result(None)

                    # An open transport still needs IFCB registration
                    self.condition.wait_for(
                        lambda: self.ready or not self.active,
                        REGISTRATION_TIMEOUT,
                    )
                    if self.active and not self.ready:
                        self._failed(TimeoutError(
                            'IFCB registration timed out'
                        ))

                    # Expiry clears readiness before considering a retry
                    while self.active:
                        if (self.clock() - self.last_heartbeat
                                >= HEARTBEAT_TIMEOUT):
                            self._failed(TimeoutError(
                                'IFCB heartbeat timed out'
                            ))
                        if not self.ready:
                            break
                        self.condition.wait(timeout=1.0)
            except Exception as error:
                with self.condition:
                    if not startup.done():
                        if not background:
                            self.stopping = True
                        startup.set_exception(error)
                    self._failed(error)
            finally:

                # Cleanup must run outside the lock used by reader callbacks
                with self.condition:
                    self._disconnect()
                if client is not None:
                    try:
                        client.stop()
                    except Exception:
                        logging.getLogger(__name__).exception(
                            'IFCB cleanup failed'
                        )
                with self.condition:
                    self.client = None

            # The same lifecycle handles one-shot and automatically retried use
            with self.condition:
                if self.stopping or not self.owner.auto_reconnect:
                    return
                self.owner._notify(client, 'retry', self.retry)
                if self.condition.wait_for(lambda: self.stopping, self.retry):
                    return
                self.retry = min(self.retry * 1.5, MAX_RETRY_INTERVAL)
