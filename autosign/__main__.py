import fcntl
import logging
import signal

from waitress import create_server

from .app import create_app
from .worker import Worker


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = create_app()
    # Keep recovery and scheduling exclusive even if another container shares /data.
    service_lock = open(app.extensions["store"].path.parent / "service.lock", "a")
    try:
        fcntl.flock(service_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("同一个数据目录已有签到服务运行，请只启动一个实例。") from None
    worker = Worker(app.extensions["store"], app.config["ALLOW_PRIVATE_TARGETS"])
    app.extensions["worker"] = worker
    server = create_server(app, host="0.0.0.0", port=8080, threads=6,
                           max_request_body_size=768 * 1024, clear_untrusted_proxy_headers=True,
                           ident="AutoSign")
    worker.start()

    def stop(signum, frame):
        worker.stop_event.set()
        # Waitress.run handles SystemExit by stopping its request dispatcher.
        # Closing only the listener leaves keep-alive channels in its event loop.
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        logging.info("AutoSign is listening on port 8080.")
        server.run()
    finally:
        app.extensions["account_login"].close()
        worker.stop()
        server.close()
        service_lock.close()


if __name__ == "__main__":
    main()
