"""Case storage. One JSON document per case, plus a list of org standing rules.

Two backends behind one Protocol: LocalJsonStore for the dev box and the demo,
DynamoStore for the deployed runtime. Both serialize a CaseRecord with Pydantic so
the bytes are identical on either side.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from .schemas import CaseRecord

RULES_KEY = "__rules__"
CASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_REPLACE_ATTEMPTS = 20


def check_case_id(case_id: str) -> str:
    """A case id arrives from the API payload and becomes a file name; validate it."""
    if not isinstance(case_id, str) or not CASE_ID_PATTERN.match(case_id):
        raise ValueError(
            f"invalid case_id {case_id!r}: expected 1-80 characters of A-Z a-z 0-9 _ -"
        )
    return case_id


@runtime_checkable
class Store(Protocol):
    """What every backend must offer. The board reads list()."""

    def get(self, case_id: str) -> Optional[CaseRecord]: ...

    def put(self, case: CaseRecord) -> None: ...

    def list(self) -> list[CaseRecord]: ...

    def rules(self) -> list[dict]: ...

    def add_rule(self, rule: dict) -> None: ...


def _atomic_write(path: Path, text: str) -> None:
    """Write through a temp file unique to this writer, then swap it in.

    A shared `<name>.tmp` makes two concurrent writers collide, which on Windows raises
    from `os.replace`; `NamedTemporaryFile` gives each writer its own name.
    """
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", suffix=".tmp",
        delete=False,
    )
    try:
        with handle as fh:
            fh.write(text)
        # Windows refuses a replace while another writer is swapping the same target;
        # the conflict lasts microseconds, so retry briefly before giving up.
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(handle.name, path)  # os.rename does not overwrite on Windows
                return
            except PermissionError:
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(0.005 * (attempt + 1))
    finally:
        if os.path.exists(handle.name):
            os.unlink(handle.name)


class LocalJsonStore:
    """One `<case_id>.json` per case in `dir`, standing rules in `org_rules.json`."""

    def __init__(self, dir: str | os.PathLike[str]) -> None:
        self.dir = Path(dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _case_path(self, case_id: str) -> Path:
        return self.dir / f"{check_case_id(case_id)}.json"

    @property
    def _rules_path(self) -> Path:
        return self.dir / "org_rules.json"

    def get(self, case_id: str) -> Optional[CaseRecord]:
        path = self._case_path(case_id)
        if not path.exists():
            return None
        return CaseRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def put(self, case: CaseRecord) -> None:
        _atomic_write(self._case_path(case.case_id), case.model_dump_json(indent=2))

    def list(self) -> list[CaseRecord]:
        out: list[CaseRecord] = []
        for path in sorted(self.dir.glob("*.json")):
            if path.name == "org_rules.json":
                continue
            out.append(CaseRecord.model_validate_json(path.read_text(encoding="utf-8")))
        return out

    def rules(self) -> list[dict]:
        if not self._rules_path.exists():
            return []
        loaded = json.loads(self._rules_path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, list) else []

    def add_rule(self, rule: dict) -> None:
        current = self.rules()
        if rule not in current:
            current.append(rule)
        _atomic_write(self._rules_path, json.dumps(current, indent=2, ensure_ascii=False))


class DynamoStore:
    """Same interface on a DynamoDB table keyed by `case_id`.

    The record travels as one JSON string attribute (`body`) rather than a mapped
    item, because DynamoDB rejects floats and the engine amounts are floats.
    Standing rules live in the item whose `case_id` is `__rules__`.
    """

    def __init__(self, table_name: str, region: str = "us-east-1") -> None:
        import boto3

        self.table_name = table_name
        self.region = region
        self._ddb = boto3.resource("dynamodb", region_name=region)
        self._client = self._ddb.meta.client

    @property
    def table(self):
        return self._ddb.Table(self.table_name)

    def ensure_table(self) -> None:
        """Create the on-demand table if it is not there yet."""
        existing = self._client.list_tables().get("TableNames", [])
        if self.table_name in existing:
            return
        self._client.create_table(
            TableName=self.table_name,
            KeySchema=[{"AttributeName": "case_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "case_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        self._client.get_waiter("table_exists").wait(TableName=self.table_name)

    def get(self, case_id: str) -> Optional[CaseRecord]:
        item = self.table.get_item(Key={"case_id": check_case_id(case_id)}).get("Item")
        if not item or "body" not in item:
            return None
        return CaseRecord.model_validate_json(item["body"])

    def put(self, case: CaseRecord) -> None:
        self.table.put_item(
            Item={"case_id": check_case_id(case.case_id), "body": case.model_dump_json()}
        )

    def list(self) -> list[CaseRecord]:
        out: list[CaseRecord] = []
        kwargs: dict = {}
        while True:
            page = self.table.scan(**kwargs)
            for item in page.get("Items", []):
                if item.get("case_id") == RULES_KEY or "body" not in item:
                    continue
                out.append(CaseRecord.model_validate_json(item["body"]))
            key = page.get("LastEvaluatedKey")
            if not key:
                break
            kwargs["ExclusiveStartKey"] = key
        return out

    def rules(self) -> list[dict]:
        item = self.table.get_item(Key={"case_id": RULES_KEY}).get("Item")
        if not item or "body" not in item:
            return []
        loaded = json.loads(item["body"])
        return loaded if isinstance(loaded, list) else []

    def add_rule(self, rule: dict) -> None:
        current = self.rules()
        if rule not in current:
            current.append(rule)
        self.table.put_item(
            Item={"case_id": RULES_KEY, "body": json.dumps(current, ensure_ascii=False)}
        )


def get_store() -> Store:
    """DynamoStore when BENEFITLINE_TABLE is set, else a LocalJsonStore."""
    table = os.environ.get("BENEFITLINE_TABLE")
    if table:
        return DynamoStore(table, region=os.environ.get("AWS_REGION", "us-east-1"))
    return LocalJsonStore(os.environ.get("BENEFITLINE_DATA_DIR", "./_data"))
