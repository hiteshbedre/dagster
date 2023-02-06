import logging
import os
import sys
from contextlib import contextmanager

from airflow import __version__ as airflow_version
from airflow.models.connection import Connection
from airflow.models.dagbag import DagBag
from airflow.settings import LOG_FORMAT
from dagster import (
    _check as check,
)
from dagster._core.definitions.utils import VALID_NAME_REGEX
from packaging import version


def is_airflow_2_loaded_in_environment() -> bool:
    return version.parse(str(airflow_version)) >= version.parse("2.0.0")


# pylint: disable=no-name-in-module,import-error
if is_airflow_2_loaded_in_environment():
    from airflow.utils.session import create_session
else:
    from airflow.utils.db import create_session  # type: ignore  # (airflow 1 compat)
# pylint: enable=no-name-in-module,import-error


def contains_duplicate_task_names(dag_bag: DagBag):
    check.inst_param(dag_bag, "dag_bag", DagBag)
    seen_task_names = set()

    # To enforce predictable iteration order
    sorted_dag_ids = sorted(dag_bag.dag_ids)
    for dag_id in sorted_dag_ids:
        dag = dag_bag.dags.get(dag_id)
        if not dag:
            continue
        for task in dag.tasks:
            if task.task_id in seen_task_names:
                return True
            else:
                seen_task_names.add(task.task_id)
    return False


class DagsterAirflowError(Exception):
    pass


def create_airflow_connections(connections):
    with create_session() as session:
        for connection in connections:
            if session.query(Connection).filter(Connection.conn_id == connection.conn_id).first():
                logging.info(
                    f"Could not import connection {connection.conn_id}: connection already exists."
                )
                continue

            session.add(connection)
            session.commit()
            logging.info(f"Imported connection {connection.conn_id}")


# Airflow DAG ids and Task ids allow a larger valid character set (alphanumeric characters,
# dashes, dots and underscores) than Dagster's naming conventions (alphanumeric characters,
# underscores), so Dagster will strip invalid characters and replace with '_'
def normalized_name(name, unique_id=None):
    base_name = "airflow_" + "".join(c if VALID_NAME_REGEX.match(c) else "_" for c in name)
    if not unique_id:
        return base_name
    else:
        return base_name + "_" + str(unique_id)


@contextmanager
def replace_airflow_logger_handlers():
    prev_airflow_handlers = logging.getLogger("airflow.task").handlers
    try:
        # Redirect airflow handlers to stdout / compute logs
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root = logging.getLogger("airflow.task")
        root.handlers = [handler]
        yield
    finally:
        # Restore previous log handlers
        logging.getLogger("airflow.task").handlers = prev_airflow_handlers


def serialize_connections(connections):
    serialized_connections = []
    for c in connections:
        serialized_connection = {
            "conn_id": c.conn_id,
            "conn_type": c.conn_type,
        }
        if hasattr(c, "login") and c.login:
            serialized_connection["login"] = c.login
        if hasattr(c, "password") and c.password:
            serialized_connection["password"] = c.password
        if hasattr(c, "host") and c.host:
            serialized_connection["host"] = c.host
        if hasattr(c, "schema") and c.schema:
            serialized_connection["schema"] = c.schema
        if hasattr(c, "port") and c.port:
            serialized_connection["port"] = c.port
        if hasattr(c, "extra") and c.extra:
            serialized_connection["extra"] = c.extra
        if hasattr(c, "description") and c.description:
            serialized_connection["description"] = c.description
        serialized_connections.append(serialized_connection)
    return serialized_connections


if os.name == "nt":
    import msvcrt  # pylint: disable=import-error

    def portable_lock(fp):
        fp.seek(0)
        msvcrt.locking(fp.fileno(), msvcrt.LK_LOCK, 1)

    def portable_unlock(fp):
        fp.seek(0)
        msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def portable_lock(fp):
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX)

    def portable_unlock(fp):
        fcntl.flock(fp.fileno(), fcntl.LOCK_UN)


class Locker:
    def __init__(self, lock_file_path="."):
        self.lock_file_path = lock_file_path
        self.fp = None

    def __enter__(self):
        self.fp = open(f"{self.lock_file_path}/lockfile.lck", "w+", encoding="utf-8")
        portable_lock(self.fp)

    def __exit__(self, _type, value, tb):
        portable_unlock(self.fp)
        self.fp.close() if self.fp else None
