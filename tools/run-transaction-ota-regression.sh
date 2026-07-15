#!/usr/bin/env bash
set -euo pipefail

readonly ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PYCACHE_DIR="${TMPDIR:-/tmp}/ming-transaction-ota-pycache"

cd "${ROOT_DIR}"
export PYTHONPYCACHEPREFIX="${PYCACHE_DIR}"

python3 -m unittest \
    tests.test_transaction_fault_matrix \
    tests.test_transaction_diagnostics \
    tests.test_transaction_regression_gate \
    tests.test_transaction_verify \
    tests.test_transaction_state \
    tests.test_transaction_rollback \
    tests.test_transaction_slot_apply \
    tests.test_transaction_boot \
    tests.test_transaction_health_bootstrap \
    tests.test_transaction_engine \
    tests.test_ota_target_guard \
    tests.test_ota_backup

python3 -m py_compile \
    assets/ming-transaction-*.py \
    assets/ming-ota-bootstrap-capability.py \
    tests/test_transaction_*.py

bash -n \
    assets/initramfs/ming-transaction-hook \
    assets/initramfs/ming-transaction-local-premount \
    assets/grub/40_ming_transaction \
    tools/run-transaction-ota-regression.sh

if command -v systemd-analyze >/dev/null 2>&1; then
    verify_root="$(mktemp -d)"
    trap 'rm -rf "${verify_root}"' EXIT
    install -Dm0644 assets/systemd/ming-transaction-health.service "${verify_root}/etc/systemd/system/ming-transaction-health.service"
    install -Dm0644 assets/systemd/ming-transaction-reconcile.service "${verify_root}/etc/systemd/system/ming-transaction-reconcile.service"
    install -Dm0755 /bin/true "${verify_root}/usr/local/sbin/ming-transaction-health"
    install -Dm0755 /bin/true "${verify_root}/bin/true"
    for unit in \
        sysinit.target \
        basic.target \
        shutdown.target \
        local-fs.target \
        multi-user.target \
        graphical.target \
        dbus.service \
        NetworkManager.service \
        systemd-logind.service \
        display-manager.service; do
        install -d "${verify_root}/etc/systemd/system"
        if [[ "${unit}" == *.service ]]; then
            printf '[Unit]\nDescription=Regression fixture %s\n\n[Service]\nType=oneshot\nExecStart=/bin/true\n' "${unit}" > "${verify_root}/etc/systemd/system/${unit}"
        else
            printf '[Unit]\nDescription=Regression fixture %s\n' "${unit}" > "${verify_root}/etc/systemd/system/${unit}"
        fi
    done
    systemd-analyze verify --root="${verify_root}" "${verify_root}/etc/systemd/system/ming-transaction-health.service" "${verify_root}/etc/systemd/system/ming-transaction-reconcile.service"
fi

if git rev-parse --show-toplevel >/dev/null 2>&1; then
    git diff --check -- \
        assets/ming-transaction-*.py \
        assets/ming-ota-bootstrap-capability.py \
        tests/test_transaction_*.py \
        tests/fixtures/transaction_fault_matrix.json \
        tools/run-transaction-ota-regression.sh \
        .github/workflows/transaction-ota-regression.yml
fi
