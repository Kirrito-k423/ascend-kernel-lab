#!/usr/bin/env bash
set -eo pipefail
mkdir -p results/setup
cat "$ASCEND_HOME_PATH/aarch64-linux/ascend_toolkit_install.info" > results/setup/cann-install.txt
"$ASCEND_HOME_PATH/bin/bisheng" --version > results/setup/compiler.txt
timeout 180 cmake -S . -B build-a5 -DAKL_NPU_ARCH=dav-3510 -DCMAKE_BUILD_TYPE=Release
timeout 240 cmake --build build-a5 --target akl_datacopy -j2 > results/setup/build-copy.log 2>&1
python3 scripts/prepare_a5_mbench.py --clock-hz 1000000000 \
    --clock-source 'CANN GetSystemCycle: Ascend950PR/950DT SYS_CNT 1 GHz' --output results/setup
timeout 60 python3 scripts/run_datacopy.py --profile results/setup/profile.json --device 0 \
    --library build-a5/libakl_datacopy.so --cases results/setup/smoke-cases.json \
    --warmup 1 --samples 2 --output results/alignment-smoke
python3 scripts/plan_alignment.py --output results/alignment-plan
python3 scripts/plan_simt_arithmetic.py --output results/simt-plan
timeout 180 cmake -S examples/a5_mbench -B build-simt -DCMAKE_BUILD_TYPE=Release
timeout 240 cmake --build build-simt -j2 > results/setup/build-simt.log 2>&1
timeout 60 build-simt/akl_simt_arithmetic 0 results/simt-plan/smoke.csv 1 2 results/setup/simt-smoke.jsonl
