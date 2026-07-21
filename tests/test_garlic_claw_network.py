import importlib.util
import io
import json
import pathlib
import types
import unittest
from contextlib import redirect_stdout


ROOT = pathlib.Path(__file__).resolve().parents[1]
GARLIC_MODULE = ROOT / "modules" / "04_garlic_claw.sh"
DEVICE_CONTROL = ROOT / "assets" / "ming-device-control.py"


def embedded_app(source):
    marker = "cat > /usr/local/bin/garlic-claw-app << 'GARLICAPP'\n"
    return source.split(marker, 1)[1].split("\nGARLICAPP", 1)[0]


def load_device_control():
    spec = importlib.util.spec_from_file_location(
        "ming_device_control_garlic_contract", DEVICE_CONTROL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_repair(status, repair_result=None, repair_returncode=0):
    source = GARLIC_MODULE.read_text(encoding="utf-8")
    prelude = embedded_app(source).split("import gi", 1)[0]
    namespace = {"__name__": "garlic_claw_network_contract"}
    exec(compile(prelude, str(GARLIC_MODULE), "exec"), namespace)

    calls = []
    responses = iter([
        types.SimpleNamespace(
            returncode=0, stdout=json.dumps(status), stderr=""),
        types.SimpleNamespace(
            returncode=repair_returncode,
            stdout=json.dumps(repair_result or {"ok": True}),
            stderr="repair failed" if repair_returncode else "",
        ),
    ])

    def run(command, **_kwargs):
        calls.append(command)
        return next(responses)

    output = io.StringIO()
    with redirect_stdout(output):
        returncode = namespace["repair_ethernet"](run=run)
    return returncode, json.loads(output.getvalue()), calls


class GarlicClawNetworkContracts(unittest.TestCase):
    def test_repair_uses_one_validated_ethernet_interface_and_json_contract(self):
        source = GARLIC_MODULE.read_text(encoding="utf-8")
        self.assertNotIn("nmcli networking off", source)
        self.assertNotIn("nmcli networking on", source)
        self.assertNotIn("systemctl restart NetworkManager", source)

        app = embedded_app(source)
        prelude = app.split("import gi", 1)[0]
        namespace = {"__name__": "garlic_claw_network_contract"}
        exec(compile(prelude, str(GARLIC_MODULE), "exec"), namespace)
        repair = namespace["repair_ethernet"]

        calls = []
        responses = iter([
            types.SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "ok": True,
                    "devices": [
                        {"device": "enp2s0", "state": "disconnected"},
                        {"device": "enp3s0", "state": "connected"},
                    ],
                }),
                stderr="",
            ),
            types.SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"ok": True, "state": "connected"}),
                stderr="",
            ),
        ])

        def run(command, **_kwargs):
            calls.append(command)
            return next(responses)

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, repair(run=run))
        self.assertEqual([
            ["/usr/local/bin/ming-device-control", "ethernet-status", "--json"],
            ["/usr/local/bin/ming-device-control", "ethernet-repair",
             "--ifname", "enp2s0", "--json"],
        ], calls)
        self.assertTrue(json.loads(output.getvalue())["ok"])

        for devices, reason_code in [
                ([], "interface_missing"),
                ([
                    {"device": "enp2s0", "state": "disconnected"},
                    {"device": "enp3s0", "state": "disconnected"},
                ], "ambiguous_interface"),
                ([{"device": "enp2s0; wifi", "state": "disconnected"}],
                 "invalid_interface")]:
            refusal_calls = []

            def refuse_run(command, **_kwargs):
                refusal_calls.append(command)
                return types.SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({"ok": True, "devices": devices}),
                    stderr="",
                )

            output = io.StringIO()
            with self.subTest(reason_code=reason_code), redirect_stdout(output):
                self.assertEqual(2, repair(run=refuse_run))
                self.assertEqual(reason_code, json.loads(output.getvalue())["reason_code"])
                self.assertEqual(1, len(refusal_calls))

        args = load_device_control().build_parser().parse_args([
            "ethernet-repair", "--ifname", "enp2s0", "--json"])
        self.assertEqual("ethernet-repair", args.action)
        self.assertEqual("enp2s0", args.ifname)
        self.assertTrue(args.json)

    def test_status_ok_false_fails_closed_before_repair(self):
        returncode, result, calls = run_repair({
            "ok": False,
            "reason_code": "diagnostic_unavailable",
            "reason_text": "NetworkManager 状态不可用。",
            "devices": [{"device": "enp2s0", "state": "disconnected"}],
        })

        self.assertNotEqual(0, returncode)
        self.assertEqual("status_not_ok", result["reason_code"])
        self.assertEqual(1, len(calls))

    def test_only_disconnected_state_is_repairable(self):
        with self.subTest(state="connected"):
            returncode, result, calls = run_repair({
                "ok": True,
                "devices": [{"device": "enp2s0", "state": "connected"}],
            })
            self.assertNotEqual(0, returncode)
            self.assertEqual("already_connected", result["reason_code"])
            self.assertEqual(1, len(calls))

        for state in ["unavailable", "unmanaged", "connecting", "mystery"]:
            with self.subTest(state=state):
                returncode, result, calls = run_repair({
                    "ok": True,
                    "devices": [{"device": "enp2s0", "state": state}],
                })
                self.assertNotEqual(0, returncode)
                self.assertEqual("interface_not_repairable", result["reason_code"])
                self.assertIn(state, result["error"])
                self.assertEqual(1, len(calls))

        with self.subTest(state="disconnected+unavailable"):
            returncode, result, calls = run_repair({
                "ok": True,
                "devices": [
                    {"device": "enp2s0", "state": "disconnected"},
                    {"device": "enp3s0", "state": "unavailable"},
                ],
            })
            self.assertNotEqual(0, returncode)
            self.assertEqual("interface_not_repairable", result["reason_code"])
            self.assertIn("unavailable", result["error"])
            self.assertEqual(1, len(calls))

    def test_repair_requires_exit_zero_and_json_ok_true(self):
        status = {
            "ok": True,
            "devices": [{"device": "enp2s0", "state": "disconnected"}],
        }
        cases = [
            (0, {"ok": False, "reason_code": "repair_failed"}, False),
            (2, {"ok": True, "state": "connected"}, False),
            (0, {"ok": True, "state": "connected"}, True),
        ]
        for process_returncode, repair_result, success in cases:
            with self.subTest(
                    process_returncode=process_returncode,
                    repair_ok=repair_result["ok"]):
                returncode, result, calls = run_repair(
                    status, repair_result, process_returncode)
                self.assertEqual(success, returncode == 0)
                self.assertEqual(repair_result["ok"], result["ok"])
                self.assertEqual(2, len(calls))


if __name__ == "__main__":
    unittest.main()
