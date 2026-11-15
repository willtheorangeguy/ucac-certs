"""Outbound-only local Society scanner. No inbound listener or roster files."""
from __future__ import annotations

import argparse
import logging
from logging.handlers import RotatingFileHandler
import os
from contextlib import ExitStack, contextmanager
from pathlib import Path
import time
from urllib.parse import urlsplit

import requests

from .browser import FirefoxSocietyClient
from .config import ConfigurationError, load_env_file
from .scraper import UpstreamError

logger = logging.getLogger(__name__)


def validate_base_url(value):
    target = urlsplit(value)
    if (target.scheme != "https" and not (target.scheme == "http" and target.hostname in {"127.0.0.1", "localhost"})):
        raise ConfigurationError("WORKER_BASE_URL must use HTTPS (HTTP is allowed only for localhost).")
    if not target.hostname or target.username or target.password or target.query or target.fragment:
        raise ConfigurationError("WORKER_BASE_URL must be a plain application URL.")
    return value.rstrip("/")


@contextmanager
def single_worker(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ConfigurationError("The local scanner is already running.") from None
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def perform_job(client, job):
    """Only the fixed Society endpoint is reachable through worker jobs."""
    if (set(job) != {"id", "member_code", "lease"} or type(job["id"]) is not int
            or job["id"] <= 0 or not isinstance(job["member_code"], str)
            or not job["member_code"].isalnum() or len(job["member_code"]) > 64
            or not isinstance(job["lease"], str) or not 32 <= len(job["lease"]) <= 128):
        raise ConfigurationError("The server returned an invalid scanner job.")
    return {"lease": job["lease"], "html": client.fetch_html(job["member_code"]), "error": None}


def run_worker(base_url, token, *, poll_seconds=2):
    base_url = validate_base_url(base_url)
    if len(token) < 32:
        raise ConfigurationError("LOCAL_WORKER_TOKEN must have at least 32 characters.")
    with requests.Session() as session, ExitStack() as resources:
        session.headers.update({"Authorization": "Bearer " + token})
        client = None
        last_job = 0
        logger.info("Local scanner started; waiting for work.")
        while True:
            try:
                response = session.post(base_url + "/worker/claim", timeout=15, allow_redirects=False)
                if response.status_code in {401, 403}:
                    raise ConfigurationError("Scanner credential was rejected. Reconnect the local scanner.")
                if response.status_code == 204:
                    if client is not None and time.monotonic() - last_job > 60:
                        resources.close()
                        client = None
                    time.sleep(poll_seconds)
                    continue
                if response.status_code != 200:
                    logger.warning("Scanner connection unavailable (HTTP %s).", response.status_code)
                    time.sleep(10)
                    continue
                job = response.json()
                # Validate before starting a browser or logging anything from a job.
                if not isinstance(job, dict) or "lease" not in job or "id" not in job:
                    raise ConfigurationError("The server returned an invalid scanner job.")
                try:
                    if client is None:
                        client = resources.enter_context(FirefoxSocietyClient())
                    payload = perform_job(client, job)
                except UpstreamError as exc:
                    kind = "cloudflare" if "Cloudflare" in str(exc) else "browser"
                    payload = {"lease": job["lease"], "html": None, "error": kind}
                    resources.close()
                    client = None
                    logger.warning("Society lookup failed (%s).", kind)
                last_job = time.monotonic()
                for attempt in range(3):
                    try:
                        reply = session.post(base_url + f"/worker/jobs/{job['id']}", json=payload,
                                             timeout=20, allow_redirects=False)
                        if reply.status_code in {204, 409}:
                            logger.info("Lookup returned to application (%s).", reply.status_code)
                            break
                        if reply.status_code in {401, 403}:
                            raise ConfigurationError("Scanner credential was rejected.")
                        logger.warning("Application did not accept scanner reply (HTTP %s).", reply.status_code)
                    except requests.RequestException:
                        logger.warning("Scanner reply interrupted; retrying.")
                    time.sleep(2**attempt)
            except requests.RequestException:
                # requests exceptions can include URLs. Log no headers, IDs or pages.
                logger.warning("Application connection interrupted; retrying.")
                time.sleep(10)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the outbound local certification scanner.")
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--log-file", type=Path, default=Path("data/local-worker.log"))
    args = parser.parse_args(argv)
    args.log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(args.log_file, maxBytes=1_000_000, backupCount=1, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[handler])
    try:
        load_env_file(args.env_file)
        with single_worker(args.log_file.with_suffix(".lock")):
            run_worker(os.environ.get("WORKER_BASE_URL", ""), os.environ.get("LOCAL_WORKER_TOKEN", ""))
    except ConfigurationError as exc:
        logger.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        logger.info("Local scanner stopped.")
    except Exception as exc:
        logger.error("Scanner stopped unexpectedly (%s).", type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
