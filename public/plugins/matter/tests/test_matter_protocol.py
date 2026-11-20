"""Tests for the messages between plugin and helper (protocol.py) and the encrypted storage (storage.py).

    python public/plugins/matter/tests/test_matter_protocol.py [path-to-comfyos]

They run without ComfyOS and without Node. Covered:
    1  every message type of the helper is parsed into a typed object
    2  everything the helper sends is foreign input: wrong types, ranges, lengths and junk are refused,
       unknown fields are dropped, error texts never repeat the input
    3  messages to the helper: start, endpoints, update, replies, pairing codes typed by a user
    4  storage: encrypted on disk, wrong key and damaged file are refused, atomic write, size limit
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "public" / "sdk"))
sys.path.insert(0, str(ROOT / "public" / "plugins"))

from matter import protocol, storage  # noqa: E402

QR_ROWS = ["101", "010", "111"]
PAIRING = {"type": "pairing", "manual": "34970112332", "qr": "MT:Y.K90AFN00KA0648G00", "rows": QR_ROWS, "commissioned": False}
NODE = {"type": "node", "node": "peer2", "online": True, "name": "Bridge", "vendor": "Signify", "product": "Hue Bridge", "serial": "s1",
        "endpoints": [{"endpoint": 3, "types": [257, 19], "name": "Stehlampe", "reachable": True,
                       "state": {"on": True, "level": 127, "unknown_key": "dropped", "mireds": None}}]}


def refused(raw: object) -> str:
    """Parse a message that must be refused and return the error text."""
    try:
        protocol.parse(raw)
    except protocol.ProtocolError as error:
        return str(error)
    raise AssertionError(f"accepted: {str(raw)[:80]}")


# ---------------------------------------------------------------------------
# 1) what the helper reports
# ---------------------------------------------------------------------------
assert protocol.parse({"type": "ready", "protocol": 1}) == protocol.Ready(1)
assert protocol.parse({"type": "started"}) == protocol.Started()
ops = protocol.parse({"type": "storage", "ops": [["bridge", "set", "root.parts", "__number__", "7"], ["bridge", "delete", "root", "old", None],
                                                 ["controller", "clear", "nodes.peer2", None, None]]})
assert [(op.namespace, op.action, op.context, op.key, op.value) for op in ops.ops] == [
    ("bridge", "set", "root.parts", "__number__", "7"), ("bridge", "delete", "root", "old", None), ("controller", "clear", "nodes.peer2", None, None)]
pairing = protocol.parse(PAIRING)
assert (pairing.manual, pairing.qr, pairing.rows, pairing.commissioned) == ("34970112332", "MT:Y.K90AFN00KA0648G00", QR_ROWS, False)
fabrics = protocol.parse({"type": "fabrics", "fabrics": [{"index": 1, "label": " Alexa  Küche ", "vendor": 4631, "extra": 1}]})
assert fabrics.fabrics == [protocol.Fabric(1, "Alexa Küche", 4631)]
command = protocol.parse({"type": "command", "id": 5, "number": 12, "state": {"level": 50, "on": True}, "fabric": 1})
assert command == protocol.Command(5, 12, {"on": True, "level": 50}, 1)
assert protocol.parse({"type": "command", "id": 6, "number": 12, "state": {"cover": "stop"}, "fabric": None}).fabric is None
node = protocol.parse(NODE)
assert node.node == "peer2" and node.online and node.endpoints[0].types == [257, 19]
assert node.endpoints[0].state == {"on": True, "level": 127}, "unknown keys and nulls are dropped"
assert protocol.parse({"type": "node_removed", "node": "peer2"}) == protocol.NodeRemoved("peer2")
result = protocol.parse({"type": "result", "id": 3, "ok": True, "node": "peer4", "attestation": ["warning:DclServiceUnavailable"]})
assert result.ok and result.node == "peer4" and result.attestation == ["warning:DclServiceUnavailable"] and result.clean
assert protocol.parse({"type": "result", "id": 4, "ok": False, "error": "ReceivedFailure"}).error == "ReceivedFailure"
assert protocol.parse({"type": "result", "id": 4, "ok": True, "clean": False}).clean is False
assert protocol.parse({"type": "error", "code": "start_failed"}) == protocol.HelperProblem("start_failed")
print("1 every message of the helper becomes a typed object; unknown fields and nulls are dropped")

# ---------------------------------------------------------------------------
# 2) foreign input
# ---------------------------------------------------------------------------
SECRET = "geheimer-inhalt"
for junk in (None, 7, "text", [], {}, {"type": "gibtsnicht"}, {"type": ["ready"]}, {"type": "ready"}, {"type": "ready", "protocol": "1"},
             {"type": "ready", "protocol": True}, {"type": "error", "code": "mit leerzeichen und <html>"}, {"type": "error", "code": "x" * 500},
             {"type": "node_removed", "node": "../../etc"}, {"type": "node_removed", "node": 5},
             {"type": "result", "id": -1, "ok": True}, {"type": "result", "id": 1, "ok": "ja"}, {"type": "result", "id": 1, "ok": False, "error": SECRET + " <b>"},
             {"type": "result", "id": 1, "ok": True, "attestation": ["a b"]}, {"type": "result", "id": 1, "ok": True, "attestation": ["x"] * 11},
             {"type": "result", "id": 1, "ok": True, "node": "a/b"}):
    refused(junk)
for state in ({"level": 300}, {"level": -1}, {"level": "50"}, {"level": True}, {"on": 1}, {"on": "true"}, {"mireds": 0}, {"x": 70000},
              {"position": 10001}, {"humidity": -5}, {"cover": "explode"}, {"color_mode": "hs"}, {"heating_setpoint": 99999}, {"level": 1.5}):
    refused({"type": "command", "id": 1, "number": 12, "state": state, "fabric": 1})
for change in ({"id": "1"}, {"number": 0}, {"number": 70000}, {"state": [1]}, {"state": None}, {"fabric": 300}, {"fabric": "1"}):
    refused({"type": "command", "id": 1, "number": 12, "state": {"on": True}, "fabric": 1, **change})
for bad_ops in ("ops", [["bridge", "set", "ctx", "key"]], [["fremd", "set", "ctx", "key", "1"]], [["bridge", "exec", "ctx", "key", "1"]],
                [["bridge", "set", "c" * 500, "key", "1"]], [["bridge", "set", "ctx", "", "1"]], [["bridge", "set", "ctx", "k" * 300, "1"]],
                [["bridge", "set", "ctx", "key", 5]], [["bridge", "set", "ctx", "key", "x" * (protocol.MAX_VALUE_LENGTH + 1)]],
                [["bridge", "delete", "ctx", None, None]], [["bridge", "set", 7, "key", "1"]], [[]] * (protocol.MAX_STORAGE_OPS + 1)):
    refused({"type": "storage", "ops": bad_ops})
for change in ({"manual": "1234"}, {"manual": 34970112332}, {"qr": "kein-qr-code"}, {"qr": "MT:" + "A" * 300}, {"rows": ["10", "01", "11"]},
               {"rows": ["12", "01"]}, {"rows": ["1" * 70] * 70}, {"rows": "101"}, {"commissioned": "nein"}):
    refused({**PAIRING, **change})
for fabric_list in ("x", [{"index": 0, "vendor": 1}], [{"index": 1, "vendor": -1}], [{"index": 1, "vendor": 1}] * 40, ["x"]):
    refused({"type": "fabrics", "fabrics": fabric_list})
for change in ({"node": "a b"}, {"online": 1}, {"endpoints": "x"}, {"endpoints": [{}] * 300},
               {"endpoints": [{"endpoint": -1, "types": [], "reachable": True, "state": {}}]},
               {"endpoints": [{"endpoint": 1, "types": ["x"], "reachable": True, "state": {}}]},
               {"endpoints": [{"endpoint": 1, "types": [1] * 20, "reachable": True, "state": {}}]},
               {"endpoints": [{"endpoint": 1, "types": [], "reachable": "ja", "state": {}}]},
               {"endpoints": [{"endpoint": 1, "types": [], "reachable": True, "state": {"level": 999}}]}):
    refused({**NODE, **change})
assert SECRET not in refused({"type": "result", "id": 1, "ok": False, "error": SECRET + " <b>"}), "error text repeats the input"
long_names = protocol.parse({**NODE, "name": "N" * 500, "vendor": ["kein", "text"], "product": "a\nb\tc"})
assert len(long_names.name) == protocol.MAX_TEXT and long_names.vendor == "" and long_names.product == "a b c", "texts are cut and flattened"
print("2 foreign input: wrong types, ranges, lengths and junk are refused; texts are cut; errors never repeat the input")

# ---------------------------------------------------------------------------
# 3) what the plugin sends
# ---------------------------------------------------------------------------
bridge = protocol.BridgeOptions(port=5540, passcode=20202021, discriminator=3840, name="ComfyOS", serial="abc", vendor_id=0xFFF1, product_id=0x8000)
controller = protocol.ControllerOptions(port=5541, serial="abc", vendor_id=0xFFF1, product_id=0x8000)
entry = protocol.EndpointEntry(number=10, kind="dimmable_light", name="Stehlampe", serial="comfyos-10", reachable=True, locked=False,
                               state={"on": True, "level": 127})
start = protocol.start_message(bridge, controller, [entry])
assert start["type"] == "start" and start["bridge"]["passcode"] == 20202021 and start["controller"]["port"] == 5541
assert start["endpoints"] == [{"number": 10, "kind": "dimmable_light", "name": "Stehlampe", "serial": "comfyos-10", "reachable": True,
                               "locked": False, "state": {"on": True, "level": 127}}]
json.dumps(start)                                                   # every message must be plain JSON
assert protocol.endpoints_message([entry])["endpoints"][0]["number"] == 10
assert protocol.update_message(10, {"on": False}, True, True) == {"type": "update", "number": 10, "state": {"on": False}, "reachable": True, "locked": True}
assert protocol.commissioning_message(True) == {"type": "commissioning", "open": True}
assert protocol.remove_fabric_message(2) == {"type": "remove_fabric", "index": 2}
assert protocol.reply_message(5, "locked") == {"type": "reply", "id": 5, "status": "locked"}
try:
    protocol.reply_message(5, "vielleicht")
    raise AssertionError("unknown reply status accepted")
except ValueError:
    pass
assert protocol.invoke_message(7, "peer2", 3, {"level": 50, "nonsense": 1}) == {"type": "invoke", "id": 7, "node": "peer2", "endpoint": 3, "state": {"level": 50}}
assert protocol.unpair_message(8, "peer2") == {"type": "unpair", "id": 8, "node": "peer2"}
assert protocol.clean_pairing_code(" 3497-011-2332 ") == "34970112332" and protocol.clean_pairing_code("3497 011 2332") == "34970112332"
assert protocol.clean_pairing_code("mt:y.k90afn00ka0648g00") == "MT:Y.K90AFN00KA0648G00"
assert protocol.pair_message(9, "3497-011-2332") == {"type": "pair", "id": 9, "code": "34970112332"}
for bad_code in ("", "1234", "3497-011-233x", "MT:", "MT:kurz", "MT:" + "A" * 400, "34970112332; rm -rf /", "<script>"):
    try:
        protocol.clean_pairing_code(bad_code)
        raise AssertionError(f"accepted pairing code: {bad_code[:20]}")
    except protocol.ProtocolError:
        pass
big = {"bridge": {"ctx": {f"key{number}": "v" * 60_000 for number in range(12)}}, "controller": {"nodes": {"a": "1"}}}
chunks = protocol.storage_messages(big)
assert len(chunks) > 3 and all(len(json.dumps(chunk)) < 400_000 for chunk in chunks), "large stores are split below the line limit"
merged: dict = {}
for chunk in chunks:
    for context, values in chunk["values"].items():
        merged.setdefault(chunk["namespace"], {}).setdefault(context, {}).update(values)
assert merged == big
print("3 messages to the helper: start, endpoints, update, reply, invoke; pairing codes are normalised or refused; storage is chunked")

# ---------------------------------------------------------------------------
# 4) storage
# ---------------------------------------------------------------------------
folder = Path(tempfile.mkdtemp(prefix="matter-storage-"))
key = storage.new_key()
store = storage.MatterStore(folder / "matter.bin", key)
store.load()
assert store.is_empty and not store.dirty and not store.save(), "a missing file is an empty store and nothing is written"
store.apply(ops.ops)
store.apply([protocol.StorageOp("bridge", "set", "fabrics", "list", '{"secret":"FABRIC-KEY-MATERIAL"}'),
             protocol.StorageOp("bridge", "set", "root.parts.aggregator", "x", "1"),
             protocol.StorageOp("bridge", "set", "root.parts.aggregator.parts.share-10", "y", "2"),
             protocol.StorageOp("bridge", "set", "rootless", "z", "3")])
assert store.dirty and store.save() and not store.dirty
raw = (folder / "matter.bin").read_bytes()
assert raw.startswith(storage.MAGIC) and b"FABRIC-KEY-MATERIAL" not in raw and b"fabrics" not in raw, "storage is not encrypted"
assert not (folder / "matter.bin.tmp").exists(), "temporary file left behind"
again = storage.MatterStore(folder / "matter.bin", key)
again.load()
assert again.stored["bridge"] == store.stored["bridge"] and again.stored["bridge"]["root.parts"] == {"__number__": "7"}
again.apply([protocol.StorageOp("bridge", "clear", "root.parts.aggregator", None, None)])       # removes the context and everything below
assert "root.parts.aggregator" not in again.stored["bridge"] and "root.parts.aggregator.parts.share-10" not in again.stored["bridge"]
assert "root.parts" in again.stored["bridge"] and "rootless" in again.stored["bridge"], "a clear must not touch similar names"
again.apply([protocol.StorageOp("bridge", "delete", "root.parts", "__number__", None), protocol.StorageOp("bridge", "delete", "nix", "nix", None)])
assert again.stored["bridge"]["root.parts"] == {}
again.forget("bridge")
assert "bridge" not in again.stored
for wrong in (storage.new_key(), "kein-schluessel", ""):
    try:
        storage.MatterStore(folder / "matter.bin", wrong).load()
        raise AssertionError("storage opened with a wrong key")
    except storage.StorageError:
        pass
(folder / "damaged.bin").write_bytes(raw[:-5] + b"XXXXX")
(folder / "short.bin").write_bytes(b"CMS1")
(folder / "other.bin").write_bytes(b"{}" * 40)
for name in ("damaged.bin", "short.bin", "other.bin"):
    try:
        storage.MatterStore(folder / name, key).load()
        raise AssertionError(f"damaged storage accepted: {name}")
    except storage.StorageError:
        pass
aside = storage.MatterStore(folder / "damaged.bin", key).set_aside()
assert aside is not None and aside.name == "damaged.bin.unreadable" and not (folder / "damaged.bin").exists()
first, second = storage.encrypt(store.stored, key), storage.encrypt(store.stored, key)
assert first != second and storage.decrypt(first, key) == storage.decrypt(second, key), "every write uses a fresh nonce"
limited = storage.MatterStore(folder / "limit.bin", key)
storage.MAX_STORED_BYTES = 1000
try:
    limited.apply([protocol.StorageOp("bridge", "set", "ctx", "big", "x" * 2000)])
    raise AssertionError("storage grew beyond its limit")
except storage.StorageError:
    pass
print("4 storage: AES-GCM on disk, wrong key and damaged files refused, atomic write, clear by prefix, size limit")

print("ALL MATTER PROTOCOL TESTS OK")
