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


if __name__ == "__main__":
    unittest.main()
